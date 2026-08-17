"""Small atomic persistence helpers for necli state files."""

from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Replace *path* atomically after a complete temporary-file write.

    The temporary file lives next to the destination, so ``os.replace`` stays on
    the same filesystem. Failed writes never expose a partially-written target
    and the temporary file is removed best-effort.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_mode: int | None = None
    with contextlib.suppress(OSError):
        existing_mode = stat.S_IMODE(target.stat().st_mode)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        if existing_mode is not None:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, existing_mode)
            else:
                os.chmod(tmp_name, existing_mode)
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            # Ownership of fd has moved to the file object. Mark it invalid so
            # the exception path does not attempt to close it twice.
            fd = -1
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def atomic_write_json(path: Path | str, data: Any, *, indent: int = 2) -> None:
    atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=indent),
    )
