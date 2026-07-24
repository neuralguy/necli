"""docx_screenshot — рендер страницы .docx (или .pdf) в PNG для отправки модели.

Pipeline:
  1. .docx → .pdf через LibreOffice headless (soffice --convert-to pdf).
     .pdf используется напрямую без конвертации.
  2. .pdf → PNG нужной страницы через PyMuPDF (рендер при заданном DPI).
  3. Возвращаем ToolResult с image_path — агентный loop прикрепит картинку
     к следующему сообщению модели (multimodal).

Так модель может «увидеть» как реально выглядит свёрстанная страница:
шрифты, отступы, таблицы, формулы, разрывы — то, что не видно из HTML.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from config.paths import BASE_DIR
from logger import logger
from tools._paths import clean_path, resolve_path
from tools.models import ToolCall, ToolResult

_DPI = 200

_TMP_DIRS: list[Path] = []

def _import_pymupdf():
    """Возвращает модуль PyMuPDF (pymupdf или его старый алиас fitz) либо None."""
    try:
        import pymupdf
        return pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
            return pymupdf
        except ImportError:
            return None


def _cleanup_tmp_dirs() -> None:
    while _TMP_DIRS:
        d = _TMP_DIRS.pop()
        shutil.rmtree(d, ignore_errors=True)


atexit.register(_cleanup_tmp_dirs)

def _find_soffice() -> str | None:
    names = ("soffice", "libreoffice")
    if sys.platform == "win32":
        names = ("soffice.exe", "libreoffice.exe", *names)
    for name in names:
        found = shutil.which(name)
        if found:
            return found

    candidates = [
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        "/opt/libreoffice/program/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    if sys.platform == "win32":
        candidates.extend(
            str(Path(base) / "LibreOffice" / "program" / "soffice.exe")
            for base in (
                os.environ.get("ProgramFiles", r"C:\Program Files"),  # noqa: SIM112
                os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),  # noqa: SIM112
            )
        )
    for cand in candidates:
        if Path(cand).exists():
            return cand
    return None

def _docx_to_pdf(docx_path: Path, op_id: str) -> Path | None:
    """Конвертирует docx → pdf через LibreOffice headless. Возвращает путь к pdf."""
    soffice = _find_soffice()
    if not soffice:
        logger.warning("docx_screenshot[{}]: soffice not found", op_id)
        return None

    out_dir = Path(tempfile.mkdtemp(prefix="necli_docx_pdf_"))
    profile_dir = Path(tempfile.mkdtemp(prefix="necli_lo_profile_"))
    # Зарегистрировать на cleanup при выходе процесса в любом случае.
    _TMP_DIRS.extend((out_dir, profile_dir))
    try:
        # Приватный UserInstallation профиль изолирует от уже запущенного
        # GUI-инстанса LibreOffice (иначе --convert-to цепляется к нему
        # и молча возвращает пустой/устаревший результат).
        proc = subprocess.run(
            [
                soffice, "--headless", "--norestore", "--invisible",
                "--nodefault", "--nofirststartwizard", "--nologo",
                f"-env:UserInstallation={profile_dir.as_uri()}",
                "--convert-to", "pdf:writer_pdf_Export",
                "--outdir", str(out_dir), str(docx_path),
            ],
            capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        logger.error("docx_screenshot[{}]: soffice timeout", op_id)
        shutil.rmtree(profile_dir, ignore_errors=True)
        return None
    except Exception as e:
        logger.opt(exception=True).error("docx_screenshot[{}]: soffice spawn failed: {}", op_id, e)
        shutil.rmtree(profile_dir, ignore_errors=True)
        return None

    # Профиль больше не нужен — удаляем сразу, не копим в /tmp.
    shutil.rmtree(profile_dir, ignore_errors=True)

    if proc.returncode != 0:
        logger.warning(
            "docx_screenshot[{}]: soffice exit={} stderr={!r}",
            op_id, proc.returncode, (proc.stderr or "")[:300],
        )
        return None

    pdf_path = out_dir / (docx_path.stem + ".pdf")
    if not pdf_path.exists():
        candidates = list(out_dir.glob("*.pdf"))
        if not candidates:
            logger.warning("docx_screenshot[{}]: pdf not produced", op_id)
            return None
        pdf_path = candidates[0]
    return pdf_path

def _pdf_pages_to_png(pdf_path: Path, page_nums: list[int], op_id: str, dpi: int = _DPI) -> tuple[list[tuple[int, Path]], int]:
    """Рендерит указанные страницы pdf (1-based) в png.

    Возвращает (list_of_(page_num, png_path), total_pages).
    """
    pymupdf = _import_pymupdf()
    if pymupdf is None:
        logger.warning("docx_screenshot[{}]: pymupdf not installed", op_id)
        return [], 0

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as e:
        logger.opt(exception=True).error("docx_screenshot[{}]: pdf open failed: {}", op_id, e)
        return [], 0

    total = doc.page_count
    if total == 0:
        doc.close()
        return [], 0

    # Нормализуем, обрезаем по границам, убираем дубли, сохраняем порядок.
    seen: set[int] = set()
    norm: list[int] = []
    for n in page_nums:
        n = max(1, min(n, total))
        if n not in seen:
            seen.add(n)
            norm.append(n)
    if not norm:
        norm = [1]

    out_dir = BASE_DIR / "docx_shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[tuple[int, Path]] = []
    zoom = dpi / 72.0
    for n in norm:
        idx = n - 1
        try:
            page = doc.load_page(idx)
            mat = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png_path = out_dir / f"shot_{op_id}_p{n}.png"
            pix.save(str(png_path))
            rendered.append((n, png_path))
        except Exception as e:
            logger.opt(exception=True).error("docx_screenshot[{}]: render page {} failed: {}", op_id, n, e)
    doc.close()
    return rendered, total


def _parse_pages_arg(args: dict, op_id: str) -> list[int]:
    """Парсит page/pages из args в список 1-based номеров страниц.

    Поддержка:
      page=3                  → [3]
      pages="2-5"             → [2,3,4,5]
      pages="1,3,7"           → [1,3,7]
      pages="2-4,8,10-11"     → [2,3,4,8,10,11]
      pages=[1,4,9]           → [1,4,9]
      pages="all"             → [] (сигнал: все страницы, заполнится позже)
    """
    pages = args.get("pages")

    if isinstance(pages, str) and pages.strip().lower() == "all":
        return []  # пустой = все, развернётся после открытия pdf

    nums: list[int] = []

    def _add_token(tok: str) -> None:
        tok = tok.strip()
        if not tok:
            return
        if "-" in tok:
            lo_s, _, hi_s = tok.partition("-")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                return
            if lo > hi:
                lo, hi = hi, lo
            nums.extend(range(lo, hi + 1))
        else:
            try:
                nums.append(int(tok))
            except ValueError:
                return

    if isinstance(pages, (list, tuple)):
        for p in pages:
            try:
                nums.append(int(p))
            except (TypeError, ValueError):  # noqa: PERF203
                continue
    elif isinstance(pages, str):
        for tok in pages.split(","):
            _add_token(tok)
    elif isinstance(pages, int):
        nums.append(pages)

    if not nums:
        nums = [1]

    return nums

def docx_screenshot(call: ToolCall) -> ToolResult:
    """Рендерит одну или несколько страниц .docx/.pdf в PNG и отдаёт модели.

    args:
      path (str, required) — путь к .docx или .pdf
      pages (str, optional) — диапазон/набор страниц:
          "2-5", "1,3,7", "2-4,8,10-11", [1,4,9] или "all" (все страницы).
          По умолчанию — страница 1.
    """
    op_id = uuid.uuid4().hex[:8]
    args = call.args or {}

    path_str = clean_path(args.get("path", ""))
    if not path_str:
        return ToolResult(
            name="docx_screenshot", status="error",
            output="File path (path) not specified.",
            exit_code=1, command=call.command,
        )

    pages_arg = args.get("pages")
    want_all = isinstance(pages_arg, str) and pages_arg.strip().lower() == "all"
    page_nums = _parse_pages_arg(args, op_id)

    dpi = _DPI

    src = resolve_path(path_str, extensions=(".docx", ".pdf"))
    if not src.exists():
        return ToolResult(
            name="docx_screenshot", status="error",
            output=f"File not found: {path_str}",
            exit_code=1, command=call.command,
        )

    suffix = src.suffix.lower()
    if suffix not in (".docx", ".pdf"):
        return ToolResult(
            name="docx_screenshot", status="error",
            output=f"Unsupported format: {suffix}. Expected .docx or .pdf.",
            exit_code=1, command=call.command,
        )

    logger.info(
        "docx_screenshot[{}]: {} pages={}", op_id, path_str,
        "all" if want_all else page_nums,
    )

    if suffix == ".pdf":
        pdf_path = src
    else:
        pdf_path = _docx_to_pdf(src, op_id)
        if pdf_path is None:
            return ToolResult(
                name="docx_screenshot", status="error",
                output=(
                    "Failed to convert docx → pdf. LibreOffice (soffice/libreoffice) "
                    "must be installed and available. Install: apt install libreoffice, "
                    "brew install --cask libreoffice, or winget install TheDocumentFoundation.LibreOffice."
                ),
                exit_code=1, command=call.command,
            )

    # "all" — узнаём число страниц и разворачиваем в полный диапазон.
    if want_all:
        pymupdf = _import_pymupdf()
        if pymupdf is not None:
            try:
                _d = pymupdf.open(str(pdf_path))
                page_nums = list(range(1, _d.page_count + 1))
                _d.close()
            except Exception:
                logger.opt(exception=True).warning("docx_screenshot[{}]: page count failed", op_id)

    rendered, total = _pdf_pages_to_png(pdf_path, page_nums, op_id, dpi=dpi)

    # PDF больше не нужен в docx-случае — PNG уже сохранёны в .data/docx_shots.
    if suffix == ".docx":
        shutil.rmtree(pdf_path.parent, ignore_errors=True)

    if not rendered:
        return ToolResult(
            name="docx_screenshot", status="error",
            output=(
                "Failed to render page(s) to PNG. PyMuPDF required: uv add pymupdf."
                if total == 0 else "Page render failed (see logs)."
            ),
            exit_code=1, command=call.command,
        )

    shown_pages = [n for n, _ in rendered]
    paths = [p for _, p in rendered]
    logger.info(
        "docx_screenshot[{}]: ok → {} image(s) (pages {} of {})",
        op_id, len(paths), shown_pages, total,
    )

    if len(paths) == 1:
        n = shown_pages[0]
        return ToolResult(
            name="docx_screenshot", status="ok",
            output=(
                f"[Rendered {path_str} page {n} of {total} → image attached. "
                f"This is how the page actually looks when laid out.]"
            ),
            exit_code=0, command=call.command,
            image_path=paths[0],
        )

    pages_str = ", ".join(str(n) for n in shown_pages)
    return ToolResult(
        name="docx_screenshot", status="ok",
        output=(
            f"[Rendered {path_str} pages {pages_str} of {total} → {len(paths)} "
            f"images attached in order. This is how the pages actually look when laid out.]"
        ),
        exit_code=0, command=call.command,
        image_paths=paths,
    )

__all__ = ["docx_screenshot"]
