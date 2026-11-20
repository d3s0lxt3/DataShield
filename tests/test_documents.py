import io
import unittest
from zipfile import ZipFile

from docx import Document

from datashield.documents import DOCUMENTS, build_documents_zip, build_docx, get_document


COMPANY = {
    "name": "ООО «Север»", "legal_form": "ООО", "inn": "7701234567",
    "ogrn": "1237700123456", "address": "Москва, Тестовая улица, 1",
    "website": "https://example.ru", "email": "privacy@example.ru",
    "phone": "+7 000 000-00-00", "director": "Иванов И. И.",
    "responsible": "Петрова А. А.", "processing_purposes": "Исполнение договоров",
    "data_categories": "Клиенты: ФИО, телефон", "processors_list": "Хостинг-провайдер",
    "storage_period": "До достижения целей с учётом сроков закона",
}


class DocumentTests(unittest.TestCase):
    def test_each_document_contains_company_data_and_valid_docx(self):
        for metadata in DOCUMENTS:
            with self.subTest(document=metadata["key"]):
                content = build_docx(metadata["key"], COMPANY)
                self.assertGreater(len(content), 5000)
                document = Document(io.BytesIO(content))
                text = "\n".join(paragraph.text for paragraph in document.paragraphs)
                self.assertIn("ООО", text)
                self.assertIn("Редактируемый проект", text)
                preview = get_document(metadata["key"], COMPANY)
                self.assertTrue(preview["blocks"])

    def test_package_contains_all_documents(self):
        package = build_documents_zip(COMPANY)
        with ZipFile(io.BytesIO(package)) as archive:
            self.assertEqual(set(archive.namelist()), {item["filename"] for item in DOCUMENTS})
            for name in archive.namelist():
                self.assertTrue(archive.read(name).startswith(b"PK"))

    def test_full_name_is_not_prefixed_twice(self):
        preview = str(get_document("privacy_policy", COMPANY)["blocks"])
        self.assertIn("ООО «Север»", preview)
        self.assertNotIn("ООО ООО", preview)

    def test_empty_company_is_explicit(self):
        preview = get_document("privacy_policy", {})
        text = str(preview["blocks"])
        self.assertIn("Не указано", text)


if __name__ == "__main__":
    unittest.main()
