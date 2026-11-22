"""Invitaciones a un grupo: el creador invita por correo a un estudiante de su universidad y el
invitado acepta o rechaza. Al invitar, se avisa por correo a quien recibe la invitación."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.mailer import send_invitation_email
from app.models import Invitacion, MiembroGrupo, Usuario, now_utc
from app.routers.grupos import (
    active_request,
    is_active,
    is_full,
    membership,
    mine_out,
    own_group,
    summary_fields,
    sync_waitlist,
)
from app.routers.solicitudes import lock_group
from app.schemas import GrupoMioOut, GrupoResumenOut, InvitacionIn, MiInvitacionOut
from app.security import current_user

logger = logging.getLogger("coestudia.invitaciones")

router = APIRouter(tags=["invitaciones"])


def notify_invitation(email: str, name: str, inviter: str, subject: str) -> None:
    # Si el correo falla la invitación igual queda guardada: se ve en «Mis solicitudes»
    try:
        send_invitation_email(email, name, inviter, subject)
    except Exception:
        logger.exception("No se pudo avisar por correo la invitación a %s", email)


def pending_invitation(group, user_id: int) -> Invitacion | None:
    return next(
        (i for i in group.invitaciones if i.usuario_id == user_id and i.estado == "pendiente"),
        None,
    )


@router.post(
    "/api/grupos/{grupo_id}/invitaciones",
    response_model=GrupoMioOut,
    status_code=status.HTTP_201_CREATED,
)
def invite(
    grupo_id: int,
    data: InvitacionIn,
    tasks: BackgroundTasks,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Invita a un estudiante de la misma universidad (solo el creador). Se puede invitar aunque
    el grupo esté cerrado a solicitudes, pero no si está lleno."""
    group = own_group(db, grupo_id, user)
    guest = db.scalar(
        select(Usuario).where(
            Usuario.email == data.email.lower(),
            Usuario.universidad == user.universidad,
            Usuario.email_verificado_en.is_not(None),
            Usuario.eliminado_en.is_(None),
        )
    )
    if guest is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No hay una cuenta verificada de tu universidad con ese correo.",
        )
    if membership(group, guest.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esa persona ya es integrante del grupo.")
    if pending_invitation(group, guest.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya invitaste a esa persona a este grupo.")
    if active_request(group, guest.id) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Esa persona ya pidió unirse: acepta su solicitud en este mismo grupo.",
        )
    if is_full(group):
        raise HTTPException(status.HTTP_409_CONFLICT, "El grupo está lleno.")

    group.invitaciones.append(Invitacion(usuario=guest))
    try:
        db.commit()
    except IntegrityError:
        # Se envió dos veces al mismo tiempo (índice uq_invitacion_pendiente)
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya invitaste a esa persona a este grupo.")
    # El correo se envía después de responder, para que el creador no espere a Brevo
    subject = f"{group.asignatura.sigla} {group.asignatura.nombre}"
    tasks.add_task(notify_invitation, guest.email, guest.nombre, user.nombre, subject)
    return mine_out(group, user)


@router.delete("/api/grupos/{grupo_id}/invitaciones/{invitacion_id}", response_model=GrupoMioOut)
def cancel_invitation(
    grupo_id: int,
    invitacion_id: int,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Retira una invitación que aún no responden (solo el creador)."""
    group = own_group(db, grupo_id, user)
    invitation = next((i for i in group.invitaciones if i.id == invitacion_id), None)
    if invitation is None or invitation.estado != "pendiente":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esta invitación ya no está pendiente.")
    invitation.estado = "cancelada"
    invitation.cancelada_en = now_utc()
    db.commit()
    return mine_out(group, user)


@router.get("/api/invitaciones/mias", response_model=list[MiInvitacionOut])
def my_invitations(user: Usuario = Depends(current_user)):
    """Invitaciones pendientes que recibió el usuario de grupos vigentes, las más recientes
    primero."""
    invitations = sorted(
        (i for i in user.invitaciones if i.estado == "pendiente" and is_active(i.grupo)),
        key=lambda i: (i.creada_en, i.id),
        reverse=True,
    )
    return [
        MiInvitacionOut(
            id=i.id,
            invitado_por=i.grupo.creador.nombre,
            creada_en=i.creada_en,
            grupo=GrupoResumenOut(**summary_fields(i.grupo, len(i.grupo.miembros))),
        )
        for i in invitations
    ]


def my_pending_invitation(db: Session, invitacion_id: int, user: Usuario) -> Invitacion:
    """Invitación pendiente que recibió el usuario de un grupo vigente, o el error que
    corresponda."""
    invitation = db.get(Invitacion, invitacion_id)
    # A quien no es el invitado se le responde 404, para no revelar invitaciones ajenas
    if invitation is None or invitation.usuario_id != user.id or not is_active(invitation.grupo):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esta invitación no existe.")
    if invitation.estado != "pendiente":
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta invitación ya no está pendiente.")
    return invitation


@router.post("/api/invitaciones/{invitacion_id}/aceptar", response_model=GrupoMioOut)
def accept_invitation(
    invitacion_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    """El invitado entra al grupo. Si con eso se llena el cupo, las solicitudes pendientes quedan
    en espera hasta que se libere uno."""
    invitation = my_pending_invitation(db, invitacion_id, user)
    group = invitation.grupo
    # Igual que al aceptar una solicitud: dos ingresos a la vez no pueden pasar del cupo
    lock_group(db, group)
    if invitation.estado != "pendiente":
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta invitación ya no está pendiente.")
    if membership(group, user.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya eres integrante de este grupo.")
    if is_full(group):
        # La invitación sigue pendiente: se puede aceptar cuando se libere un cupo
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El grupo ya no tiene cupos. Puedes aceptar la invitación cuando se libere uno.",
        )

    invitation.estado = "aceptada"
    invitation.respondida_en = now_utc()
    group.miembros.append(MiembroGrupo(usuario_id=user.id))
    sync_waitlist(group)
    db.commit()
    db.refresh(group)
    return mine_out(group, user)


@router.post("/api/invitaciones/{invitacion_id}/rechazar", status_code=status.HTTP_204_NO_CONTENT)
def reject_invitation(
    invitacion_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    invitation = my_pending_invitation(db, invitacion_id, user)
    invitation.estado = "rechazada"
    invitation.respondida_en = now_utc()
    db.commit()
