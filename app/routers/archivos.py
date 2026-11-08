"""Archivos compartidos dentro de un grupo (CU12, RF19). Solo los integrantes los ven, suben y
descargan; los borra quien lo subió o el creador del grupo."""

import logging
import mimetypes
import uuid
from pathlib import PurePath
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Archivo, GrupoEstudio, Usuario
from app.routers.grupos import is_active, visible_group
from app.schemas import ArchivoOut
from app.security import current_user
from app.storage import MissingFileError, get_storage

logger = logging.getLogger("coestudia.archivos")

router = APIRouter(tags=["archivos"])

MAX_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_GROUP = 100

# Apuntes, guías y pruebas: documentos, planillas, presentaciones, texto e imágenes. Sin HTML ni
# SVG, que el navegador podría ejecutar.
ALLOWED_EXTENSIONS = {
    "pdf", "doc", "docx", "odt", "rtf",
    "ppt", "pptx", "odp",
    "xls", "xlsx", "ods", "csv",
    "txt", "md",
    "png", "jpg", "jpeg", "gif", "webp", "heic",
}  # fmt: skip
# Se guarda su texto para que lo lea el asistente (B13)
TEXT_EXTENSIONS = {"txt", "md", "csv"}


def extension(name: str) -> str:
    return PurePath(name).suffix.lower().removeprefix(".")


def member_group(db: Session, grupo_id: int, user: Usuario) -> GrupoEstudio:
    """Grupo vigente donde el usuario es integrante, o el error que corresponda."""
    group = visible_group(db, grupo_id, user)
    if not any(m.usuario_id == user.id for m in group.miembros):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Solo los integrantes del grupo pueden ver sus archivos."
        )
    return group


def member_file(db: Session, archivo_id: int, user: Usuario) -> Archivo:
    """Archivo de un grupo vigente donde el usuario es integrante, o 404."""
    file = db.get(Archivo, archivo_id)
    # A quien no es integrante se le responde 404, para no revelar archivos ajenos
    if (
        file is None
        or not is_active(file.grupo)
        or not any(m.usuario_id == user.id for m in file.grupo.miembros)
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Este archivo no existe.")
    return file


def file_out(file: Archivo, user: Usuario) -> ArchivoOut:
    return ArchivoOut(
        id=file.id,
        nombre=file.nombre,
        tipo_mime=file.tipo_mime,
        tamano_bytes=file.tamano_bytes,
        subido_por=file.subido_por.nombre,
        creado_en=file.creado_en,
        puede_eliminar=user.id in (file.subido_por_id, file.grupo.creador_id),
    )


@router.get("/api/grupos/{grupo_id}/archivos", response_model=list[ArchivoOut])
def list_files(grupo_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)):
    """Archivos del grupo, los más recientes primero."""
    group = member_group(db, grupo_id, user)
    files = sorted(group.archivos, key=lambda f: (f.creado_en, f.id), reverse=True)
    return [file_out(f, user) for f in files]


@router.post(
    "/api/grupos/{grupo_id}/archivos",
    response_model=ArchivoOut,
    status_code=status.HTTP_201_CREATED,
)
def upload_file(
    grupo_id: int,
    archivo: UploadFile = File(),
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    """Sube un archivo de hasta 10 MB al grupo."""
    group = member_group(db, grupo_id, user)
    # Algunos navegadores mandan la ruta completa: se deja solo el nombre
    name = PurePath((archivo.filename or "").replace("\\", "/")).name.strip()[:255]
    ext = extension(name)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Ese tipo de archivo no se puede subir. Sube PDF, Word, PowerPoint, Excel, texto "
            "o imágenes.",
        )
    if len(group.archivos) >= MAX_FILES_PER_GROUP:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"El grupo ya tiene {MAX_FILES_PER_GROUP} archivos. Elimina alguno para subir otro.",
        )
    # Se lee un byte más del máximo para saber si se pasa sin cargar archivos enormes
    data = archivo.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "El archivo pesa más de 10 MB.")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El archivo está vacío.")

    # El tipo se deduce de la extensión, no del que manda el navegador
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    key = f"grupos/{group.id}/{uuid.uuid4().hex}.{ext}"
    storage.save(key, data, mime)
    file = Archivo(
        grupo=group,
        subido_por=user,
        nombre=name,
        tipo_mime=mime,
        tamano_bytes=len(data),
        clave_almacenamiento=key,
        texto_extraido=data.decode("utf-8", errors="replace") if ext in TEXT_EXTENSIONS else None,
    )
    db.add(file)
    try:
        db.commit()
    except Exception:
        db.rollback()
        # Sin la fila en la base el archivo quedaría huérfano en el almacenamiento
        storage.delete(key)
        raise
    return file_out(file, user)


@router.get("/api/archivos/{archivo_id}/descarga")
def download_file(
    archivo_id: int,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    """Contenido del archivo. Pasa por la API para revisar que el usuario sea integrante."""
    file = member_file(db, archivo_id, user)
    try:
        content = storage.read(file.clave_almacenamiento)
    except MissingFileError:
        logger.error("Falta %s en el almacenamiento", file.clave_almacenamiento)
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No encontramos el contenido de este archivo. Pídele a quien lo subió que lo suba "
            "de nuevo.",
        )
    return Response(
        content=content,
        media_type=file.tipo_mime,
        headers={
            # Siempre como descarga, nunca abierto en el dominio de la API
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.nombre)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/api/archivos/{archivo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(
    archivo_id: int,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
    storage=Depends(get_storage),
):
    """Lo borra quien lo subió o el creador del grupo."""
    file = member_file(db, archivo_id, user)
    if user.id not in (file.subido_por_id, file.grupo.creador_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Solo quien subió el archivo o el creador del grupo pueden eliminarlo.",
        )
    key = file.clave_almacenamiento
    db.delete(file)
    db.commit()
    try:
        storage.delete(key)
    except Exception:
        # El archivo ya no aparece en el grupo; si quedó en el almacenamiento solo ocupa espacio
        logger.exception("No se pudo borrar %s del almacenamiento", key)
