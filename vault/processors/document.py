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


MEDICAL_SUBCATEGORIES = {
    "eye": ["eye", "ophthalmol", "optom", "vision", "retina", "cataract", "glaucoma", "myopia", "hypermetropia",
            "astigmatism", "spectacle", "glasses", "lens", "cornea", "lasik", "optic"],
    "skin": ["skin", "dermatol", "eczema", "acne", "psoriasis", "rash", "melanoma", "mole", "fungal"],
    "dental": ["dental", "dentist", "tooth", "teeth", "oral", "gum", "cavity", "root canal", "orthodont", "braces"],
    "cardiac": ["cardiac", "cardiol", "heart", "ecg", "ekg", "echocardiogram", "blood pressure", "hypertension", "cholesterol"],
    "orthopedic": ["ortho", "bone", "fracture", "joint", "spine", "x-ray", "xray", "mri", "ct scan"],
    "general": ["general", "physician", "fever", "cold", "cough", "flu"],
    "blood": ["blood test", "cbc", "hemoglobin", "platelet", "wbc", "rbc", "blood report", "hematology", "pathology"],
}


def guess_medical_subcategory(filename: str, extracted_text: str) -> str | None:
    """Detect medical sub-category from filename and text content."""
    combined = (filename.lower() + " " + extracted_text.lower()[:1000])
    for subcat, keywords in MEDICAL_SUBCATEGORIES.items():
        if any(kw in combined for kw in keywords):
            return subcat
    return None


def extract_document_metadata(filename: str, extracted_text: str, category: str) -> dict:
    """Extract rich metadata from document text using regex patterns.

    Returns dict with optional keys: sub_category, doctor, doc_date, keywords.
    """
    import re
    from datetime import datetime

    meta: dict = {}
    lower_text = extracted_text.lower()
    lower_name = filename.lower()
    combined = lower_name + " " + lower_text[:2000]

    if category == "medical":
        subcat = guess_medical_subcategory(filename, extracted_text)
        if subcat:
            meta["sub_category"] = subcat

    doctor_patterns = [
        r"(?:[Dd]r\.?|[Dd]octor)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
        r"(?:consulting|attending|referred by|treated by)\s*:?\s*(?:[Dd]r\.?\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
    ]
    for pat in doctor_patterns:
        m = re.search(pat, extracted_text)
        if m:
            meta["doctor"] = m.group(1).strip()
            break

    date_patterns = [
        r"(?:date|dated|report date|visit date|consultation date)\s*:?\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})",
        r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})",
        r"(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4})",
        r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{2,4})",
    ]
    for pat in date_patterns:
        m = re.search(pat, extracted_text, re.IGNORECASE)
        if m:
            date_str = m.group(1).strip()
            for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
                        "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y",
                        "%d/%m/%y", "%m/%d/%y"):
                try:
                    parsed = datetime.strptime(date_str, fmt)
                    meta["doc_date"] = parsed.strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
            if "doc_date" not in meta:
                meta["doc_date"] = date_str
            break

    kw_pools = {
        "prescription": ["prescription", "rx", "prescribed"],
        "report": ["report", "test result", "lab result", "investigation"],
        "certificate": ["certificate", "certified", "certification"],
        "receipt": ["receipt", "invoice", "bill", "payment"],
        "insurance_claim": ["claim", "insurance", "tpa", "cashless"],
    }
    extracted_kw = []
    for kw, triggers in kw_pools.items():
        if any(t in combined for t in triggers):
            extracted_kw.append(kw)
    if extracted_kw:
        meta["keywords"] = extracted_kw

    return meta
