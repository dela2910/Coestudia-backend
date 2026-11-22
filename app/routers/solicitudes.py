"""Solicitudes para unirse a un grupo: las que envió el usuario («mis solicitudes») y responder
las de sus grupos (solo el creador). Al responder, se avisa por correo a quien la envió."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.mailer import send_request_answered_email
from app.models import GrupoEstudio, MiembroGrupo, Solicitud, Usuario, now_utc
from app.routers.grupos import (
    is_active,
    is_full,
    mine_out,
    notify_full,
    reject_if_full,
    summary_fields,
)
from app.schemas import GrupoMioOut, GrupoResumenOut, MiSolicitudOut
from app.security import current_user

logger = logging.getLogger("coestudia.solicitudes")

router = APIRouter(prefix="/api/solicitudes", tags=["solicitudes"])


@router.get("/mias", response_model=list[MiSolicitudOut])
def my_requests(user: Usuario = Depends(current_user)):
    """Solicitudes que envió el usuario a grupos vigentes, las más recientes primero. Las que
    canceló no aparecen."""
    requests = sorted(
        (s for s in user.solicitudes if s.estado != "cancelada" and is_active(s.grupo)),
        key=lambda s: (s.creada_en, s.id),
        reverse=True,
    )
    return [
        MiSolicitudOut(
            id=s.id,
            estado=s.estado,
            mensaje=s.mensaje,
            creada_en=s.creada_en,
            respondida_en=s.respondida_en,
            grupo=GrupoResumenOut(**summary_fields(s.grupo, len(s.grupo.miembros))),
        )
        for s in requests
    ]


def pending_of_my_group(db: Session, solicitud_id: int, user: Usuario) -> Solicitud:
    """Solicitud pendiente de un grupo vigente creado por el usuario, o el error que
    corresponda."""
    request = db.get(Solicitud, solicitud_id)
    # A quien no es el creador se le responde 404, para no revelar solicitudes ajenas
    if request is None or request.grupo.creador_id != user.id or not is_active(request.grupo):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esta solicitud no existe.")
    if request.estado != "pendiente":
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta solicitud ya no está pendiente.")
    return request


def lock_group(db: Session, group: GrupoEstudio) -> None:
    """Bloquea la fila del grupo hasta el commit (en PostgreSQL) y vuelve a leer todo desde la
    base. Si el creador acepta dos solicitudes a la vez (dos pestañas o doble clic), la segunda
    espera a que se guarde la primera y ve el cupo ya ocupado: así no se pasa del cupo."""
    db.execute(select(GrupoEstudio.id).where(GrupoEstudio.id == group.id).with_for_update())
    db.expire_all()


def notify_answer(email: str, name: str, subject: str, accepted: bool) -> None:
    # Si el correo falla la respuesta igual queda guardada: se ve en «Mis solicitudes»
    try:
        send_request_answered_email(email, name, subject, accepted)
    except Exception:
        logger.exception("No se pudo avisar por correo la respuesta de la solicitud a %s", email)


def schedule_notification(tasks: BackgroundTasks, request: Solicitud, accepted: bool) -> None:
    """El correo se envía después de responder al creador, para que no espere a Brevo. Se copian
    los datos ahora porque la sesión de la base ya estará cerrada."""
    applicant = request.usuario
    if applicant.eliminado_en is not None:
        return
    subject = f"{request.grupo.asignatura.sigla} {request.grupo.asignatura.nombre}"
    tasks.add_task(notify_answer, applicant.email, applicant.nombre, subject, accepted)


@router.post("/{solicitud_id}/aceptar", response_model=GrupoMioOut)
def accept_request(
    solicitud_id: int,
    tasks: BackgroundTasks,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Suma a quien la envió como integrante. Si con eso se llena el cupo, las demás solicitudes
    pendientes se rechazan solas y se les avisa por correo."""
    request = pending_of_my_group(db, solicitud_id, user)
    group = request.grupo
    lock_group(db, group)
    if request.estado != "pendiente":
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta solicitud ya no está pendiente.")
    if not group.abierto:
        raise HTTPException(status.HTTP_409_CONFLICT, "El grupo está cerrado.")
    if is_full(group):
        raise HTTPException(status.HTTP_409_CONFLICT, "El grupo ya no tiene cupos.")

    request.estado = "aceptada"
    request.respondida_en = now_utc()
    group.miembros.append(MiembroGrupo(usuario_id=request.usuario_id))
    rejected = reject_if_full(group)
    db.commit()
    schedule_notification(tasks, request, accepted=True)
    notify_full(tasks, rejected)
    db.refresh(group)
    return mine_out(group, user)


@router.post("/{solicitud_id}/rechazar", response_model=GrupoMioOut)
def reject_request(
    solicitud_id: int,
    tasks: BackgroundTasks,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    request = pending_of_my_group(db, solicitud_id, user)
    request.estado = "rechazada"
    request.respondida_en = now_utc()
    db.commit()
    schedule_notification(tasks, request, accepted=False)
    return mine_out(request.grupo, user)
