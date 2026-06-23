"""File format readers: CSV, Excel, DOCX, images."""

import csv
import re
from pathlib import Path

from logger import logger
from tools.file_ops._pandoc import find_pandoc as _find_pandoc



_IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".bmp", ".tiff", ".tif", ".ico", ".svg",
}

_DOCX_EXTENSIONS = {".docx"}
_CSV_EXTENSIONS = {".csv", ".tsv"}
_EXCEL_EXTENSIONS = {".xlsx", ".xls", ".xlsm", ".xlsb"}
_PDF_EXTENSIONS = {".pdf"}

# Лимиты усечения табличных данных (CSV/TSV/Excel).
# При total > _TABLE_TRUNCATE_THRESHOLD печатаем header + первые
# _TABLE_HEAD_ROWS строк, затем '...', затем последние _TABLE_TAIL_ROWS.
_TABLE_HEAD_ROWS = 100
_TABLE_TAIL_ROWS = 50
_TABLE_TRUNCATE_THRESHOLD = 200
# Сколько строк реально показываем при усечении (header + head + tail).
_TABLE_SHOWN_ROWS = _TABLE_HEAD_ROWS + _TABLE_TAIL_ROWS + 1
# Жёсткий потолок чтения с диска — не материализуем больше, чем нужно,
# чтобы не словить OOM на огромных файлах. Берём чуть больше порога, чтобы
# отличить ровно-пороговый файл от усечённого.
_TABLE_READ_CAP = _TABLE_TRUNCATE_THRESHOLD + 1





def _rows_to_markdown(rows: list[list[str]], total: int) -> str:
    """Канонический рендер табличных данных в markdown.

    Принимает все строки и общее число строк. Для total > 200 печатает
    header + 100 строк данных + ... + последние 50. Иначе все.
    Используется и для CSV/TSV, и для каждого листа Excel.
    """
    if not rows:
        return "(empty)"

    truncated = total > _TABLE_TRUNCATE_THRESHOLD
    display_rows: list = (
        rows[: _TABLE_HEAD_ROWS + 1] + [None] + rows[-_TABLE_TAIL_ROWS:]
        if truncated
        else list(rows)
    )

    md_lines: list[str] = []
    for idx, row in enumerate(display_rows):
        if row is None:
            md_lines.append(f"| ... ({total - _TABLE_SHOWN_ROWS} rows skipped) ... |")
            continue
        md_lines.append("| " + " | ".join(cell.strip() for cell in row) + " |")
        if idx == 0:
            md_lines.append("| " + " | ".join("---" for _ in row) + " |")

    result = "\n".join(md_lines)
    if truncated:
        result += f"\n\n(Showing {_TABLE_SHOWN_ROWS} of {total} rows)"
    return result


def _collect_limited_rows(row_iter) -> tuple[list[list[str]], int]:
    """Стримит строки, не материализуя весь файл (защита от OOM на больших таблицах).

    Держит в памяти максимум head (_TABLE_HEAD_ROWS+1) + tail (_TABLE_TAIL_ROWS)
    строк. Если общее число строк <= порога усечения — возвращает все; иначе
    head + tail, чего достаточно для _rows_to_markdown. Второй элемент кортежа —
    точное общее число строк.

    Каждая ячейка приводится к str (Excel отдаёт значения разных типов).
    """
    from collections import deque

    head: list[list[str]] = []
    tail: deque[list[str]] = deque(maxlen=_TABLE_TAIL_ROWS)
    total = 0
    for raw in row_iter:
        total += 1
        row = [str(cell) if cell is not None else "" for cell in raw]
        if len(head) < _TABLE_READ_CAP:
            head.append(row)
        else:
            tail.append(row)

    if total <= _TABLE_TRUNCATE_THRESHOLD:
        return head, total
    # head хранит _TABLE_READ_CAP строк; _rows_to_markdown сам нарежет head+tail,
    # поэтому отдаём первые (_TABLE_HEAD_ROWS+1) + последние _TABLE_TAIL_ROWS.
    return head[: _TABLE_HEAD_ROWS + 1] + list(tail), total

def _read_csv(path: Path, encoding: str = "utf-8") -> str:
    """Reads a CSV/TSV file and returns content as a markdown table.

    For large files (>200 rows), shows first 100 and last 50 rows.
    """

    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","

    try:
        with open(path, "r", encoding=encoding, errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            rows, total = _collect_limited_rows(reader)
    except Exception as e:
        return f"[Error reading CSV: {e}]"

    if not rows:
        return "(empty file)"

    return _rows_to_markdown(rows, total=total)




def _read_excel(path: Path) -> str:
    """Reads an Excel file and returns all sheets as markdown tables."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return (
            "[Error: openpyxl not installed. "
            "Run: pip install openpyxl]"
        )

    try:
        wb = load_workbook(str(path), read_only=True, data_only=True)
    except Exception as e:
        return f"[Error opening Excel file: {e}]"

    parts: list[str] = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows, total = _collect_limited_rows(ws.iter_rows(values_only=True))

        if not rows:
            parts.append(f"## Sheet: {sheet_name}\n\n(empty sheet)")
            continue

        sheet_md = f"## Sheet: {sheet_name}\n\n" + _rows_to_markdown(rows, total=total)
        parts.append(sheet_md)

    wb.close()
    return "\n\n".join(parts)




def _read_docx(path: Path) -> str:
    """Reads a .docx file and returns HTML (via pandoc) or markdown fallback.

    HTML формат соответствует тому, что принимает create_docx — это позволяет
    агенту читать → править → перезаписывать документ без потери структуры.
    Формулы конвертируются в LaTeX-вид ($...$, $$...$$).

    Если документ был создан через create_docx и сохранён HTML-исходник —
    отдаём его (точный round-trip без потерь pandoc-конвертации).
    """
    try:
        from tools.file_ops._docx_sources import load_source
        saved = load_source(path)
        if saved is not None:
            return (
                "[DOCX as HTML · source restored exactly. To EDIT: patch_file this HTML "
                "in place (small find/replace), then create_docx with the full HTML to "
                "regenerate. Do NOT rewrite the whole document.]\n"
                + saved
            )
    except Exception:
        logger.debug("docx source restore skipped", exc_info=True)

    html = _read_docx_via_pandoc(path)
    if html is not None:
        return html
    return _read_docx_via_python_docx(path)


def _read_docx_via_pandoc(path: Path) -> str | None:
    """DOCX → HTML через pandoc + восстановление LaTeX-формул из markdown-прохода.

    Pandoc HTML writer не возвращает inline-формулы как `$...$` — он отдаёт
    разрисованный `<span class="math inline"><em>a</em>…</span>`. Делаем
    второй проход в markdown, где формулы корректно сохраняются как
    `$...$` / `$$...$$`, и подменяем pandoc-плейсхолдеры в HTML на чистый
    LaTeX. Так round-trip read→edit→write остаётся идеальным.

    Возвращает None, если pandoc недоступен.
    """
    import hashlib
    import subprocess
    import tempfile

    pandoc = _find_pandoc()
    if not pandoc:
        return None

    media_key = hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:12]
    media_dir = Path(tempfile.gettempdir()) / f"necli_docx_media_{media_key}"
    # Каталог НАМЕРЕННО переживает вызов: _rewrite_media_paths переписывает
    # src картинок на абсолютные пути в нём, чтобы последующий create_docx нашёл
    # их на диске. Но перед извлечением чистим его, иначе при изменении того же
    # файла осталась бы устаревшая media от прошлой версии.
    if media_dir.exists():
        import shutil as _shutil
        try:
            _shutil.rmtree(media_dir)
        except OSError:
            logger.debug("docx media dir cleanup failed: %s", media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)

    try:
        html_proc = subprocess.run(
            [
                pandoc, "-f", "docx", "-t", "html", "--wrap=none",
                f"--extract-media={media_dir}", str(path),
            ],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if html_proc.returncode != 0:
        return None
    html = (html_proc.stdout or "").strip()
    if not html:
        return None

    html = _rewrite_media_paths(html, media_dir)

    try:
        md_proc = subprocess.run(
            [pandoc, "-f", "docx", "-t", "markdown", "--wrap=none", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        md_text = md_proc.stdout if md_proc.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        md_text = ""

    if md_text:
        html = _restore_math_from_markdown(html, md_text)

    html = _enrich_html_with_docx_styles(html, path)

    # Восстанавливаем точный whitespace (ведущие/хвостовые/внутренние пробелы и
    # табы), который pandoc обрезал при docx→html. Без этого теряется ручная
    # разметка пробелами (центрирование) и отступы кода — ломается round-trip.
    try:
        from docx import Document as _WsDocument
        from tools.file_ops._docx_whitespace import restore_into_html as _ws_restore_html
        _ws_doc = _WsDocument(str(path))
        html = _ws_restore_html(html, _ws_doc)
    except Exception:
        logger.debug("docx whitespace restore (read) skipped", exc_info=True)

    # Стэшим оригинал как шаблон: при последующей записи docx с тем же текстом
    # клонируем его и патчим только текст — так сохраняются секции, стили,
    # колонтитулы, нумерация, картинки, ширины таблиц (pandoc их теряет) и
    # вёрстка (число страниц) остаётся идентичной.
    try:
        from tools.file_ops._docx_sources import save_template as _ws_save_template
        _ws_save_template(path)
    except Exception:
        logger.debug("docx template stash (read) skipped", exc_info=True)

    return (
        "[DOCX as HTML. To EDIT: patch_file this HTML in place (small find/replace), "
        "then create_docx with the full HTML to regenerate. Do NOT rewrite the whole "
        "document.]\n" + html
    )


def _rewrite_media_paths(html: str, media_dir: Path) -> str:
    """Pandoc с --extract-media кладёт картинки в <media_dir>/media/* и пишет
    в HTML относительные src="media/имя.png". Подменяем их на абсолютные пути,
    чтобы при повторном create_docx картинки находились на диске.
    """
    import re

    def repl(match: "re.Match[str]") -> str:
        before, src, after = match.group(1), match.group(2), match.group(3)
        if src.startswith(("http://", "https://", "data:", "/")):
            return match.group(0)
        abs_path = (media_dir / src).resolve()
        if not abs_path.exists():
            return match.group(0)
        return f'{before}src="{abs_path}"{after}'

    return re.sub(r'(<img\b[^>]*?\s)src="([^"]+)"([^>]*>)', repl, html)


def _effective_alignment(para):
    """Эффективное выравнивание абзаца: прямое (pPr/jc) либо унаследованное
    из именованного стиля по цепочке base_style. python-docx pf.alignment
    возвращает только ПРЯМОЕ значение, поэтому выравнивание, заданное стилем
    (напр. 'Body Text' с jc=center), теряется — обходим цепочку стилей вручную.
    """
    pf = para.paragraph_format
    if pf is not None and pf.alignment is not None:
        return pf.alignment

    style = para.style
    seen = set()
    while style is not None and id(style) not in seen:
        seen.add(id(style))
        spf = getattr(style, "paragraph_format", None)
        if spf is not None and spf.alignment is not None:
            return spf.alignment
        style = getattr(style, "base_style", None)
    return None


def _para_style_attrs(para) -> dict:
    """Достаёт inline-style атрибуты, которые pandoc теряет:
    text-align, font-size первого run, color первого run, font-family.
    """
    style: dict = {}

    from docx.oxml.ns import qn

    try:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        WD_ALIGN_PARAGRAPH = None

    if WD_ALIGN_PARAGRAPH is not None:
        align = _effective_alignment(para)
        align_name = {
            WD_ALIGN_PARAGRAPH.CENTER: "center",
            WD_ALIGN_PARAGRAPH.RIGHT: "right",
            WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
        }.get(align)
        if align_name:
            style["text-align"] = align_name

    runs = [r for r in para.runs if r.text or r._element.findall(qn("w:br"))]
    if runs:
        first = runs[0]
        font = first.font
        if font.size is not None:
            try:
                pt = font.size.pt
                if pt and pt != 14:  # 14pt — наш default, не пишем
                    style["font-size"] = f"{int(pt) if pt == int(pt) else pt}pt"
            except Exception:
                logger.debug("docx font.size read failed", exc_info=True)
        if font.name and font.name != "Times New Roman":
            style["font-family"] = font.name
        if font.color is not None and font.color.rgb is not None:
            try:
                rgb = str(font.color.rgb).lower()
                if rgb and rgb != "000000":
                    style["color"] = f"#{rgb}"
            except Exception:
                logger.debug("docx font.color read failed", exc_info=True)

    return style


def _para_is_empty(para) -> bool:
    """Параграф пустой (только пробелы/переводы строк)?"""
    return not (para.text or "").strip()


_BLOCK_OPEN_RE = re.compile(
    r"<(p|h[1-6])(\s[^>]*)?>",
    flags=re.IGNORECASE,
)


def _merge_style_attr(existing_attrs: str, extra_style: str) -> str:
    """Сливает style="..." в существующих атрибутах с дополнительным style."""
    if not extra_style:
        return existing_attrs
    m = re.search(r'style\s*=\s*"([^"]*)"', existing_attrs or "", flags=re.IGNORECASE)
    if m:
        merged = m.group(1).rstrip("; ") + "; " + extra_style
        return existing_attrs[: m.start()] + f'style="{merged}"' + existing_attrs[m.end() :]
    sep = " " if existing_attrs and not existing_attrs.startswith(" ") else ""
    return f'{existing_attrs}{sep} style="{extra_style}"'


def _enrich_html_with_docx_styles(html: str, path: Path) -> str:
    """Дочитывает docx через python-docx и добавляет в HTML inline-стили,
    которые pandoc теряет: text-align, font-size, color, font-family.

    Также вставляет пустые параграфы <p><br/></p> там, где они были в docx
    (pandoc их сжимает).
    """
    try:
        from docx import Document
    except ImportError:
        return html

    try:
        doc = Document(str(path))
    except Exception:
        return html

    # Собираем все параграфы в порядке обхода body (включая параграфы из ячеек таблиц).
    # ВАЖНО: pandoc для каждой ячейки таблицы тоже создаёт параграф, поэтому
    # порядок должен совпадать с обходом docx-body-level paragraphs + table-cell paragraphs.
    body_paras: list = []

    # Индекс id(element) → (kind, Paragraph). Без него поиск был O(n²) на больших docx.
    para_by_elem: dict[int, tuple[str, object]] = {}
    for p in doc.paragraphs:
        para_by_elem[id(p._element)] = ("p", p)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for cp in cell.paragraphs:
                    para_by_elem[id(cp._element)] = ("cell_p", cp)

    def _collect_paras(parent_element):
        for child in parent_element.iterchildren():
            tag = child.tag.split("}")[-1]
            if tag == "p":
                entry = para_by_elem.get(id(child))
                if entry is not None:
                    body_paras.append(entry)
            # tbl: параграфы внутри таблицы pandoc отдаёт как td-содержимое,
            # стили на них не сопоставляем.

    _collect_paras(doc.element.body)

    # Фильтруем только body-level параграфы (не из таблиц) — их сопоставляем с p/h* в HTML
    top_paras = [p for kind, p in body_paras if kind == "p"]

    # Восстановим пустые параграфы: где в docx был пустой p, а в html его нет —
    # это сложно сделать точечно. Применим простую эвристику: подсчитаем сколько
    # подряд идущих пустых параграфов было в исходнике между i-м и (i+1)-м
    # непустым, и вставим столько же <p><br/></p> после i-го блока.
    para_runs: list = []  # список: (style_dict, empty_count_before)
    empty_buffer = 0
    for p in top_paras:
        if _para_is_empty(p):
            empty_buffer += 1
            continue
        para_runs.append((_para_style_attrs(p), empty_buffer))
        empty_buffer = 0

    # Теперь идём по HTML, находим открывающие p/h* и доклеиваем стили + вставляем пустые
    idx = 0
    result_parts: list[str] = []
    last_end = 0

    for m in _BLOCK_OPEN_RE.finditer(html):
        if idx >= len(para_runs):
            break
        style_dict, empty_before = para_runs[idx]

        result_parts.append(html[last_end : m.start()])

        # Вставляем пустые параграфы ПЕРЕД текущим блоком
        if empty_before:
            result_parts.append("<p><br/></p>\n" * empty_before)

        tag = m.group(1)
        attrs = m.group(2) or ""

        if style_dict:
            extra = "; ".join(f"{k}:{v}" for k, v in style_dict.items())
            attrs = _merge_style_attr(attrs, extra)

        result_parts.append(f"<{tag}{attrs}>")
        last_end = m.end()
        idx += 1

    result_parts.append(html[last_end:])
    return "".join(result_parts)


_MATH_DISPLAY_MD_RE = re.compile(r"\$\$(.+?)\$\$", flags=re.DOTALL)
_MATH_INLINE_MD_RE = re.compile(r"(?<!\$)\$([^\$\n]+?)\$(?!\$)")
_MATH_DISPLAY_HTML_RE = re.compile(
    r'<span class="math display">.*?</span>', flags=re.DOTALL,
)
_MATH_INLINE_HTML_RE = re.compile(
    r'<span class="math inline">.*?</span>', flags=re.DOTALL,
)


def _restore_math_from_markdown(html: str, md: str) -> str:
    """Подменяет pandoc-плейсхолдеры формул в HTML на LaTeX из markdown-прохода.

    Pandoc обрабатывает формулы одинаковым порядком, так что i-я формула в
    HTML соответствует i-й в markdown. Сначала display ($$...$$), затем
    inline ($...$), чтобы случайно не съесть инлайн внутри блочной.
    """
    display_md = _MATH_DISPLAY_MD_RE.findall(md)
    md_no_display = _MATH_DISPLAY_MD_RE.sub("\x00", md)
    inline_md = _MATH_INLINE_MD_RE.findall(md_no_display)

    di = iter(display_md)

    def _sub_display(_m):
        try:
            tex = next(di).strip()
        except StopIteration:
            return _m.group(0)
        return f"$${tex}$$"

    html2 = _MATH_DISPLAY_HTML_RE.sub(_sub_display, html)

    ii = iter(inline_md)

    def _sub_inline(_m):
        try:
            tex = next(ii).strip()
        except StopIteration:
            return _m.group(0)
        return f"${tex}$"

    return _MATH_INLINE_HTML_RE.sub(_sub_inline, html2)


def _read_docx_via_python_docx(path: Path) -> str:
    """Fallback: markdown-подобный текст через python-docx (без pandoc)."""
    try:
        from docx import Document
    except ImportError:
        return (
            "[Error: python-docx not installed and pandoc not in PATH. "
            "Run: pip install python-docx OR apt install pandoc]"
        )

    doc = Document(str(path))
    parts: list[str] = ["[DOCX as markdown — pandoc not available, formulas may be lost]"]

    # Индекс id(element) → объект, чтобы сопоставление было O(1), а не O(n²)
    # (раньше для каждого body-элемента линейно искали по doc.paragraphs/tables).
    para_by_elem = {id(p._element): p for p in doc.paragraphs}
    table_by_elem = {id(t._element): t for t in doc.tables}

    for element in doc.element.body:
        tag = element.tag.split("}")[-1]  # strip namespace

        if tag == "p":
            para = para_by_elem.get(id(element))
            if para is not None:
                text = _format_paragraph(para)
                if text is not None:
                    parts.append(text)

        elif tag == "tbl":
            table = table_by_elem.get(id(element))
            if table is not None:
                parts.append(_format_table(table))

    return "\n\n".join(parts)




def _format_paragraph(para) -> str | None:
    """Formats a single paragraph with heading level and inline styles."""
    style_name = (para.style.name or "").lower()

    # Build text with inline formatting
    text_parts: list[str] = []
    for run in para.runs:
        t = run.text
        if not t:
            continue
        if run.bold and run.italic:
            t = f"***{t}***"
        elif run.bold:
            t = f"**{t}**"
        elif run.italic:
            t = f"*{t}*"
        text_parts.append(t)

    text = "".join(text_parts).strip()
    if not text:
        return None

    # Heading levels
    if style_name.startswith("heading"):
        try:
            level = int(style_name.replace("heading", "").strip())
            level = min(level, 6)
        except ValueError:
            level = 1
        return "#" * level + " " + text

    # List items
    if style_name.startswith("list"):
        return "- " + text

    return text




def _format_table(table) -> str:
    """Formats a docx table as a markdown table."""
    rows = table.rows
    if not rows:
        return ""

    md_rows: list[str] = []
    for i, row in enumerate(rows):
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        md_rows.append("| " + " | ".join(cells) + " |")
        if i == 0:
            md_rows.append("| " + " | ".join("---" for _ in cells) + " |")

    return "\n".join(md_rows)



def _read_pdf(path: Path) -> str:
    """Полное чтение PDF: текст (через PyMuPDF) + таблицы (через pdfplumber).

    Без лимитов на размер/число страниц. Возвращает markdown-подобный текст:
      - каждая страница с заголовком `## Page N`
      - текст в естественном порядке чтения (sort=True)
      - таблицы, найденные на странице, рендерятся ниже текста как markdown
    """
    try:
        import pymupdf  # PyMuPDF
    except ImportError:
        try:
            import fitz as pymupdf  # старое имя пакета
        except ImportError:
            return (
                "[Error: pymupdf not installed. "
                "Run: uv add pymupdf]"
            )

    try:
        pdfplumber = __import__("pdfplumber")
    except ImportError:
        pdfplumber = None

    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        return f"[Error opening PDF: {e}]"

    plumber_doc = None
    if pdfplumber is not None:
        try:
            plumber_doc = pdfplumber.open(str(path))
        except Exception:
            plumber_doc = None

    parts: list[str] = []
    meta = doc.metadata or {}
    title = (meta.get("title") or "").strip()
    author = (meta.get("author") or "").strip()
    header_bits = [f"pages: {doc.page_count}"]
    if title:
        header_bits.append(f"title: {title}")
    if author:
        header_bits.append(f"author: {author}")
    parts.append("[PDF · " + " · ".join(header_bits) + "]")

    for page_idx in range(doc.page_count):
        page = doc.load_page(page_idx)
        page_parts: list[str] = [f"## Page {page_idx + 1}"]

        try:
            text = page.get_text("text", sort=True) or ""
        except Exception as e:
            text = f"[text extraction failed: {e}]"
        text = text.strip()
        if text:
            page_parts.append(text)

        if plumber_doc is not None and page_idx < len(plumber_doc.pages):
            try:
                pl_page = plumber_doc.pages[page_idx]
                tables = pl_page.extract_tables() or []
            except Exception:
                tables = []
            for t_idx, table in enumerate(tables, start=1):
                if not table:
                    continue
                md_rows: list[str] = [f"\n### Table {page_idx + 1}.{t_idx}"]
                norm = [
                    [(cell if cell is not None else "").replace("\n", " ").strip() for cell in row]
                    for row in table
                ]
                if not norm or not norm[0]:
                    continue
                width = max(len(r) for r in norm)
                norm = [r + [""] * (width - len(r)) for r in norm]
                md_rows.append("| " + " | ".join(norm[0]) + " |")
                md_rows.append("| " + " | ".join("---" for _ in range(width)) + " |")
                for row in norm[1:]:
                    md_rows.append("| " + " | ".join(row) + " |")
                page_parts.append("\n".join(md_rows))

        parts.append("\n\n".join(page_parts))

    doc.close()
    if plumber_doc is not None:
        try:
            plumber_doc.close()
        except Exception:
            pass

    return "\n\n".join(parts)




def _safe_read(path: Path, encoding: str = "utf-8") -> str:
    """Read a file fully."""
    return path.read_text(encoding=encoding, errors="replace")




