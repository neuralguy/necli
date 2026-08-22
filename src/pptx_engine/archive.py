"""PPTX ZIP package access and relationship resolution."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

from .models import SlideSize
from .xmlutil import attr_int, local, parse_xml, qn

REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_MAX_ARCHIVE_PARTS = 10_000
_MAX_PART_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


@dataclass(slots=True, frozen=True)
class Relationship:
    id: str
    type: str
    target: str
    target_mode: str | None = None


class PackageArchive:
    """In-memory PPTX archive that preserves every original part as bytes."""

    def __init__(self, entries: dict[str, bytes], original_hash: str):
        self.entries = entries
        self.original_hash = original_hash

    @classmethod
    def open(cls, data: bytes) -> PackageArchive:
        try:
            with zipfile.ZipFile(BytesIO(data), "r") as package:
                parts = [info for info in package.infolist() if not info.is_dir()]
                if len(parts) > _MAX_ARCHIVE_PARTS:
                    raise ValueError(
                        f"PPTX contains too many parts (>{_MAX_ARCHIVE_PARTS})"
                    )

                entries: dict[str, bytes] = {}
                total_size = 0
                for info in parts:
                    if info.file_size > _MAX_PART_UNCOMPRESSED_BYTES:
                        raise ValueError(
                            f"PPTX part '{info.filename}' exceeds the {_MAX_PART_UNCOMPRESSED_BYTES // (1024 * 1024)}MB limit"
                        )
                    total_size += info.file_size
                    if total_size > _MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                        raise ValueError(
                            f"PPTX exceeds the {_MAX_ARCHIVE_UNCOMPRESSED_BYTES // (1024 * 1024)}MB uncompressed limit"
                        )
                    entries[info.filename] = package.read(info)
        except zipfile.BadZipFile as exc:
            raise ValueError("Input is not a valid PPTX/ZIP package") from exc
        return cls(entries, sha256(data).hexdigest())

    @classmethod
    def from_file(cls, path: str) -> PackageArchive:
        with open(path, "rb") as source:
            return cls.open(source.read())

    def read_bytes(self, path: str) -> bytes | None:
        return self.entries.get(path)

    def read_text(self, path: str) -> str | None:
        data = self.read_bytes(path)
        if data is None:
            return None
        return data.decode("utf-8", errors="replace")

    def write_text(self, path: str, content: str) -> None:
        self.entries[path] = content.encode("utf-8")

    def read_rels(self, part_path: str) -> dict[str, Relationship]:
        xml = self.read_text(rels_path_for(part_path))
        if not xml:
            return {}
        try:
            root = parse_xml(xml)
        except ET.ParseError:
            return {}
        relationships: dict[str, Relationship] = {}
        for child in root:
            if local(child.tag) != "Relationship":
                continue
            relation = Relationship(
                child.get("Id", ""),
                child.get("Type", ""),
                child.get("Target", ""),
                child.get("TargetMode"),
            )
            relationships[relation.id] = relation
        return relationships

    def write_rels(
        self, part_path: str, relationships: dict[str, Relationship]
    ) -> None:
        root = ET.Element(f"{{{REL_NS}}}Relationships")
        for relation in relationships.values():
            attrs = {
                "Id": relation.id,
                "Type": relation.type,
                "Target": relation.target,
            }
            if relation.target_mode:
                attrs["TargetMode"] = relation.target_mode
            ET.SubElement(root, f"{{{REL_NS}}}Relationship", attrs)
        xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + ET.tostring(
            root, encoding="unicode"
        )
        self.write_text(rels_path_for(part_path), xml)

    def read_presentation(self) -> tuple[SlideSize, list[str]]:
        xml = self.read_text("ppt/presentation.xml")
        if not xml:
            raise ValueError("ppt/presentation.xml is missing")
        try:
            root = parse_xml(xml)
        except ET.ParseError as exc:
            raise ValueError("ppt/presentation.xml is malformed") from exc
        size_node = root.find(qn("p", "sldSz"))
        size = SlideSize(
            attr_int(size_node, "cx", 9_144_000), attr_int(size_node, "cy", 6_858_000)
        )
        relationships = self.read_rels("ppt/presentation.xml")
        slide_paths: list[str] = []
        listing = root.find(qn("p", "sldIdLst"))
        if listing is not None:
            for item in listing:
                relation_id = item.get(qn("r", "id")) or item.get("id")
                if relation_id in relationships:
                    slide_paths.append(
                        resolve_target(
                            "ppt/presentation.xml", relationships[relation_id].target
                        )
                    )
        return size, slide_paths

    def resolve_slide_chain(self, slide_path: str) -> dict[str, str | None]:
        layout_path = None
        master_path = None
        theme_path = None
        for relation in self.read_rels(slide_path).values():
            if relation.type.endswith("/slideLayout"):
                layout_path = resolve_target(slide_path, relation.target)
                break
        if layout_path:
            for relation in self.read_rels(layout_path).values():
                if relation.type.endswith("/slideMaster"):
                    master_path = resolve_target(layout_path, relation.target)
                    break
        if master_path:
            for relation in self.read_rels(master_path).values():
                if relation.type.endswith("/theme"):
                    theme_path = resolve_target(master_path, relation.target)
                    break
        return {
            "layout_path": layout_path,
            "master_path": master_path,
            "theme_path": theme_path,
        }

    def to_bytes(self) -> bytes:
        buffer = BytesIO()
        # XML receives DEFLATE; opaque binary parts are stored verbatim, matching
        # the source engine's performance-oriented policy.
        with zipfile.ZipFile(buffer, "w") as output:
            for path, data in self.entries.items():
                extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
                info = zipfile.ZipInfo(path)
                info.compress_type = (
                    zipfile.ZIP_DEFLATED
                    if extension in {"xml", "rels"}
                    else zipfile.ZIP_STORED
                )
                output.writestr(info, data)
        return buffer.getvalue()


def rels_path_for(part_path: str) -> str:
    path = PurePosixPath(part_path)
    return str(path.parent / "_rels" / f"{path.name}.rels")


def resolve_target(base_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts: list[str] = list(PurePosixPath(base_part).parent.parts)
    for piece in target.split("/"):
        if piece in {"", "."}:
            continue
        if piece == "..":
            if parts:
                parts.pop()
        else:
            parts.append(piece)
    return "/".join(parts)


def relative_target(base_part: str, target_part: str) -> str:
    """Return a relative OOXML relationship target."""
    base = list(PurePosixPath(base_part).parent.parts)
    target = list(PurePosixPath(target_part).parts)
    while base and target and base[0] == target[0]:
        base.pop(0)
        target.pop(0)
    return "/".join([".."] * len(base) + target)
