from datetime import timedelta

import pytest

from app.main import app
from app.models import Archivo, GrupoEstudio, now_utc, today_in_chile
from app.routers import archivos
from app.storage import LocalStorage, get_storage
from tests.test_solicitudes import answer, ask, create_group, pending_ids


@pytest.fixture
def storage(tmp_path):
    """Almacenamiento en una carpeta temporal por test."""
    local = LocalStorage(tmp_path / "uploads")
    app.dependency_overrides[get_storage] = lambda: local
    return local


@pytest.fixture
def group(client, users, storage):
    """Grupo de Ana con Luis como integrante. Sofía no es integrante."""
    group_id = create_group(client, users["ana"])
    ask(client, users["luis"], group_id)
    answer(client, users["ana"], pending_ids(client, users["ana"])[0], "aceptar")
    return group_id


def upload(client, auth, group_id, name="Guía integrales.pdf", content=b"%PDF-1.4 apuntes"):
    return client.post(
        f"/api/grupos/{group_id}/archivos",
        files={"archivo": (name, content, "application/pdf")},
        headers=auth,
    )


def list_files(client, auth, group_id):
    return client.get(f"/api/grupos/{group_id}/archivos", headers=auth)


# --- Subir y listar ---


def test_member_uploads_and_everyone_in_the_group_sees_it(client, users, group, storage):
    response = upload(client, users["luis"], group)

    assert response.status_code == 201, response.text
    file = response.json()
    assert file["nombre"] == "Guía integrales.pdf"
    assert file["tipo_mime"] == "application/pdf"
    assert file["tamano_bytes"] == len(b"%PDF-1.4 apuntes")
    assert file["subido_por"] == "Luis Castro"
    assert file["puede_eliminar"] is True

    listed = list_files(client, users["ana"], group)
    assert listed.status_code == 200
    assert [f["nombre"] for f in listed.json()] == ["Guía integrales.pdf"]
    # La creadora también puede eliminarlo
    assert listed.json()[0]["puede_eliminar"] is True


def test_list_shows_newest_first_and_who_can_delete(client, users, group):
    upload(client, users["ana"], group, name="Primero.pdf")
    upload(client, users["luis"], group, name="Segundo.pdf")

    files = list_files(client, users["luis"], group).json()

    assert [f["nombre"] for f in files] == ["Segundo.pdf", "Primero.pdf"]
    # Luis no es el creador: solo puede eliminar lo que subió
    assert [f["puede_eliminar"] for f in files] == [True, False]


def test_upload_keeps_only_the_file_name(client, users, group):
    response = upload(client, users["ana"], group, name="C:\\Users\\ana\\Apuntes.docx")

    assert response.json()["nombre"] == "Apuntes.docx"
    assert response.json()["tipo_mime"].endswith("wordprocessingml.document")


def test_text_files_save_their_text_for_the_assistant(client, users, group, session_factory):
    upload(client, users["ana"], group, name="resumen.md", content="# Series\nConvergen".encode())
    upload(client, users["ana"], group, name="guia.pdf")

    with session_factory() as db:
        texts = {f.nombre: f.texto_extraido for f in db.query(Archivo)}
    assert texts == {"resumen.md": "# Series\nConvergen", "guia.pdf": None}


@pytest.mark.parametrize("name", ["pagina.html", "dibujo.svg", "script.exe", "sin_extension"])
def test_rejects_file_types_not_allowed(client, users, group, name):
    response = upload(client, users["ana"], group, name=name)

    assert response.status_code == 422
    assert "tipo de archivo" in response.json()["detail"]


def test_rejects_files_over_the_limit(client, users, group, monkeypatch):
    monkeypatch.setattr(archivos, "MAX_BYTES", 10)

    response = upload(client, users["ana"], group, content=b"x" * 11)

    assert response.status_code == 413
    assert list_files(client, users["ana"], group).json() == []


def test_rejects_empty_files(client, users, group):
    response = upload(client, users["ana"], group, content=b"")
    assert response.status_code == 422


def test_rejects_uploads_when_the_group_is_full_of_files(client, users, group, monkeypatch):
    monkeypatch.setattr(archivos, "MAX_FILES_PER_GROUP", 1)
    upload(client, users["ana"], group)

    response = upload(client, users["ana"], group, name="otro.pdf")

    assert response.status_code == 409


def test_failed_save_does_not_leave_the_file_in_storage(client, users, group, storage, monkeypatch):
    def fail_commit(self):
        raise RuntimeError("se cayó la base")

    monkeypatch.setattr("sqlalchemy.orm.Session.commit", fail_commit)

    with pytest.raises(RuntimeError):
        upload(client, users["ana"], group)

    assert not any(path.is_file() for path in storage.root.rglob("*"))


# --- Permisos ---


def test_non_members_cannot_list_or_upload(client, users, group):
    assert list_files(client, users["sofia"], group).status_code == 403
    assert upload(client, users["sofia"], group).status_code == 403


def test_files_require_session(client, group):
    assert client.get(f"/api/grupos/{group}/archivos").status_code == 401


def test_unknown_or_expired_group_is_404(client, users, group, session_factory):
    assert list_files(client, users["ana"], 999).status_code == 404

    with session_factory() as db:
        db.get(GrupoEstudio, group).vigente_hasta = today_in_chile() - timedelta(days=1)
        db.commit()
    assert list_files(client, users["ana"], group).status_code == 404


# --- Descargar ---


def test_member_downloads_the_file(client, users, group):
    file_id = upload(client, users["ana"], group, name="Guía ñandú.pdf").json()["id"]

    response = client.get(f"/api/archivos/{file_id}/descarga", headers=users["luis"])

    assert response.status_code == 200
    assert response.content == b"%PDF-1.4 apuntes"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        "attachment; filename*=UTF-8''Gu%C3%ADa%20%C3%B1and%C3%BA.pdf"
    )
    assert response.headers["x-content-type-options"] == "nosniff"


def test_download_of_a_file_missing_in_storage_is_404(client, users, group, storage):
    file_id = upload(client, users["ana"], group).json()["id"]
    for path in storage.root.rglob("*.pdf"):
        path.unlink()

    response = client.get(f"/api/archivos/{file_id}/descarga", headers=users["ana"])

    assert response.status_code == 404
    assert "suba de nuevo" in response.json()["detail"]


def test_non_members_cannot_download(client, users, group):
    file_id = upload(client, users["ana"], group).json()["id"]

    response = client.get(f"/api/archivos/{file_id}/descarga", headers=users["sofia"])
    assert response.status_code == 404
    assert client.get("/api/archivos/999/descarga", headers=users["ana"]).status_code == 404


def test_files_of_deleted_groups_cannot_be_downloaded(client, users, group, session_factory):
    file_id = upload(client, users["ana"], group).json()["id"]
    with session_factory() as db:
        deleted = db.get(GrupoEstudio, group)
        deleted.eliminado_en = now_utc()
        deleted.eliminado_por_vigencia = False
        db.commit()

    assert client.get(f"/api/archivos/{file_id}/descarga", headers=users["ana"]).status_code == 404


# --- Eliminar ---


def test_uploader_deletes_the_file(client, users, group, storage):
    file_id = upload(client, users["luis"], group).json()["id"]

    response = client.delete(f"/api/archivos/{file_id}", headers=users["luis"])

    assert response.status_code == 204
    assert list_files(client, users["ana"], group).json() == []
    assert not any(path.is_file() for path in storage.root.rglob("*"))


def test_group_creator_deletes_any_file(client, users, group):
    file_id = upload(client, users["luis"], group).json()["id"]

    assert client.delete(f"/api/archivos/{file_id}", headers=users["ana"]).status_code == 204


def test_other_members_cannot_delete(client, users, group):
    file_id = upload(client, users["ana"], group).json()["id"]

    response = client.delete(f"/api/archivos/{file_id}", headers=users["luis"])

    assert response.status_code == 403
    assert len(list_files(client, users["ana"], group).json()) == 1


def test_non_members_cannot_delete(client, users, group):
    file_id = upload(client, users["ana"], group).json()["id"]
    assert client.delete(f"/api/archivos/{file_id}", headers=users["sofia"]).status_code == 404


def test_delete_works_even_if_storage_fails(client, users, group, storage, monkeypatch):
    file_id = upload(client, users["ana"], group).json()["id"]

    def fail(key):
        raise OSError("sin conexión")

    monkeypatch.setattr(storage, "delete", fail)

    assert client.delete(f"/api/archivos/{file_id}", headers=users["ana"]).status_code == 204
    assert list_files(client, users["ana"], group).json() == []


# --- Almacenamiento ---


def test_local_storage_rejects_keys_outside_its_folder(tmp_path):
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).read("../fuera.txt")


def test_storage_uses_local_folder_without_b2(monkeypatch, tmp_path):
    monkeypatch.delenv("B2_BUCKET", raising=False)
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    get_storage.cache_clear()
    try:
        storage = get_storage()
        assert isinstance(storage, LocalStorage)
        assert storage.root == tmp_path
    finally:
        get_storage.cache_clear()
