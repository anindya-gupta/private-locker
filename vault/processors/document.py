"""
Document processing pipeline.

Extracts text from PDFs and images (via OCR), then stores
encrypted text + vector embeddings for semantic search.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

IMAGE_MIMETYPES = {"image/png", "image/jpeg", "image/jpg", "image/tiff", "image/bmp", "image/webp"}
PDF_MIMETYPES = {"application/pdf"}


def detect_file_type(filename: str) -> str:
    mime, _ = mimetypes.guess_type(filename)
    if mime in IMAGE_MIMETYPES:
        return "image"
    if mime in PDF_MIMETYPES:
        return "pdf"
    if mime and mime.startswith("text/"):
        return "text"
    ext = Path(filename).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"):
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in (".txt", ".md", ".csv", ".json", ".xml"):
        return "text"
    return "binary"


def extract_text_from_pdf(data: bytes) -> str:
    """Extract text from a PDF using PyMuPDF."""
    try:
        import fitz
        doc = fitz.open(stream=data, filetype="pdf")
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n".join(text_parts).strip()
    except ImportError:
        logger.warning("PyMuPDF not installed. PDF text extraction unavailable.")
        return ""
    except Exception as e:
        logger.error("PDF extraction failed: %s", e)
        return ""


def extract_text_from_image(data: bytes) -> str:
    """Extract text from an image using Tesseract OCR."""
    try:
        import io
        from PIL import Image
        import pytesseract

        image = Image.open(io.BytesIO(data))
        text = pytesseract.image_to_string(image)
        return text.strip()
    except ImportError:
        logger.warning("pytesseract/Pillow not installed. OCR unavailable.")
        return ""
    except Exception as e:
        logger.error("OCR extraction failed: %s", e)
        return ""


def extract_text(data: bytes, filename: str) -> str:
    """Auto-detect file type and extract text."""
    file_type = detect_file_type(filename)

    if file_type == "pdf":
        return extract_text_from_pdf(data)
    elif file_type == "image":
        return extract_text_from_image(data)
    elif file_type == "text":
        try:
            return data.decode("utf-8").strip()
        except UnicodeDecodeError:
            return data.decode("latin-1").strip()
    else:
        return ""


def guess_category(filename: str, extracted_text: str) -> str:
    """Best-effort category detection from filename and content."""
    lower_name = filename.lower()
    lower_text = extracted_text.lower()

    keywords = {
        "identity": ["aadhaar", "aadhar", "passport", "pan card", "voter", "driving license", "dl", "voter id"],
        "financial": ["bank", "account", "ifsc", "cheque", "tax", "itr", "form 16", "salary", "payslip"],
        "medical": ["blood", "prescription", "medical", "hospital", "diagnosis", "vaccination", "covid"],
        "education": ["degree", "certificate", "marksheet", "transcript", "school", "college", "university"],
        "legal": ["agreement", "contract", "lease", "rental", "will", "power of attorney", "affidavit"],
        "insurance": ["insurance", "policy", "premium", "claim", "lic", "health insurance"],
    }

    combined = lower_name + " " + lower_text[:500]
    for category, words in keywords.items():
        if any(w in combined for w in words):
            return category

    return "general"
