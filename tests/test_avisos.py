"""Correo a quien pidió unirse cuando el creador acepta o rechaza su solicitud."""

from app import mailer
from app.models import Usuario, now_utc
from tests.test_solicitudes import answer, ask, create_group, pending_ids


def request_from_luis(client, users, **fields):
    group_id = create_group(client, users["ana"], **fields)
    ask(client, users["luis"], group_id)
    return pending_ids(client, users["ana"])[0]


def test_accepting_emails_the_applicant(client, users, answer_outbox):
    request_id = request_from_luis(client, users)

    assert answer(client, users["ana"], request_id, "aceptar").status_code == 200

    assert answer_outbox == [("luis@uc.cl", "Luis Castro", "MAT1620 Cálculo II", True)]


def test_rejecting_emails_the_applicant(client, users, answer_outbox):
    request_id = request_from_luis(client, users)

    assert answer(client, users["ana"], request_id, "rechazar").status_code == 200

    assert answer_outbox == [("luis@uc.cl", "Luis Castro", "MAT1620 Cálculo II", False)]


def test_no_email_if_the_answer_fails(client, users, answer_outbox):
    request_id = request_from_luis(client, users)
    answer(client, users["ana"], request_id, "rechazar")
    answer_outbox.clear()

    # Ya está respondida: 409 y sin un segundo correo
    assert answer(client, users["ana"], request_id, "aceptar").status_code == 409
    # Quien no es el creador no puede responderla
    assert answer(client, users["sofia"], request_id, "rechazar").status_code == 404
    assert answer_outbox == []


def test_no_email_to_a_deleted_account(client, users, answer_outbox, session_factory):
    request_id = request_from_luis(client, users)
    with session_factory() as db:
        db.query(Usuario).filter_by(email="luis@uc.cl").one().eliminado_en = now_utc()
        db.commit()

    answer(client, users["ana"], request_id, "rechazar")

    assert answer_outbox == []


def test_answer_is_saved_even_if_the_email_fails(client, users, monkeypatch):
    def broken(*args):
        raise RuntimeError("Brevo no responde")

    monkeypatch.setattr("app.routers.solicitudes.send_request_answered_email", broken)
    request_id = request_from_luis(client, users)

    assert answer(client, users["ana"], request_id, "aceptar").status_code == 200
    group = client.get("/api/grupos/mios", headers=users["luis"]).json()
    assert [g["asignatura"]["sigla"] for g in group["participo"]] == ["MAT1620"]


# --- Contenido del correo ---


def capture_email(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send_email", lambda *args, **kwargs: sent.append((args, kwargs)))
    return sent


def test_accepted_email_content(monkeypatch):
    sent = capture_email(monkeypatch)

    mailer.send_request_answered_email("luis@uc.cl", "Luis Castro", "MAT1620 Cálculo II", True)

    (to, subject), body = sent[0]
    assert to == "luis@uc.cl"
    assert subject == "Te aceptaron en el grupo de MAT1620 Cálculo II"
    assert "Hola Luis:" in body["text"]
    assert "Mis grupos" in body["text"]
    assert mailer.frontend_url() in body["html"]


def test_rejected_email_content_escapes_html(monkeypatch):
    sent = capture_email(monkeypatch)

    mailer.send_request_answered_email("luis@uc.cl", "<b>Luis</b>", "IIC2143 <Software>", False)

    (_, subject), body = sent[0]
    assert subject == "Tu solicitud al grupo de IIC2143 <Software> no fue aceptada"
    assert "<b>Luis</b>" not in body["html"]
    assert "&lt;Software&gt;" in body["html"]
