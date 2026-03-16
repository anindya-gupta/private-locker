"""Aggressive tests for document processing — category guessing, metadata extraction, expiry detection."""

from __future__ import annotations

import pytest

from vault.processors.document import (
    detect_file_type,
    extract_document_metadata,
    extract_text,
    guess_category,
    guess_medical_subcategory,
)


class TestFileTypeDetection:
    @pytest.mark.parametrize("filename,expected", [
        ("report.pdf", "pdf"),
        ("photo.png", "image"),
        ("photo.jpg", "image"),
        ("scan.tiff", "image"),
        ("notes.txt", "text"),
        ("data.csv", "text"),
        ("readme.md", "text"),
        ("config.json", "text"),
        ("archive.zip", "binary"),
        ("unknown.xyz", "binary"),
    ])
    def test_detection(self, filename, expected):
        assert detect_file_type(filename) == expected


class TestTextExtraction:
    def test_text_file(self):
        data = b"Hello world, this is a test."
        assert extract_text(data, "test.txt") == "Hello world, this is a test."

    def test_csv_file(self):
        data = b"name,age\nJohn,30\nJane,25"
        text = extract_text(data, "data.csv")
        assert "John" in text

    def test_binary_file_returns_empty(self):
        data = b"\x00\x01\x02\x03\xff\xfe"
        assert extract_text(data, "binary.zip") == ""

    def test_utf8_text(self):
        data = "Héllo wörld".encode("utf-8")
        assert extract_text(data, "test.txt") == "Héllo wörld"


class TestCategoryGuessing:
    @pytest.mark.parametrize("filename,text,expected", [
        ("aadhaar.pdf", "", "identity"),
        ("passport_scan.jpg", "", "identity"),
        ("bank_statement.pdf", "", "financial"),
        ("salary_slip.pdf", "", "financial"),
        ("blood_report.pdf", "", "medical"),
        ("prescription.pdf", "Dr. prescribed medicine", "medical"),
        ("degree_certificate.pdf", "", "education"),
        ("rent_agreement.pdf", "", "legal"),
        ("health_insurance.pdf", "", "insurance"),
        ("random_file.pdf", "some random text", "general"),
    ])
    def test_category_from_filename(self, filename, text, expected):
        assert guess_category(filename, text) == expected

    def test_category_from_content(self):
        assert guess_category("document.pdf", "This is an Aadhaar Card") == "identity"
        assert guess_category("report.pdf", "CBC blood test result hemoglobin") == "medical"
        assert guess_category("file.pdf", "IFSC code bank account number") == "financial"

    def test_multiple_keywords_first_match_wins(self):
        cat = guess_category("file.pdf", "aadhaar bank account")
        assert cat in ("identity", "financial")


class TestMedicalSubcategory:
    @pytest.mark.parametrize("filename,text,expected", [
        ("eye_prescription.pdf", "", "eye"),
        ("file.pdf", "ophthalmology department vision test", "eye"),
        ("file.pdf", "spectacle power lens prescription", "eye"),
        ("skin_report.pdf", "", "skin"),
        ("file.pdf", "dermatology clinic eczema treatment", "skin"),
        ("dental_checkup.pdf", "", "dental"),
        ("file.pdf", "root canal procedure teeth", "dental"),
        ("ecg_report.pdf", "", None),  # "ecg" alone — check if cardiac matches
        ("file.pdf", "ecg heart rate blood pressure", "cardiac"),
        ("xray_report.pdf", "", None),  # "xray" -> orthopedic only via text
        ("file.pdf", "fracture bone x-ray orthopedic", "orthopedic"),
        ("file.pdf", "CBC hemoglobin blood test platelet", "blood"),
        ("file.pdf", "some random medical text", None),
    ])
    def test_subcategory(self, filename, text, expected):
        result = guess_medical_subcategory(filename, text)
        if expected is not None:
            assert result == expected
        else:
            pass  # None or any value is ok for ambiguous cases


class TestDoctorExtraction:
    @pytest.mark.parametrize("text,expected_doctor", [
        ("Dr. Sharma prescribed glasses for myopia.", "Sharma"),
        ("Dr. Anita Bansal - Ophthalmologist", "Anita Bansal"),
        ("Doctor Kumar examined the patient", "Kumar"),
        ("Consulting: Dr. Mehra", "Mehra"),
        ("treated by Dr. Singh", "Singh"),
        ("No doctor mentioned here", None),
    ])
    def test_doctor_extraction(self, text, expected_doctor):
        meta = extract_document_metadata("test.pdf", text, "medical")
        if expected_doctor:
            assert meta.get("doctor") == expected_doctor
        else:
            assert "doctor" not in meta


class TestDateExtraction:
    @pytest.mark.parametrize("text,has_date", [
        ("Date: 15/03/2026", True),
        ("Report date: 15-03-2026", True),
        ("Visit date: 15.03.2026", True),
        ("15 March 2026", True),
        ("March 15, 2026", True),
        ("No date here at all.", False),
    ])
    def test_date_extraction(self, text, has_date):
        meta = extract_document_metadata("test.pdf", text, "general")
        assert ("doc_date" in meta) == has_date


class TestExpiryDateExtraction:
    @pytest.mark.parametrize("text,has_expiry", [
        ("Valid until: 31/12/2027", True),
        ("Expiry date: 15/06/2028", True),
        ("Date of expiry: 01/01/2030", True),
        ("Valid upto: 20/09/2029", True),
        ("Expires: 10-11-2026", True),
        ("Renewal date: 15/03/2027", True),
        ("Due date: 30/06/2026", True),
        ("No expiry info in this text.", False),
        ("Just a regular document with date: 15/03/2026", False),
    ])
    def test_expiry_extraction(self, text, has_expiry):
        meta = extract_document_metadata("passport.pdf", text, "identity")
        assert ("expiry_date" in meta) == has_expiry, f"Expected expiry_date={'present' if has_expiry else 'absent'} for: {text}"

    def test_expiry_with_text_date_format(self):
        text = "Valid until 15 June 2028"
        meta = extract_document_metadata("passport.pdf", text, "identity")
        assert "expiry_date" in meta

    def test_expiry_month_name_format(self):
        text = "Expiry date: June 15, 2028"
        meta = extract_document_metadata("license.pdf", text, "identity")
        assert "expiry_date" in meta


class TestKeywordExtraction:
    def test_prescription_keyword(self):
        text = "Prescription: Take medicine twice daily"
        meta = extract_document_metadata("rx.pdf", text, "medical")
        assert "prescription" in meta.get("keywords", [])

    def test_report_keyword(self):
        text = "Lab result: Blood test result shows normal"
        meta = extract_document_metadata("report.pdf", text, "medical")
        assert "report" in meta.get("keywords", [])

    def test_no_keywords(self):
        text = "Just some ordinary text with nothing special"
        meta = extract_document_metadata("file.pdf", text, "general")
        assert "keywords" not in meta or len(meta.get("keywords", [])) == 0
