from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.claude import get_claude
from app.main import app
from app.models import Archivo
from app.routers import asistente
from tests import test_archivos
from tests.test_extract import make_pdf

# Mismos grupo y almacenamiento que en los tests de archivos
storage = test_archivos.storage
group = test_archivos.group
upload = test_archivos.upload


class FakeClaude:
    """Reemplaza al cliente de Anthropic: guarda lo que se le pide y responde lo configurado."""

    def __init__(self):
        self.calls = []
        self.error = None
        self.stop_reason = "end_turn"
        # Bloques de texto de la respuesta: (texto, índices de los documentos citados)
        self.blocks = [("Las integrales por partes sirven para productos.", [0])]
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        content = [
            SimpleNamespace(
                type="text",
                text=text,
                citations=[SimpleNamespace(type="char_location", document_index=i) for i in cited],
            )
            for text, cited in self.blocks
        ]
        return SimpleNamespace(
            content=content,
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=100, cache_read_input_tokens=0, output_tokens=20),
        )


@pytest.fixture
def claude():
    fake = FakeClaude()
    app.dependency_overrides[get_claude] = lambda: fake
    return fake


def ask(client, auth, group_id, text="Resume el material"):
    return client.post(f"/api/grupos/{group_id}/asistente", json={"contenido": text}, headers=auth)


def notes(client, auth, group_id, name="apuntes.txt", text="Integrales por partes: u dv."):
    return upload(client, auth, group_id, name=name, content=text.encode())


# --- Responder con el material ---


def test_answers_using_only_the_group_material(client, users, group, claude):
    notes(client, users["ana"], group)

    response = ask(client, users["luis"], group)

    assert response.status_code == 201, response.text
    reply = response.json()
    assert reply["es_asistente"] is True
    assert reply["contenido"] == "Las integrales por partes sirven para productos."
    assert [f["nombre"] for f in reply["fuentes"]] == ["apuntes.txt"]

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
    # Con el nombre de quien pregunta: la conversación es de todo el grupo
    assert question == {"type": "text", "text": "Luis Castro: Resume el material"}


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


def test_without_readable_material_answers_without_calling_claude(client, users, group, claude):
    upload(client, users["ana"], group, name="pizarra.png", content=b"\x89PNG")

    response = ask(client, users["ana"], group)

    assert response.status_code == 201
    assert response.json()["contenido"] == asistente.NO_MATERIAL_REPLY
    assert claude.calls == []


def test_sends_previous_messages_as_context(client, users, group, claude):
    notes(client, users["ana"], group)
    ask(client, users["ana"], group, "¿Qué es u?")
    ask(client, users["ana"], group, "Dame un ejemplo")

    messages = claude.calls[1]["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"][-1]["text"] == "Ana Rojas: ¿Qué es u?"
    assert messages[2]["content"] == "Ana Rojas: Dame un ejemplo"


def test_leaves_out_material_over_the_limit_and_says_so(client, users, group, claude, monkeypatch):
    monkeypatch.setattr(asistente, "MAX_MATERIAL_CHARS", 40)
    notes(client, users["ana"], group, name="viejo.txt", text="a" * 30)
    notes(client, users["ana"], group, name="nuevo.txt", text="b" * 30)

    reply = ask(client, users["ana"], group).json()

    # Se priorizan los más recientes
    documents = claude.calls[0]["messages"][0]["content"][:-1]
    assert [d["title"] for d in documents] == ["nuevo.txt"]
    assert "no alcancé a leer: viejo.txt" in reply["contenido"]


def test_sources_are_the_cited_files_without_repeats(client, users, group, claude):
    notes(client, users["ana"], group, name="a.txt")
    notes(client, users["ana"], group, name="b.txt")
    claude.blocks = [("Primero ", [1]), ("y luego ", [0, 1]), ("fin.", [])]

    reply = ask(client, users["ana"], group).json()

    assert reply["contenido"] == "Primero y luego fin."
    assert [f["nombre"] for f in reply["fuentes"]] == ["b.txt", "a.txt"]


def test_cut_answer_is_marked(client, users, group, claude):
    notes(client, users["ana"], group)
    claude.stop_reason = "max_tokens"

    assert "quedó cortada" in ask(client, users["ana"], group).json()["contenido"]


# --- Historial ---


def test_the_whole_group_shares_the_conversation(client, users, group, claude):
    notes(client, users["ana"], group)
    ask(client, users["ana"], group, "Arma un control")
    ask(client, users["luis"], group, "Corrige mis respuestas")

    seen_by_ana = client.get(f"/api/grupos/{group}/asistente", headers=users["ana"]).json()
    seen_by_luis = client.get(f"/api/grupos/{group}/asistente", headers=users["luis"]).json()

    assert [(m["autor"], m["contenido"]) for m in seen_by_luis] == [
        ("Ana Rojas", "Arma un control"),
        (None, "Las integrales por partes sirven para productos."),
        ("Luis Castro", "Corrige mis respuestas"),
        (None, "Las integrales por partes sirven para productos."),
    ]
    assert seen_by_luis[1]["fuentes"][0]["nombre"] == "apuntes.txt"
    # Cada uno ve marcadas como suyas solo sus preguntas
    assert [m["es_mio"] for m in seen_by_ana] == [True, False, False, False]
    assert [m["es_mio"] for m in seen_by_luis] == [False, False, True, False]


def test_claude_knows_who_asked_each_question(client, users, group, claude):
    notes(client, users["ana"], group)
    ask(client, users["ana"], group, "Arma un control")
    ask(client, users["luis"], group, "Corrige mis respuestas")

    messages = claude.calls[1]["messages"]
    assert messages[0]["content"][-1]["text"] == "Ana Rojas: Arma un control"
    assert messages[2]["content"] == "Luis Castro: Corrige mis respuestas"
    assert "Varios integrantes del grupo comparten" in claude.calls[1]["system"]


def test_consecutive_questions_are_joined_in_one_turn():
    ana = SimpleNamespace(nombre="Ana")
    luis = SimpleNamespace(nombre="Luis")
    history = [
        SimpleNamespace(es_asistente=False, usuario=ana, contenido="Pregunta 1"),
        # Luis preguntó mientras el asistente respondía a Ana
        SimpleNamespace(es_asistente=False, usuario=luis, contenido="Pregunta 2"),
        SimpleNamespace(es_asistente=True, usuario=ana, contenido="Respuesta"),
    ]
    file = SimpleNamespace(nombre="a.txt", texto_extraido="material")

    messages = asistente.build_messages([file], history, ana, "Pregunta 3")

    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"][-1]["text"] == "Ana: Pregunta 1\n\nLuis: Pregunta 2"


def test_only_the_creator_clears_the_conversation(client, users, group, claude):
    notes(client, users["ana"], group)
    ask(client, users["luis"], group)

    forbidden = client.delete(f"/api/grupos/{group}/asistente", headers=users["luis"])
    response = client.delete(f"/api/grupos/{group}/asistente", headers=users["ana"])

    assert forbidden.status_code == 403
    assert response.status_code == 204
    assert client.get(f"/api/grupos/{group}/asistente", headers=users["luis"]).json() == []


# --- Errores y límites ---


def test_only_members_can_use_the_assistant(client, users, group, claude):
    assert ask(client, users["sofia"], group).status_code == 403
    assert client.get(f"/api/grupos/{group}/asistente", headers=users["sofia"]).status_code == 403


def test_without_api_key_the_assistant_is_unavailable(client, users, group, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_claude.cache_clear()

    response = ask(client, users["ana"], group)

    assert response.status_code == 503
    assert "no está configurado" in response.json()["detail"]


def test_claude_error_does_not_save_the_question(client, users, group, claude):
    notes(client, users["ana"], group)
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    claude.error = anthropic.APIStatusError(
        "Sin saldo", response=httpx.Response(400, request=request), body=None
    )

    response = ask(client, users["ana"], group)

    assert response.status_code == 503
    assert client.get(f"/api/grupos/{group}/asistente", headers=users["ana"]).json() == []


def test_daily_question_limit(client, users, group, claude, monkeypatch):
    monkeypatch.setattr(asistente, "MAX_QUESTIONS_PER_DAY", 2)
    notes(client, users["ana"], group)
    ask(client, users["ana"], group)
    ask(client, users["ana"], group)

    response = ask(client, users["ana"], group)

    assert response.status_code == 429
    assert len(claude.calls) == 2


@pytest.mark.parametrize("text", ["", "   ", "x" * 2001])
def test_rejects_empty_or_too_long_questions(client, users, group, claude, text):
    assert ask(client, users["ana"], group, text).status_code == 422
