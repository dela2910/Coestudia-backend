"""Notificaciones dentro de la app (la campana del menú).

No tienen tabla propia: se arman con las solicitudes y las invitaciones que ya existen, así que
no hay que acordarse de crearlas en cada endpoint. Lo único que se guarda es cuándo el usuario
abrió las notificaciones por última vez (usuario.avisos_vistos_en), para saber cuáles son nuevas.

- Pendientes (solicitudes a sus grupos e invitaciones que recibió): se muestran mientras sigan
  pendientes, porque piden que el usuario haga algo.
- Respondidas (sus solicitudes y las invitaciones que mandó): se muestran los últimos 30 días.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import GrupoEstudio, Invitacion, Solicitud, Usuario, now_utc
from app.routers.grupos import is_active
from app.schemas import NotificacionesOut, NotificacionOut
from app.security import as_utc, current_user

router = APIRouter(prefix="/api/notificaciones", tags=["notificaciones"])

MAX_NOTIFICACIONES = 30
DIAS_RESPONDIDAS = 30


def subject_name(group: GrupoEstudio) -> str:
    return f"{group.asignatura.sigla} {group.asignatura.nombre}"


def active_person(user: Usuario) -> str | None:
    return user.nombre if user.eliminado_en is None else None


def collect(db: Session, user: Usuario) -> list[NotificacionOut]:
    since = now_utc() - timedelta(days=DIAS_RESPONDIDAS)
    seen = as_utc(user.avisos_vistos_en) if user.avisos_vistos_en else None
    items: list[NotificacionOut] = []

    def add(kind, record_id, moment, group, person, accepted=None):
        moment = as_utc(moment)
        items.append(
            NotificacionOut(
                id=f"{kind}-{record_id}",
                tipo=kind,
                fecha=moment,
                grupo_id=group.id,
                asignatura=subject_name(group),
                persona=person,
                aceptada=accepted,
                nueva=seen is None or moment > seen,
            )
        )

    # Alguien pidió unirse a un grupo que creó el usuario
    received = db.scalars(
        select(Solicitud)
        .join(Solicitud.grupo)
        .where(GrupoEstudio.creador_id == user.id, Solicitud.estado == "pendiente")
    )
    for request in received:
        if is_active(request.grupo) and request.usuario.eliminado_en is None:
            add("solicitud_recibida", request.id, request.creada_en, request.grupo,
                request.usuario.nombre)

    # Lo invitaron a un grupo
    for invitation in db.scalars(
        select(Invitacion).where(
            Invitacion.usuario_id == user.id, Invitacion.estado == "pendiente"
        )
    ):
        if is_active(invitation.grupo):
            add("invitacion_recibida", invitation.id, invitation.creada_en, invitation.grupo,
                invitation.grupo.creador.nombre)

    # Respondieron una solicitud suya (también el rechazo automático al llenarse el grupo)
    for request in db.scalars(
        select(Solicitud).where(
            Solicitud.usuario_id == user.id,
            Solicitud.estado.in_(("aceptada", "rechazada")),
            Solicitud.respondida_en >= since,
        )
    ):
        if is_active(request.grupo):
            add("solicitud_respondida", request.id, request.respondida_en, request.grupo, None,
                request.estado == "aceptada")

    # Respondieron una invitación que mandó
    for invitation in db.scalars(
        select(Invitacion)
        .join(Invitacion.grupo)
        .where(
            GrupoEstudio.creador_id == user.id,
            Invitacion.estado.in_(("aceptada", "rechazada")),
            Invitacion.respondida_en >= since,
        )
    ):
        if is_active(invitation.grupo):
            add("invitacion_respondida", invitation.id, invitation.respondida_en,
                invitation.grupo, active_person(invitation.usuario),
                invitation.estado == "aceptada")

    items.sort(key=lambda n: n.fecha, reverse=True)
    return items[:MAX_NOTIFICACIONES]


@router.get("", response_model=NotificacionesOut)
def my_notifications(user: Usuario = Depends(current_user), db: Session = Depends(get_db)):
    """Notificaciones del usuario, las más recientes primero."""
    items = collect(db, user)
    return NotificacionesOut(sin_leer=sum(n.nueva for n in items), notificaciones=items)


@router.post("/vistas", status_code=status.HTTP_204_NO_CONTENT)
def mark_seen(user: Usuario = Depends(current_user), db: Session = Depends(get_db)):
    """El usuario abrió la campana: lo que ya existe deja de ser nuevo."""
    now = now_utc()
    # Solo avanza: si dos pestañas abren la campana a la vez y la más antigua guarda al último,
    # la fecha no vuelve atrás (y no reaparecen como nuevas notificaciones ya vistas)
    db.execute(
        update(Usuario)
        .where(
            Usuario.id == user.id,
            or_(Usuario.avisos_vistos_en.is_(None), Usuario.avisos_vistos_en < now),
        )
        .values(avisos_vistos_en=now)
        # La comparación la hace la base; no se repite en Python sobre el usuario ya cargado
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
