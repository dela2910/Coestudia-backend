"""Solicitudes para unirse a un grupo: las que envió el usuario («mis solicitudes») y responder
las de sus grupos (solo el creador)."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MiembroGrupo, Solicitud, Usuario, now_utc
from app.routers.grupos import (
    ACTIVE_REQUEST_STATES,
    is_active,
    is_full,
    mine_out,
    summary_fields,
    sync_waitlist,
)
from app.schemas import GrupoMioOut, GrupoResumenOut, MiSolicitudOut
from app.security import current_user

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
    """Solicitud pendiente o en espera de un grupo vigente creado por el usuario, o el error
    que corresponda."""
    request = db.get(Solicitud, solicitud_id)
    # A quien no es el creador se le responde 404, para no revelar solicitudes ajenas
    if request is None or request.grupo.creador_id != user.id or not is_active(request.grupo):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esta solicitud no existe.")
    if request.estado not in ACTIVE_REQUEST_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esta solicitud ya no está pendiente.")
    return request


@router.post("/{solicitud_id}/aceptar", response_model=GrupoMioOut)
def accept_request(
    solicitud_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    """Suma a quien la envió como integrante. Si con eso se llena el cupo, las demás solicitudes
    quedan en espera hasta que se libere uno."""
    request = pending_of_my_group(db, solicitud_id, user)
    group = request.grupo
    if not group.abierto:
        raise HTTPException(status.HTTP_409_CONFLICT, "El grupo está cerrado.")
    if is_full(group):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El grupo ya no tiene cupos: la solicitud queda en espera hasta que se libere uno.",
        )

    request.estado = "aceptada"
    request.respondida_en = now_utc()
    group.miembros.append(MiembroGrupo(usuario_id=request.usuario_id))
    sync_waitlist(group)
    db.commit()
    db.refresh(group)
    return mine_out(group, user)


@router.post("/{solicitud_id}/rechazar", response_model=GrupoMioOut)
def reject_request(
    solicitud_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    request = pending_of_my_group(db, solicitud_id, user)
    request.estado = "rechazada"
    request.respondida_en = now_utc()
    db.commit()
    return mine_out(request.grupo, user)
