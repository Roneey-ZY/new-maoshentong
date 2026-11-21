"""Read original evidence independently of normalized and manually edited values."""
import io

import pdfplumber

from core.parser import checked_format, checked_rows, ParseError


def original_evidence(file, source):
    if file.name != source.file:
        raise ValueError("证据文件名不匹配")
    ext = checked_format(file.name, file.data)
    if ext != ".pdf":
        if source.row is None:
            return {"location": source.display(), "rows": [], "message": "未记录精确行号，请对照原件。"}
        rows = []
        for sheet, number, values in checked_rows(file.data, ext):
            if sheet == source.sheet and abs(number - source.row) <= 1:
                rows.append({"sheet": sheet, "row": number, "values": ["" if v is None else str(v) for v in values],
                             "target": number == source.row})
        return {"location": source.display(), "rows": rows,
                "message": "以下为上传原件中的行，未应用预览修改。"}
    with pdfplumber.open(io.BytesIO(file.data)) as pdf:
        if source.page is None or not 1 <= source.page <= len(pdf.pages):
            raise ParseError("没有可定位的 PDF 页码，请对照原件。")
        text = (pdf.pages[source.page - 1].extract_text() or "")[:24000]
    return {"location": source.display(), "text": text,
            "message": "以下为原件对应页全文。PDF 行号为解析位置，不是视觉行号；未做字段框选。"}


def pdf_page_image(file, page_number):
    """Bound rendering size; no OCR and no fabricated field highlight."""
    import pypdfium2 as pdfium
    checked_format(file.name, file.data)
    with pdfium.PdfDocument(file.data) as pdf:
        if not 1 <= page_number <= len(pdf):
            raise ParseError("PDF 页码无效")
        page = pdf[page_number - 1]
        try:
            width, height = page.get_size()
            if min(width, height) <= 0 or max(width, height) > 20000:
                raise ParseError("PDF 页面尺寸超出预览范围")
            bitmap = page.render(scale=min(1.25, 1600 / max(width, height)))
            try:
                buffer = io.BytesIO()
                bitmap.to_pil().save(buffer, format="PNG")
                return buffer.getvalue()
            finally:
                bitmap.close()
        finally:
            page.close()
