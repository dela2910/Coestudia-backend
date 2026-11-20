"""Foro del grupo (B16): temas con su pregunta, por ejemplo «Guía 2 – Ejercicio 3», y respuestas
debajo en orden. Solo los integrantes leen y escriben.

No hay consultas periódicas: el frontend carga el foro al abrirlo y con el botón «Actualizar», así
la base de datos solo trabaja cuando alguien lo usa.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import GrupoEstudio, RespuestaForo, TemaForo, Usuario, now_utc
from app.routers.grupos import is_active, membership, visible_group
from app.schemas import (
    RespuestaForoIn,
    RespuestaForoOut,
    TemaForoIn,
    TemaForoOut,
    TemaResumenOut,
)
from app.security import as_utc, current_user

router = APIRouter(tags=["foro"])


def forum_group(db: Session, grupo_id: int, user: Usuario) -> GrupoEstudio:
    """Grupo vigente donde el usuario es integrante, o el error que corresponda."""
    group = visible_group(db, grupo_id, user)
    if membership(group, user.id) is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Solo los integrantes del grupo pueden ver su foro."
        )
    return group


def member_topic(db: Session, tema_id: int, user: Usuario) -> TemaForo:
    """Tema de un grupo vigente donde el usuario es integrante, o 404."""
    topic = db.get(TemaForo, tema_id)
    # A quien no es integrante se le responde 404, para no revelar temas ajenos
    if topic is None or not is_active(topic.grupo) or membership(topic.grupo, user.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Este tema no existe.")
    return topic


# SQLite devuelve las fechas sin zona horaria: se responden en UTC para que el navegador muestre
# la hora local correcta


def reply_out(reply: RespuestaForo, user: Usuario) -> RespuestaForoOut:
    return RespuestaForoOut(
        id=reply.id,
        autor=reply.autor.nombre,
        es_mia=reply.autor_id == user.id,
        contenido=reply.contenido,
        creado_en=as_utc(reply.creado_en),
    )


def topic_out(topic: TemaForo, user: Usuario) -> TemaForoOut:
    return TemaForoOut(
        id=topic.id,
        titulo=topic.titulo,
        autor=topic.autor.nombre,
        es_mio=topic.autor_id == user.id,
        contenido=topic.contenido,
        creado_en=as_utc(topic.creado_en),
        respuestas=[reply_out(r, user) for r in topic.respuestas],
    )


@router.get("/api/grupos/{grupo_id}/foro", response_model=list[TemaResumenOut])
def list_topics(
    grupo_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    """Temas del grupo, primero los que tuvieron actividad más reciente."""
    group = forum_group(db, grupo_id, user)
    topics = sorted(
        group.temas_foro, key=lambda t: (as_utc(t.ultima_actividad_en), t.id), reverse=True
    )
    return [
        TemaResumenOut(
            id=t.id,
            titulo=t.titulo,
            autor=t.autor.nombre,
            creado_en=as_utc(t.creado_en),
            ultima_actividad_en=as_utc(t.ultima_actividad_en),
            total_respuestas=len(t.respuestas),
        )
        for t in topics
    ]


@router.post(
    "/api/grupos/{grupo_id}/foro", response_model=TemaForoOut, status_code=status.HTTP_201_CREATED
)
def create_topic(
    grupo_id: int,
    data: TemaForoIn,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Abre un tema con un título y la pregunta o el enunciado."""
    group = forum_group(db, grupo_id, user)
    topic = TemaForo(grupo=group, autor=user, titulo=data.titulo, contenido=data.contenido)
    db.add(topic)
    db.commit()
    return topic_out(topic, user)


@router.get("/api/foro/{tema_id}", response_model=TemaForoOut)
def get_topic(tema_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)):
    """El tema con todas sus respuestas, de la más antigua a la más nueva."""
    return topic_out(member_topic(db, tema_id, user), user)


@router.post(
    "/api/foro/{tema_id}/respuestas",
    response_model=RespuestaForoOut,
    status_code=status.HTTP_201_CREATED,
)
def reply_topic(
    tema_id: int,
    data: RespuestaForoIn,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Responde un tema. El tema sube al principio de la lista del foro."""
    topic = member_topic(db, tema_id, user)
    reply = RespuestaForo(tema=topic, autor=user, contenido=data.contenido)
    db.add(reply)
    topic.ultima_actividad_en = now_utc()
    db.commit()
    return reply_out(reply, user)
