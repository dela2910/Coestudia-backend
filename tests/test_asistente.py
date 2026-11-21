import base64
import io
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from pypdf import PdfReader

from app.claude import get_claude
from app.main import app
from app.models import Archivo, MensajeAsistente
from app.routers import asistente
from tests import test_archivos
from tests.test_extract import make_pdf

# Mismos grupo y almacenamiento que en los tests de archivos
storage = test_archivos.storage
group = test_archivos.group
upload = test_archivos.upload


def api_error(status_code=400):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIStatusError(
        "Sin saldo", response=httpx.Response(status_code, request=request), body=None
    )


class FakeStream:
    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        if self.fake.error:
            raise self.fake.error
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for text, _ in self.fake.blocks:
            yield text
        if self.fake.error_mid_stream:
            raise self.fake.error_mid_stream

    def get_final_message(self):
        content = [
            SimpleNamespace(
                type="text",
                text=text,
                citations=[SimpleNamespace(type="char_location", document_index=i) for i in cited],
            )
            for text, cited in self.fake.blocks
        ]
        return SimpleNamespace(
            content=content,
            stop_reason=self.fake.stop_reason,
            usage=SimpleNamespace(
                input_tokens=100,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                output_tokens=20,
            ),
        )


class FakeClaude:
    """Reemplaza al cliente de Anthropic: guarda lo que se le pide y responde lo configurado."""

    def __init__(self):
        self.calls = []
        self.error = None
        self.error_mid_stream = None
        self.stop_reason = "end_turn"
        # Partes de la respuesta: (texto, índices de los documentos citados)
        self.blocks = [("Las integrales por partes ", [0]), ("sirven para productos.", [])]
        self.messages = SimpleNamespace(stream=self.stream)

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self)


@pytest.fixture
def claude():
    fake = FakeClaude()
    app.dependency_overrides[get_claude] = lambda: fake
    return fake


ANSWER = "Las integrales por partes sirven para productos."


def events(response) -> list[tuple[str, dict]]:
    """Eventos (nombre, datos) de una respuesta text/event-stream."""
    out = []
    for chunk in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.split("\n"))
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def ask(client, auth, group_id, text="Resume el material"):
    return client.post(f"/api/grupos/{group_id}/asistente", json={"contenido": text}, headers=auth)


def reply(response) -> dict:
    """La respuesta guardada: el evento «listo» del final."""
    assert response.status_code == 200, response.text
    name, data = events(response)[-1]
    assert name == "listo", data
    return data


def history(client, auth, group_id) -> list[dict]:
    return client.get(f"/api/grupos/{group_id}/asistente", headers=auth).json()


def notes(client, auth, group_id, name="apuntes.txt", text="Integrales por partes: u dv."):
    return upload(client, auth, group_id, name=name, content=text.encode())


def question_text(message) -> str:
    """Texto de un mensaje del usuario para Claude (el último bloque, después del material)."""
    return message["content"][-1]["text"]


# --- Responder con el material ---


def test_answers_using_only_the_group_material(client, users, group, claude):
    notes(client, users["ana"], group)

    answer = reply(ask(client, users["luis"], group))

    assert answer["es_asistente"] is True
    assert answer["contenido"] == ANSWER
    assert [f["nombre"] for f in answer["fuentes"]] == ["apuntes.txt"]
    assert answer["aviso"] is None

    call = claude.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert "Usa solo el material del grupo" in call["system"]
    assert "Cálculo II" in call["system"]
    # El material va como documento al inicio del primer mensaje, con citas y en caché
    document, question = call["messages"][0]["content"]
    assert document["type"] == "document"
    assert document["title"] == "apuntes.txt"
    assert document["source"]["data"] == "Integrales por partes: u dv."
    assert document["citations"] == {"enabled": True}
    assert document["cache_control"] == {"type": "ephemeral"}
    # La conversación es de cada uno: la pregunta va sin el nombre de quien la hace
    assert question["text"] == "Resume el material"


def test_the_answer_arrives_in_parts(client, users, group, claude):
    notes(client, users["ana"], group)

    response = ask(client, users["ana"], group)

    assert response.headers["content-type"].startswith("text/event-stream")
    assert events(response)[:2] == [
        ("texto", {"texto": "Las integrales por partes "}),
        ("texto", {"texto": "sirven para productos."}),
    ]
    assert events(response)[-1][0] == "listo"


def test_reads_pdfs(client, users, group, claude):
    upload(client, users["ana"], group, name="guia.pdf", content=make_pdf(["Series de Taylor"]))

    ask(client, users["ana"], group)

    document = claude.calls[0]["messages"][0]["content"][0]
    assert "Series de Taylor" in document["source"]["data"]


def test_reads_files_uploaded_before_text_extraction(client, users, group, claude, session_factory):
    upload(client, users["ana"], group, name="guia.pdf", content=make_pdf(["Regla de Cauchy"]))
    # Así quedaban los PDF subidos antes de que el asistente pudiera leerlos
    with session_factory() as db:
        db.query(Archivo).one().texto_extraido = None
        db.commit()

    ask(client, users["ana"], group)

    assert "Cauchy" in claude.calls[0]["messages"][0]["content"][0]["source"]["data"]
    with session_factory() as db:
        assert "Cauchy" in db.query(Archivo).one().texto_extraido


def test_scanned_pdfs_are_sent_as_pdf(client, users, group, claude):
    scanned = make_pdf(["", ""])
    upload(client, users["ana"], group, name="escaneado.pdf", content=scanned)

    answer = reply(ask(client, users["ana"], group))

    document = claude.calls[0]["messages"][0]["content"][0]
    assert document["title"] == "escaneado.pdf"
    assert document["source"]["type"] == "base64"
    assert document["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(document["source"]["data"]) == scanned
    assert document["citations"] == {"enabled": True}
    assert answer["aviso"] is None


def test_long_scanned_pdfs_are_cut_and_it_says_so(client, users, group, claude, monkeypatch):
    monkeypatch.setattr(asistente, "MAX_SCANNED_PAGES", 2)
    upload(client, users["ana"], group, name="escaneado.pdf", content=make_pdf([""] * 5))

    answer = reply(ask(client, users["ana"], group))

    data = claude.calls[0]["messages"][0]["content"][0]["source"]["data"]
    assert len(PdfReader(io.BytesIO(base64.b64decode(data))).pages) == 2
    assert answer["aviso"] == (
        "escaneado.pdf es un PDF escaneado de 5 páginas: solo leí las primeras 2."
    )


def test_damaged_pdfs_are_skipped(client, users, group, claude):
    upload(client, users["ana"], group, name="roto.pdf", content=b"%PDF-1.4 no es un PDF")

    answer = reply(ask(client, users["ana"], group))

    assert answer["contenido"] == asistente.NO_MATERIAL_REPLY
    assert claude.calls == []


def test_scanned_pages_count_for_the_material_limit(client, users, group, claude, monkeypatch):
    monkeypatch.setattr(asistente, "SCANNED_PAGE_CHARS", 30)
    monkeypatch.setattr(asistente, "MAX_MATERIAL_CHARS", 40)
    upload(client, users["ana"], group, name="escaneado.pdf", content=make_pdf(["", ""]))
    notes(client, users["ana"], group, name="nuevo.txt", text="b" * 30)

    answer = reply(ask(client, users["ana"], group))

    documents = claude.calls[0]["messages"][0]["content"][:-1]
    assert [d["title"] for d in documents] == ["nuevo.txt"]
    assert "no alcancé a leer: escaneado.pdf" in answer["aviso"]


def test_without_readable_material_answers_without_calling_claude(client, users, group, claude):
    upload(client, users["ana"], group, name="pizarra.png", content=b"\x89PNG")

    answer = reply(ask(client, users["ana"], group))

    assert answer["contenido"] == asistente.NO_MATERIAL_REPLY
    assert claude.calls == []


def test_leaves_out_material_over_the_limit_and_says_so(client, users, group, claude, monkeypatch):
    monkeypatch.setattr(asistente, "MAX_MATERIAL_CHARS", 40)
    notes(client, users["ana"], group, name="viejo.txt", text="a" * 30)
    notes(client, users["ana"], group, name="nuevo.txt", text="b" * 30)

    answer = reply(ask(client, users["ana"], group))

    # Se priorizan los más recientes
    documents = claude.calls[0]["messages"][0]["content"][:-1]
    assert [d["title"] for d in documents] == ["nuevo.txt"]
    # El aviso va aparte de la respuesta
    assert answer["contenido"] == ANSWER
    assert "no alcancé a leer: viejo.txt" in answer["aviso"]


def test_a_file_over_the_limit_is_read_from_the_start(client, users, group, claude, monkeypatch):
    monkeypatch.setattr(asistente, "MAX_MATERIAL_CHARS", 40)
    notes(client, users["ana"], group, name="libro.txt", text="a" * 30 + "b" * 30)

    answer = reply(ask(client, users["ana"], group))

    document = claude.calls[0]["messages"][0]["content"][0]
    assert document["title"] == "libro.txt"
    assert document["source"]["data"] == "a" * 30 + "b" * 10
    assert answer["aviso"] == "libro.txt es muy largo: solo alcancé a leer su comienzo."


def test_sources_are_the_cited_files_without_repeats(client, users, group, claude):
    notes(client, users["ana"], group, name="a.txt")
    notes(client, users["ana"], group, name="b.txt")
    claude.blocks = [("Primero ", [1]), ("y luego ", [0, 1]), ("fin.", [])]

    answer = reply(ask(client, users["ana"], group))

    assert answer["contenido"] == "Primero y luego fin."
    assert [f["nombre"] for f in answer["fuentes"]] == ["b.txt", "a.txt"]


def test_cut_answer_is_marked(client, users, group, claude):
    notes(client, users["ana"], group)
    claude.stop_reason = "max_tokens"

    assert "quedó cortada" in reply(ask(client, users["ana"], group))["contenido"]


def test_refusal_replaces_the_streamed_text(client, users, group, claude):
    notes(client, users["ana"], group)
    claude.stop_reason = "refusal"

    answer = reply(ask(client, users["ana"], group))

    assert answer["contenido"].startswith("No puedo ayudarte con eso")
    assert answer["fuentes"] == []


# --- Historial ---


def test_sends_previous_messages_as_context(client, users, group, claude):
    notes(client, users["ana"], group)
    ask(client, users["ana"], group, "¿Qué es u?")
    ask(client, users["ana"], group, "Dame un ejemplo")

    messages = claude.calls[1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert question_text(messages[0]) == "¿Qué es u?"
    assert messages[1]["content"] == [{"type": "text", "text": ANSWER}]
    assert question_text(messages[2]) == "Dame un ejemplo"
    # La conversación hasta la pregunta actual queda en caché para la próxima
    assert messages[2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in messages[1]["content"][-1]


def test_notices_are_not_sent_back_to_claude(client, users, group, claude, monkeypatch):
    monkeypatch.setattr(asistente, "MAX_MATERIAL_CHARS", 40)
    notes(client, users["ana"], group, name="viejo.txt", text="a" * 30)
    notes(client, users["ana"], group, name="nuevo.txt", text="b" * 30)
    ask(client, users["ana"], group)
    ask(client, users["ana"], group, "Sigue")

    assert claude.calls[1]["messages"][1]["content"][0]["text"] == ANSWER


def test_only_the_latest_messages_are_sent_as_context(client, users, group, claude, monkeypatch):
    monkeypatch.setattr(asistente, "HISTORY_MESSAGES", 2)
    notes(client, users["ana"], group)
    for text in ("Pregunta 1", "Pregunta 2", "Pregunta 3"):
        ask(client, users["ana"], group, text)

    messages = claude.calls[-1]["messages"]
    assert question_text(messages[0]) == "Pregunta 2"
    assert question_text(messages[-1]) == "Pregunta 3"


def test_history_is_cut_by_length(client, users, group, claude, monkeypatch):
    notes(client, users["ana"], group)
    ask(client, users["ana"], group, "Pregunta 1")
    ask(client, users["ana"], group, "Pregunta 2")
    # Cabe solo la última pregunta con su respuesta
    monkeypatch.setattr(asistente, "MAX_HISTORY_CHARS", len("Pregunta 2") + len(ANSWER))

    ask(client, users["ana"], group, "Pregunta 3")

    messages = claude.calls[-1]["messages"]
    assert [question_text(m) for m in messages if m["role"] == "user"] == [
        "Pregunta 2",
        "Pregunta 3",
    ]


def test_each_member_has_their_own_conversation(client, users, group, claude):
    notes(client, users["ana"], group)
    ask(client, users["ana"], group, "Arma un control")
    ask(client, users["luis"], group, "Resume la clase 5")

    seen_by_ana = history(client, users["ana"], group)
    seen_by_luis = history(client, users["luis"], group)

    assert [(m["autor"], m["contenido"]) for m in seen_by_luis] == [
        ("Luis Castro", "Resume la clase 5"),
        (None, ANSWER),
    ]
    assert seen_by_luis[1]["fuentes"][0]["nombre"] == "apuntes.txt"
    assert [m["contenido"] for m in seen_by_ana] == ["Arma un control", ANSWER]
    assert [m["es_mio"] for m in seen_by_ana] == [True, False]
    # Claude tampoco ve lo que preguntaron los demás
    luis_messages = claude.calls[1]["messages"]
    assert len(luis_messages) == 1
    assert question_text(luis_messages[0]) == "Resume la clase 5"


def test_consecutive_questions_are_joined_in_one_turn():
    history = [
        SimpleNamespace(es_asistente=False, contenido="Pregunta 1"),
        # La respuesta a la pregunta 1 no se guardó
        SimpleNamespace(es_asistente=False, contenido="Pregunta 2"),
        SimpleNamespace(es_asistente=True, contenido="Respuesta"),
    ]
    material = [asistente.Material(SimpleNamespace(nombre="a.txt"), text="material")]

    messages = asistente.build_messages(material, history, "Pregunta 3")

    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert question_text(messages[0]) == "Pregunta 1\n\nPregunta 2"


def test_each_member_clears_their_own_conversation(client, users, group, claude):
    notes(client, users["ana"], group)
    ask(client, users["ana"], group)
    ask(client, users["luis"], group)

    response = client.delete(f"/api/grupos/{group}/asistente", headers=users["luis"])

    assert response.status_code == 204
    assert history(client, users["luis"], group) == []
    assert len(history(client, users["ana"], group)) == 2


# --- Errores y límites ---


def test_only_members_can_use_the_assistant(client, users, group, claude):
    url = f"/api/grupos/{group}/asistente"
    assert ask(client, users["sofia"], group).status_code == 403
    assert client.get(url, headers=users["sofia"]).status_code == 403
    assert client.delete(url, headers=users["sofia"]).status_code == 403


def test_without_api_key_the_assistant_is_unavailable(client, users, group, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_claude.cache_clear()

    response = ask(client, users["ana"], group)

    assert response.status_code == 503
    assert "no está configurado" in response.json()["detail"]


def test_claude_error_does_not_save_the_question(client, users, group, claude, session_factory):
    notes(client, users["ana"], group)
    claude.error = api_error()

    response = ask(client, users["ana"], group)

    assert events(response) == [
        ("error", {"detail": "El asistente no está disponible en este momento. Intenta más tarde."})
    ]
    assert history(client, users["ana"], group) == []
    with session_factory() as db:
        assert db.query(MensajeAsistente).count() == 0


def test_error_in_the_middle_of_the_answer(client, users, group, claude):
    notes(client, users["ana"], group)
    claude.error_mid_stream = anthropic.APIConnectionError(
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )

    name, data = events(ask(client, users["ana"], group))[-1]

    assert name == "error"
    assert "No pudimos conectar" in data["detail"]
    assert history(client, users["ana"], group) == []


def test_rate_limit_from_claude(client, users, group, claude):
    notes(client, users["ana"], group)
    claude.error = anthropic.RateLimitError(
        "429",
        response=httpx.Response(
            429, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        ),
        body=None,
    )

    name, data = events(ask(client, users["ana"], group))[-1]

    assert name == "error"
    assert "muchas preguntas" in data["detail"]


def test_daily_question_limit(client, users, group, claude, monkeypatch):
    monkeypatch.setattr(asistente, "MAX_QUESTIONS_PER_DAY", 2)
    notes(client, users["ana"], group)
    ask(client, users["ana"], group)
    ask(client, users["ana"], group)

    response = ask(client, users["ana"], group)

    assert response.status_code == 429
    assert len(claude.calls) == 2


def test_failed_questions_do_not_count_for_the_limit(client, users, group, claude, monkeypatch):
    monkeypatch.setattr(asistente, "MAX_QUESTIONS_PER_DAY", 1)
    notes(client, users["ana"], group)
    claude.error = api_error()
    ask(client, users["ana"], group)
    claude.error = None

    assert reply(ask(client, users["ana"], group))["contenido"] == ANSWER


def test_questions_without_material_do_not_count_for_the_limit(
    client, users, group, claude, monkeypatch
):
    monkeypatch.setattr(asistente, "MAX_QUESTIONS_PER_DAY", 1)
    ask(client, users["ana"], group)
    notes(client, users["ana"], group)

    assert reply(ask(client, users["ana"], group))["contenido"] == ANSWER


def test_clearing_the_conversation_does_not_reset_the_daily_limit(
    client, users, group, claude, monkeypatch
):
    monkeypatch.setattr(asistente, "MAX_QUESTIONS_PER_DAY", 2)
    notes(client, users["ana"], group)
    ask(client, users["ana"], group)
    ask(client, users["ana"], group)

    client.delete(f"/api/grupos/{group}/asistente", headers=users["ana"])

    assert history(client, users["ana"], group) == []
    assert ask(client, users["ana"], group).status_code == 429


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
def test_rejects_empty_or_too_long_questions(client, users, group, claude, text):
    assert ask(client, users["ana"], group, text).status_code == 422
