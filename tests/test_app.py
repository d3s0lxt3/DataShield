import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO

from datashield import create_app
from datashield.db import get_db
from datashield.rules import QUESTIONS


class AppTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = str(Path(self.tempdir.name) / "test.db")
        self.app = create_app({"TESTING": True, "CSRF_ENABLED": False, "DATABASE": self.database})
        self.client = self.app.test_client()

    def tearDown(self):
        self.tempdir.cleanup()

    def answer_payload(self, **changes):
        answers = {question["key"]: 2 for question in QUESTIONS}
        answers.update({
            "has_personal_data": 1, "customers_ru": 1, "eu_clients": 0, "eu_activity": 0,
            "large_scale_monitoring": 0, "special_data": 0, "biometric_data": 0,
            "marketing": 0, "profiling": 0, "processors": 0, "cross_border": 0,
            "has_policy": 1, "has_security": 1,
        })
        answers.update(changes)
        return {f"answer-{key}": value for key, value in answers.items()}

    def save_assessment(self, **changes):
        return self.client.post("/assessment", data=self.answer_payload(**changes), follow_redirects=True)

    def test_pages_and_company_profile(self):
        for path in ("/", "/assessment", "/documents", "/incidents", "/methodology", "/health"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
        response = self.client.post("/company", data={
            "name": "ООО «Север»", "inn": "77-01-234567", "email": "privacy@example.ru",
            "responsible": "Петрова А. А.", "processing_purposes": "Договоры",
            "data_categories": "Клиенты: контакты",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("ООО «Север»".encode(), response.data)
        with self.app.app_context():
            company = get_db().execute("SELECT * FROM company WHERE id = 1").fetchone()
            self.assertEqual(company["inn"], "7701234567")

    def test_assessment_progress_and_manual_requirement(self):
        response = self.save_assessment(eu_clients=1)
        self.assertIn("Перечень требований обновлён".encode(), response.data)
        with self.app.app_context():
            connection = get_db()
            statuses = dict(connection.execute("SELECT key, status FROM requirement_progress"))
            self.assertEqual(statuses["public_policy"], "done")
            self.assertEqual(statuses["security_measures"], "done")
            self.assertEqual(statuses["has_consents"] if "has_consents" in statuses else statuses["legal_basis_and_consents"], "pending")
        response = self.client.post("/requirements/gdpr_records", data={
            "status": "done", "note": "Реестр утверждён", "due_date": "2026-12-01",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.save_assessment(eu_clients=1)
        with self.app.app_context():
            row = get_db().execute("SELECT * FROM requirement_progress WHERE key = 'gdpr_records'").fetchone()
            self.assertEqual(row["status"], "done")
            self.assertEqual(row["note"], "Реестр утверждён")
        dashboard = self.client.get("/")
        self.assertIn(b"%", dashboard.data)

    def test_manual_progress_survives_reassessment(self):
        self.save_assessment()
        self.client.post("/requirements/public_policy", data={
            "status": "in_progress", "note": "Согласование новой редакции", "due_date": "2026-12-01",
        })
        self.save_assessment(has_personal_data=0)
        self.save_assessment()
        with self.app.app_context():
            row = get_db().execute("SELECT * FROM requirement_progress WHERE key='public_policy'").fetchone()
            self.assertEqual((row["status"], row["note"], row["due_date"]),
                             ("in_progress", "Согласование новой редакции", "2026-12-01"))

    def test_incident_decisions_and_independent_clocks(self):
        case = self.incident_payload(rkn_required="unknown", gdpr_required="no")
        self.client.post("/incidents/new", data=case)
        self.assertNotIn(b'class="deadline-card', self.client.get("/incidents/1").data)
        case.update(rkn_required="yes", gdpr_required="yes", gdpr_aware_at="2026-11-18T13:00",
                    rkn_final_at="2026-11-20T12:30")
        response = self.client.post("/incidents/1/update", data=case, follow_redirects=True)
        for moment in ("19.11.2026, 10:00", "21.11.2026, 10:00", "21.11.2026, 13:00", "20.11.2026, 12:30"):
            self.assertIn(moment.encode(), response.data)
        csv = self.client.get("/incidents/export.csv").data.decode("utf-8-sig")
        self.assertIn("2026-11-18T13:00", csv)

    def test_document_downloads(self):
        for key in ("privacy_policy", "consent", "responsible_order", "processing_register", "incident_procedure"):
            with self.subTest(key=key):
                response = self.client.get(f"/documents/{key}.docx")
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.data.startswith(b"PK"))
                self.assertEqual(self.client.get(f"/documents/{key}").status_code, 200)
        package = self.client.get("/documents/package.zip")
        self.assertEqual(package.status_code, 200)
        with ZipFile(BytesIO(package.data)) as archive:
            self.assertEqual(len(archive.namelist()), 5)

    def incident_payload(self, **changes):
        payload = {
            "rkn_required": "yes", "gdpr_required": "no", "title": "Доступ к почте", "detected_at": "2026-11-18T10:00",
            "category": "unauthorized_access", "description": "Подозрительный вход",
            "systems": "Почта", "data_categories": "Контакты клиентов",
            "subjects_count": "12", "risk": "high", "status": "new",
            "rkn_notified_at": "", "gdpr_notified_at": "", "resolution": "",
        }
        payload.update(changes)
        return payload

    def test_incident_lifecycle_and_export(self):
        response = self.client.post("/incidents/new", data=self.incident_payload(), follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("19.11.2026".encode(), response.data)
        self.assertIn("21.11.2026".encode(), response.data)
        response = self.client.post("/incidents/1/update", data=self.incident_payload(
            status="resolved", rkn_notified_at="2026-11-18T15:00", resolution="Доступ отозван",
        ), follow_redirects=True)
        self.assertIn("Доступ отозван".encode(), response.data)
        response = self.client.get("/incidents?status=resolved&q=почте")
        self.assertIn("Доступ к почте".encode(), response.data)
        export = self.client.get("/incidents/export.csv")
        self.assertEqual(export.status_code, 200)
        self.assertIn("Доступ к почте", export.data.decode("utf-8-sig"))
        response = self.client.post("/incidents/1/delete", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Доступ к почте".encode(), response.data)

    def test_invalid_incident_is_rejected(self):
        response = self.client.post("/incidents/new", data=self.incident_payload(
            title="", subjects_count="-1",
        ))
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            count = get_db().execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
            self.assertEqual(count, 0)

    def test_csrf_is_required_when_enabled(self):
        app = create_app({"TESTING": True, "CSRF_ENABLED": True, "DATABASE": self.database})
        response = app.test_client().post("/company", data={"name": "Test"})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
