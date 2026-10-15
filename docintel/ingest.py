"""Turn an uploaded file into a Document: text, structured data and forensic metadata."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

from .models import Document


def load_bytes(filename: str, content: bytes) -> Document:
    sha = hashlib.sha256(content).hexdigest()
    ext = Path(filename).suffix.lower()
    text, data, meta = "", None, {"extension": ext, "size_bytes": len(content)}

    if ext == ".pdf":
        text, meta = _read_pdf(content, meta)
    elif ext == ".csv":
        text = content.decode("utf-8", errors="replace")
        data = list(csv.DictReader(io.StringIO(text)))
    elif ext == ".json":
        text = content.decode("utf-8", errors="replace")
        data = json.loads(text)
    elif ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        text, meta = _read_image(content, meta)
    else:
        text = content.decode("utf-8", errors="replace")

    return Document(filename=filename, sha256=sha, text=text, data=data, metadata=meta)


def load_path(path: str | Path) -> Document:
    p = Path(path)
    return load_bytes(p.name, p.read_bytes())


def _read_pdf(content: bytes, meta: dict) -> tuple[str, dict]:
    import pdfplumber

    pages, fonts = [], set()
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        info = pdf.metadata or {}
        meta.update({
            "page_count": len(pdf.pages),
            "producer": info.get("Producer"),
            "creator": info.get("Creator"),
            "created": info.get("CreationDate"),
            "modified": info.get("ModDate"),
        })
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
            fonts.update(c.get("fontname") for c in page.chars if c.get("fontname"))
    meta["fonts"] = sorted(fonts)
    text = "\n".join(pages)
    # Scanned PDF with no text layer -> needs OCR
    meta["needs_ocr"] = len(text.strip()) < 20
    return text, meta


def _read_image(content: bytes, meta: dict) -> tuple[str, dict]:
    """OCR images if Tesseract is installed; otherwise flag for OCR/LLM vision."""
    from PIL import Image

    img = Image.open(io.BytesIO(content))
    meta.update({"width": img.width, "height": img.height, "exif_software": _exif_software(img)})
    try:
        import pytesseract

        text = pytesseract.image_to_string(img)
        meta["ocr_engine"] = "tesseract"
    except Exception:
        text = ""
        meta["needs_ocr"] = True
    return text, meta


def _exif_software(img) -> str | None:
    try:
        return img.getexif().get(0x0131)  # 'Software' tag: e.g. 'Adobe Photoshop'
    except Exception:
        return None
