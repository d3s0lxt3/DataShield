import csv
import io
import secrets
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, abort, render_template, request, session

from . import db


STATUS_LABELS = {
    "pending": "К выполнению",
    "in_progress": "В работе",
    "done": "Выполнено",
}
RISK_LABELS = {"low": "Низкий", "medium": "Средний", "high": "Высокий"}
INCIDENT_STATUS_LABELS = {
    "new": "Новый",
    "investigating": "Расследование",
    "contained": "Локализован",
    "resolved": "Закрыт",
}
CATEGORY_LABELS = {
    "unauthorized_access": "Несанкционированный доступ",
    "disclosure": "Раскрытие или передача данных",
    "loss": "Утрата или уничтожение данных",
    "malware": "Вредоносное ПО",
    "availability": "Нарушение доступности",
    "other": "Другое",
}
ANSWER_LABELS = {0: "Нет", 1: "Да", 2: "Не знаю"}


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=str(Path(app.instance_path) / "datashield.db"),
        SECRET_KEY=secrets.token_hex(32),
        CSRF_ENABLED=True,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app)

    from .routes import bp
    app.register_blueprint(bp)

    @app.before_request
    def csrf_protect():
        if request.method == "POST" and app.config["CSRF_ENABLED"]:
            expected = session.get("csrf_token", "")
            supplied = request.form.get("csrf_token", "")
            if not expected or not supplied or not secrets.compare_digest(expected, supplied):
                abort(400, "Недействительный токен формы. Обновите страницу и повторите действие.")

    @app.after_request
    def secure_response(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "base-uri 'self'; object-src 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.context_processor
    def template_helpers():
        token = session.get("csrf_token")
        if token is None:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return {
            "csrf_token": token,
            "status_labels": STATUS_LABELS,
            "risk_labels": RISK_LABELS,
            "incident_status_labels": INCIDENT_STATUS_LABELS,
            "category_labels": CATEGORY_LABELS,
            "answer_labels": ANSWER_LABELS,
        }

    @app.template_filter("date_ru")
    def date_ru(value, with_time=True):
        if not value:
            return "—"
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
            return parsed.strftime("%d.%m.%Y, %H:%M" if with_time else "%d.%m.%Y")
        except (ValueError, TypeError):
            return value

    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400, message=str(error.description)), 400

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", code=404, message="Такой страницы не существует."), 404

    return app
