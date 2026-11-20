import csv
import io
from datetime import datetime, timedelta

from flask import (
    Blueprint, Response, abort, flash, redirect, render_template, request,
    send_file, url_for,
)

from .db import COMPANY_FIELDS, get_answers, get_company, get_db
from .documents import (
    DOCUMENTS, build_documents_zip, build_docx, get_document,
)
from .rules import QUESTIONS, evaluate, gdpr_scope

bp = Blueprint("main", __name__)

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
QUESTION_MAP = {question["key"]: question for question in QUESTIONS}
INCIDENT_CATEGORIES = {
    "unauthorized_access", "disclosure", "loss", "malware", "availability", "other",
}
INCIDENT_STATUSES = {"new", "investigating", "contained", "resolved"}
RISKS = {"low", "medium", "high"}


def now_iso():
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def safe_next(default="main.dashboard"):
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//"):
        return target
    return url_for(default)


def sync_requirements(connection, previous_answers):
    answers = get_answers()
    applicable = evaluate(answers)
    for requirement in applicable:
        question = requirement.get("question")
        default_status = "done" if question and answers.get(question, 2) == 1 else "pending"
        if question and previous_answers.get(question) != answers.get(question):
            connection.execute(
                """INSERT INTO requirement_progress (key, status, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET status = excluded.status,
                       updated_at = excluded.updated_at""",
                (requirement["key"], default_status, now_iso()),
            )
        else:
            connection.execute(
                "INSERT OR IGNORE INTO requirement_progress (key, status, updated_at) VALUES (?, ?, ?)",
                (requirement["key"], default_status, now_iso()),
            )


def requirements_with_progress():
    answers = get_answers()
    progress = {
        row["key"]: dict(row)
        for row in get_db().execute("SELECT * FROM requirement_progress")
    }
    requirements = []
    for requirement in evaluate(answers):
        item = dict(requirement)
        item["eu_uncertain"] = "GDPR" in item["regime"] and gdpr_scope(answers) == 2
        saved = progress.get(item["key"])
        if saved:
            item.update(status=saved["status"], note=saved["note"], due_date=saved["due_date"])
        else:
            is_done = item.get("question") and answers.get(item["question"], 2) == 1
            item.update(status="done" if is_done else "pending", note="", due_date="")
        item["question_label"] = QUESTION_MAP.get(item.get("question"), {}).get("label", "")
        requirements.append(item)
    return requirements, answers


def incident_deadlines(incident):
    detected = datetime.fromisoformat(incident["detected_at"])
    items = []
    if incident["rkn_required"] == "yes":
        for key, title, hours, actual in (
            ("rkn_initial", "Первичное уведомление Роскомнадзора", 24, "rkn_notified_at"),
            ("rkn_final", "Итоги внутреннего расследования", 72, "rkn_final_at"),
        ):
            items.append({"key": key, "title": title, "date": detected + timedelta(hours=hours),
                          "law": "ч. 3.1 ст. 21 152-ФЗ", "notified": incident[actual]})
    if incident["gdpr_required"] == "yes" and incident["gdpr_aware_at"]:
        aware = datetime.fromisoformat(incident["gdpr_aware_at"])
        items.append({"key": "gdpr", "title": "Уведомление надзорного органа GDPR",
                      "date": aware + timedelta(hours=72), "law": "ст. 33 GDPR",
                      "notified": incident["gdpr_notified_at"]})
    for item in items:
        seconds = (item["date"] - datetime.now(item["date"].tzinfo)).total_seconds()
        item["overdue"] = seconds < 0 and not item["notified"]
        item["urgent"] = 0 <= seconds <= 12 * 3600 and not item["notified"]
    return items


@bp.get("/")
def dashboard():
    requirements, answers = requirements_with_progress()
    total = len(requirements)
    done = sum(item["status"] == "done" for item in requirements)
    in_progress = sum(item["status"] == "in_progress" for item in requirements)
    percent = round(done * 100 / total) if total else 0
    scope_answered = sum(answers.get(q["key"], 2) != 2 for q in QUESTIONS if q["group"] == "scope")
    scope_total = sum(q["group"] == "scope" for q in QUESTIONS)
    company = get_company()
    profile_fields = ("name", "inn", "email", "responsible", "processing_purposes", "data_categories")
    profile_done = sum(bool(company[field].strip()) for field in profile_fields)
    connection = get_db()
    active_incidents = connection.execute(
        "SELECT COUNT(*) FROM incidents WHERE status != 'resolved'"
    ).fetchone()[0]
    recent_incidents = connection.execute(
        "SELECT * FROM incidents ORDER BY detected_at DESC LIMIT 3"
    ).fetchall()
    next_steps = sorted(
        (item for item in requirements if item["status"] != "done"),
        key=lambda item: (PRIORITY_ORDER[item["priority"]], item["status"] == "pending"),
    )[:4]
    return render_template(
        "dashboard.html", requirements=requirements, total=total, done=done,
        in_progress=in_progress, percent=percent, scope_answered=scope_answered,
        scope_total=scope_total, company=company, profile_done=profile_done,
        profile_total=len(profile_fields), active_incidents=active_incidents,
        recent_incidents=recent_incidents, next_steps=next_steps,
    )


@bp.route("/assessment", methods=("GET", "POST"))
def assessment():
    connection = get_db()
    if request.method == "POST":
        values = {}
        errors = []
        for question in QUESTIONS:
            raw = request.form.get(f"answer-{question['key']}", "2")
            try:
                value = int(raw)
            except ValueError:
                value = 2
            if value not in (0, 1, 2):
                errors.append(question["label"])
                value = 2
            values[question["key"]] = value
        if errors:
            flash("Проверьте ответы анкеты.", "error")
        else:
            previous_answers = get_answers()
            connection.executemany(
                """INSERT INTO answers (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                values.items(),
            )
            sync_requirements(connection, previous_answers)
            connection.commit()
            flash("Анкета сохранена. Перечень требований обновлён.", "success")
            return redirect(url_for("main.assessment", saved="1"))
    answers = get_answers()
    requirements, _ = requirements_with_progress()
    groups = {
        "scope": [q for q in QUESTIONS if q["group"] == "scope"],
        "measures": [q for q in QUESTIONS if q["group"] == "measures"],
    }
    return render_template(
        "assessment.html", groups=groups, answers=answers, company=get_company(),
        requirements=requirements,
    )


@bp.post("/company")
def save_company():
    values = []
    for field in COMPANY_FIELDS:
        value = request.form.get(field, "").strip()
        if field in {"inn", "ogrn"}:
            value = "".join(character for character in value if character.isdigit())
        values.append(value[:5000])
    assignments = ", ".join(f"{field} = ?" for field in COMPANY_FIELDS)
    connection = get_db()
    connection.execute(
        f"UPDATE company SET {assignments}, updated_at = ? WHERE id = 1",
        (*values, now_iso()),
    )
    connection.commit()
    flash("Профиль компании сохранён.", "success")
    return redirect(url_for("main.assessment", section="company"))


@bp.post("/requirements/<key>")
def update_requirement(key):
    requirements, _answers = requirements_with_progress()
    requirement = next((item for item in requirements if item["key"] == key), None)
    if requirement is None:
        abort(404)
    status = request.form.get("status", "pending")
    if status not in {"pending", "in_progress", "done"}:
        abort(400, "Неизвестный статус требования.")
    due_date = request.form.get("due_date", "").strip()
    if due_date:
        try:
            datetime.strptime(due_date, "%Y-%m-%d")
        except ValueError:
            abort(400, "Срок должен быть указан в формате даты.")
    note = request.form.get("note", "").strip()[:3000]
    connection = get_db()
    connection.execute(
        """INSERT INTO requirement_progress (key, status, note, due_date, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET status = excluded.status,
               note = excluded.note, due_date = excluded.due_date,
               updated_at = excluded.updated_at""",
        (key, status, note, due_date, now_iso()),
    )
    connection.commit()
    flash("Статус требования обновлён.", "success")
    return redirect(safe_next("main.assessment"))


@bp.get("/documents")
def documents():
    return render_template(
        "documents.html", documents=DOCUMENTS, company=get_company(),
    )


@bp.get("/documents/<key>")
def document_preview(key):
    try:
        document = get_document(key, get_company())
    except KeyError:
        abort(404)
    return render_template("document_preview.html", key=key, document=document)


@bp.get("/documents/<key>.docx")
def document_download(key):
    try:
        content = build_docx(key, get_company())
    except KeyError:
        abort(404)
    filename = next(item["filename"] for item in DOCUMENTS if item["key"] == key)
    return send_file(
        io.BytesIO(content), as_attachment=True, download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@bp.get("/documents/package.zip")
def documents_package():
    return send_file(
        io.BytesIO(build_documents_zip(get_company())), as_attachment=True,
        download_name="DataShield_пакет_документов.zip", mimetype="application/zip",
    )


def parse_incident_form():
    data = {
        "title": request.form.get("title", "").strip()[:300],
        "detected_at": request.form.get("detected_at", "").strip(),
        "category": request.form.get("category", "other"),
        "description": request.form.get("description", "").strip()[:10000],
        "systems": request.form.get("systems", "").strip()[:2000],
        "data_categories": request.form.get("data_categories", "").strip()[:2000],
        "subjects_count": request.form.get("subjects_count", "0").strip(),
        "special_data": 1 if request.form.get("special_data") == "1" else 0,
        "risk": request.form.get("risk", "medium"),
        "status": request.form.get("status", "new"),
        "rkn_notified_at": request.form.get("rkn_notified_at", "").strip(),
        "gdpr_notified_at": request.form.get("gdpr_notified_at", "").strip(),
        "resolution": request.form.get("resolution", "").strip()[:10000],
    }
    errors = []
    for name in ("rkn_required", "gdpr_required"):
        data[name] = request.form.get(name, "unknown")
        if data[name] not in ("yes", "no", "unknown"):
            errors.append("Проверьте решение об уведомлении.")
    for name in ("gdpr_aware_at", "rkn_final_at"):
        data[name] = request.form.get(name, "").strip()
    if data["gdpr_required"] == "yes" and not data["gdpr_aware_at"]:
        errors.append("Для срока GDPR укажите момент осведомлённости контролёра.")
    if not data["title"]:
        errors.append("Укажите название инцидента.")
    if data["category"] not in INCIDENT_CATEGORIES:
        errors.append("Выберите корректную категорию.")
    if data["risk"] not in RISKS or data["status"] not in INCIDENT_STATUSES:
        errors.append("Проверьте риск и статус инцидента.")
    for field in ("detected_at", "rkn_notified_at", "gdpr_notified_at", "rkn_final_at", "gdpr_aware_at"):
        if data[field]:
            try:
                parsed = datetime.fromisoformat(data[field])
                if parsed.year >= 9999:
                    raise ValueError
            except ValueError:
                errors.append("Проверьте дату и время инцидента.")
    if not data["detected_at"]:
        errors.append("Укажите время обнаружения.")
    try:
        data["subjects_count"] = int(data["subjects_count"])
        if data["subjects_count"] < 0 or data["subjects_count"] > 1_000_000_000:
            raise ValueError
    except ValueError:
        data["subjects_count"] = 0
        errors.append("Количество субъектов должно быть целым неотрицательным числом.")
    return data, errors


@bp.get("/incidents")
def incidents():
    clauses = []
    parameters = []
    status = request.args.get("status", "")
    risk = request.args.get("risk", "")
    query = request.args.get("q", "").strip()[:200]
    if status in INCIDENT_STATUSES:
        clauses.append("status = ?")
        parameters.append(status)
    if risk in RISKS:
        clauses.append("risk = ?")
        parameters.append(risk)
    if query:
        clauses.append("(title LIKE ? OR description LIKE ? OR systems LIKE ?)")
        parameters.extend([f"%{query}%"] * 3)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = get_db().execute(
        f"SELECT * FROM incidents {where} ORDER BY detected_at DESC", parameters
    ).fetchall()
    all_count = get_db().execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    return render_template(
        "incidents.html", incidents=rows, all_count=all_count,
        filters={"status": status, "risk": risk, "q": query},
    )


@bp.route("/incidents/new", methods=("GET", "POST"))
def incident_new():
    if request.method == "POST":
        data, errors = parse_incident_form()
        if not errors:
            timestamp = now_iso()
            fields = list(data)
            connection = get_db()
            cursor = connection.execute(
                f"""INSERT INTO incidents ({', '.join(fields)}, created_at, updated_at)
                    VALUES ({', '.join('?' for _ in fields)}, ?, ?)""",
                (*data.values(), timestamp, timestamp),
            )
            connection.commit()
            flash("Инцидент зарегистрирован. Проверьте расчётные сроки уведомлений.", "success")
            return redirect(url_for("main.incident_detail", incident_id=cursor.lastrowid))
        for error in errors:
            flash(error, "error")
    else:
        data = {
            "title": "", "detected_at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "category": "other", "description": "", "systems": "",
            "data_categories": "", "subjects_count": 0, "special_data": 0,
            "risk": "medium", "status": "new", "rkn_notified_at": "",
            "gdpr_notified_at": "", "resolution": "",
        }
    return render_template("incident_form.html", incident=data, is_new=True)


@bp.get("/incidents/<int:incident_id>")
def incident_detail(incident_id):
    incident = get_db().execute(
        "SELECT * FROM incidents WHERE id = ?", (incident_id,)
    ).fetchone()
    if incident is None:
        abort(404)
    answers = get_answers()
    eu_applicable = gdpr_scope(answers) == 1
    deadlines = incident_deadlines(incident)
    return render_template(
        "incident_detail.html", incident=incident, deadlines=deadlines,
        eu_applicable=eu_applicable,
    )


@bp.post("/incidents/<int:incident_id>/update")
def incident_update(incident_id):
    connection = get_db()
    if connection.execute("SELECT 1 FROM incidents WHERE id = ?", (incident_id,)).fetchone() is None:
        abort(404)
    data, errors = parse_incident_form()
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("main.incident_detail", incident_id=incident_id))
    fields = list(data)
    assignments = ", ".join(f"{field} = ?" for field in fields)
    connection.execute(
        f"UPDATE incidents SET {assignments}, updated_at = ? WHERE id = ?",
        (*data.values(), now_iso(), incident_id),
    )
    connection.commit()
    flash("Карточка инцидента обновлена.", "success")
    return redirect(url_for("main.incident_detail", incident_id=incident_id))


@bp.post("/incidents/<int:incident_id>/delete")
def incident_delete(incident_id):
    connection = get_db()
    cursor = connection.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
    connection.commit()
    if cursor.rowcount == 0:
        abort(404)
    flash("Запись об инциденте удалена.", "success")
    return redirect(url_for("main.incidents"))


@bp.get("/incidents/export.csv")
def incidents_export():
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow([
        "ID", "Название", "Обнаружен", "Категория", "Риск", "Статус",
        "Субъектов", "Специальные категории", "Системы", "Категории данных",
        "Уведомление Роскомнадзора", "Уведомление GDPR", "Описание", "Результат",
        "Решение РКН", "Решение GDPR", "Осведомлённость контролёра", "Итоги в РКН",
    ])
    rows = get_db().execute("SELECT * FROM incidents ORDER BY detected_at DESC")
    from . import CATEGORY_LABELS, INCIDENT_STATUS_LABELS, RISK_LABELS
    for row in rows:
        writer.writerow([
            row["id"], row["title"], row["detected_at"],
            CATEGORY_LABELS.get(row["category"], row["category"]),
            RISK_LABELS[row["risk"]], INCIDENT_STATUS_LABELS[row["status"]],
            row["subjects_count"], "Да" if row["special_data"] else "Нет",
            row["systems"], row["data_categories"], row["rkn_notified_at"],
            row["gdpr_notified_at"], row["description"], row["resolution"],
            row["rkn_required"], row["gdpr_required"], row["gdpr_aware_at"], row["rkn_final_at"],
        ])
    content = ("\ufeff" + output.getvalue()).encode("utf-8")
    return Response(
        content, mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=datashield_incidents.csv"},
    )


@bp.get("/methodology")
def methodology():
    return render_template("methodology.html", questions=QUESTIONS)


@bp.get("/health")
def health():
    get_db().execute("SELECT 1").fetchone()
    return {"status": "ok", "service": "DataShield"}
