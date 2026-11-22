"""Grupos de estudio. Buscar y filtrar (CU04, RF06–07), ver el detalle (CU05, RF08), crear
grupos, «mis grupos», pedir unirse, administrar el grupo (CU10, RF13–15: editar, cerrar, eliminar
y expulsar) y abandonarlo (CU11, RF16)."""

import logging
from datetime import time
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import Field
from sqlalchemy import and_, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.mailer import send_group_full_email
from app.models import (
    Asignatura,
    GrupoEstudio,
    HorarioGrupo,
    MiembroGrupo,
    Solicitud,
    Usuario,
    now_utc,
    today_in_chile,
)
from app.routers.perfil import VALID_CODE, find_subject
from app.schemas import (
    AsignaturaOut,
    GrupoDetalleOut,
    GrupoEditIn,
    GrupoIn,
    GrupoMioOut,
    GrupoResumenOut,
    HorarioOut,
    IntegranteOut,
    InvitacionOut,
    MisGruposOut,
    SolicitanteOut,
    SolicitudIn,
    SolicitudOut,
)
from app.security import current_user
from app.text import normalize_code, normalize_text

logger = logging.getLogger("coestudia.grupos")

router = APIRouter(prefix="/api/grupos", tags=["grupos"])

MAX_RESULTS = 100

DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado"]


def overlaps(a: tuple[int, time, time], b: tuple[int, time, time]) -> bool:
    """Dos horarios (día, inicio, fin) chocan si son el mismo día y se cruzan sus horas.
    Que uno termine justo cuando empieza el otro no es choque."""
    return a[0] == b[0] and a[1] < b[2] and b[1] < a[2]


def slot(h) -> tuple[int, time, time]:
    return (h.dia_semana, h.hora_inicio, h.hora_fin)


# HORARIO_GRUPO.dia_semana: 0 = lunes ... 5 = sábado
DayOfWeek = Annotated[int, Field(ge=0, le=5)]


def summary_fields(group: GrupoEstudio, members: int) -> dict:
    """Datos que comparten el resultado del buscador y el detalle del grupo."""
    return {
        "id": group.id,
        "asignatura": AsignaturaOut.model_validate(group.asignatura),
        "descripcion": group.descripcion,
        "es_online": group.es_online,
        "cupo_maximo": group.cupo_maximo,
        "miembros": members,
        "abierto": group.abierto,
        "vigente_hasta": group.vigente_hasta,
        "horarios": [HorarioOut.model_validate(h) for h in sorted(group.horarios, key=slot)],
    }


def matches_subject(subject: Asignatura, query: str) -> bool:
    """Busca por sigla ("mat 1620") o por nombre sin importar tildes ("calculo")."""
    return normalize_code(query) in subject.sigla or normalize_text(query) in normalize_text(
        subject.nombre
    )


@router.get("", response_model=list[GrupoResumenOut])
def search_groups(
    q: Annotated[str, Query(max_length=100, description="Sigla o nombre de la asignatura")] = "",
    es_online: Annotated[bool | None, Query(description="Sin valor: cualquier modalidad")] = None,
    dia: Annotated[list[DayOfWeek], Query(description="0 = lunes ... 5 = sábado")] = [],
    desde: Annotated[
        time | None, Query(description="Horarios que terminan después de esta")
    ] = None,
    hasta: Annotated[time | None, Query(description="Horarios que empiezan antes de esta")] = None,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Grupos vigentes de la universidad del usuario; los abiertos y con cupo primero.

    El día y el rango de horas elegidos tienen que calzar en un mismo horario del grupo: basta
    con que se crucen, no que el horario quede completo dentro del rango.
    """
    if desde is not None and hasta is not None and desde >= hasta:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "La hora «hasta» debe ser posterior a «desde»."
        )
    # Las asignaturas de una universidad son pocas: se filtran aquí para ignorar tildes, que
    # SQLite y PostgreSQL tratan distinto
    subjects = db.scalars(select(Asignatura).where(Asignatura.universidad == user.universidad))
    if q.strip():
        subjects = (s for s in subjects if matches_subject(s, q))
    subject_ids = [s.id for s in subjects]
    if not subject_ids:
        return []

    members = (
        select(MiembroGrupo.grupo_id, func.count().label("total"))
        .group_by(MiembroGrupo.grupo_id)
        .subquery()
    )
    stmt = (
        select(GrupoEstudio, func.coalesce(members.c.total, 0))
        .outerjoin(members, members.c.grupo_id == GrupoEstudio.id)
        .where(
            GrupoEstudio.asignatura_id.in_(subject_ids),
            GrupoEstudio.eliminado_en.is_(None),
            GrupoEstudio.vigente_hasta >= today_in_chile(),
        )
        .options(selectinload(GrupoEstudio.asignatura), selectinload(GrupoEstudio.horarios))
        # Primero los que reciben solicitudes y tienen cupo
        .order_by(
            and_(
                GrupoEstudio.abierto, func.coalesce(members.c.total, 0) < GrupoEstudio.cupo_maximo
            ).desc(),
            GrupoEstudio.vigente_hasta,
            GrupoEstudio.id,
        )
        .limit(MAX_RESULTS)
    )
    if es_online is not None:
        stmt = stmt.where(GrupoEstudio.es_online == es_online)
    if dia or desde is not None or hasta is not None:
        schedule = [HorarioGrupo.grupo_id == GrupoEstudio.id]
        if dia:
            schedule.append(HorarioGrupo.dia_semana.in_(dia))
        if desde is not None:
            schedule.append(HorarioGrupo.hora_fin > desde)
        if hasta is not None:
            schedule.append(HorarioGrupo.hora_inicio < hasta)
        stmt = stmt.where(exists().where(*schedule))

    return [GrupoResumenOut(**summary_fields(group, total)) for group, total in db.execute(stmt)]


def is_active(group: GrupoEstudio) -> bool:
    return group.eliminado_en is None and group.vigente_hasta >= today_in_chile()


def visible_group(db: Session, grupo_id: int, user: Usuario) -> GrupoEstudio:
    """Grupo vigente de la universidad del usuario, o 404."""
    group = db.get(GrupoEstudio, grupo_id)
    # Los grupos de otra universidad, eliminados o vencidos no aparecen en el buscador: tampoco
    # se pueden abrir con el id
    if group is None or group.asignatura.universidad != user.universidad or not is_active(group):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Este grupo no existe o ya no está disponible."
        )
    return group


def is_full(group: GrupoEstudio) -> bool:
    return len(group.miembros) >= group.cupo_maximo


def active_request(group: GrupoEstudio, user_id: int) -> Solicitud | None:
    """Solicitud del usuario que espera respuesta del creador."""
    return next(
        (s for s in group.solicitudes if s.usuario_id == user_id and s.estado == "pendiente"),
        None,
    )


def reject_if_full(group: GrupoEstudio) -> list[Solicitud]:
    """Rechazo automático: si el grupo quedó lleno, las solicitudes pendientes se rechazan, para
    que nadie se quede esperando una respuesta que ya no puede llegar. Devuelve las rechazadas
    para avisarles por correo (ver notify_full)."""
    if not is_full(group):
        return []
    rejected = [s for s in group.solicitudes if s.estado == "pendiente"]
    for s in rejected:
        s.estado = "rechazada"
        s.respondida_en = now_utc()
    return rejected


def send_full_notice(email: str, name: str, subject: str) -> None:
    # Si el correo falla el rechazo igual queda guardado: se ve en «Mis solicitudes»
    try:
        send_group_full_email(email, name, subject)
    except Exception:
        logger.exception("No se pudo avisar por correo que el grupo se llenó a %s", email)


def notify_full(tasks: BackgroundTasks, rejected: list[Solicitud]) -> None:
    """Avisa por correo a quienes se rechazó porque el grupo se llenó. Se envía después de
    responder y se copian los datos ahora, porque la sesión de la base ya estará cerrada."""
    for s in rejected:
        if s.usuario.eliminado_en is not None:
            continue
        subject = f"{s.grupo.asignatura.sigla} {s.grupo.asignatura.nombre}"
        tasks.add_task(send_full_notice, s.usuario.email, s.usuario.nombre, subject)


def own_group(db: Session, grupo_id: int, user: Usuario) -> GrupoEstudio:
    """Grupo vigente creado por el usuario, o el error que corresponda."""
    group = visible_group(db, grupo_id, user)
    if group.creador_id != user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Solo el creador puede administrar el grupo."
        )
    return group


def membership(group: GrupoEstudio, user_id: int) -> MiembroGrupo | None:
    return next((m for m in group.miembros if m.usuario_id == user_id), None)


@router.get("/mios", response_model=MisGruposOut)
def my_groups(user: Usuario = Depends(current_user)):
    """Grupos vigentes donde el usuario es integrante, separados en los que creó y en los que
    participa. Los más próximos a vencer primero."""
    groups = sorted(
        (m.grupo for m in user.membresias if is_active(m.grupo)),
        key=lambda g: (g.vigente_hasta, g.id),
    )
    return MisGruposOut(
        creados=[mine_out(g, user) for g in groups if g.creador_id == user.id],
        participo=[mine_out(g, user) for g in groups if g.creador_id != user.id],
    )


@router.get("/{grupo_id}", response_model=GrupoDetalleOut)
def group_detail(
    grupo_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    """Detalle de un grupo vigente de la universidad del usuario, con sus integrantes.

    Lugar y enlace se muestran solo a los integrantes (RNF de privacidad).
    """
    return detail_out(visible_group(db, grupo_id, user), user)


@router.post(
    "/{grupo_id}/solicitudes", response_model=GrupoDetalleOut, status_code=status.HTTP_201_CREATED
)
def request_to_join(
    grupo_id: int,
    data: SolicitudIn | None = None,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Pide unirse a un grupo abierto y con cupo. El creador la acepta o la rechaza."""
    group = visible_group(db, grupo_id, user)
    if membership(group, user.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya eres integrante de este grupo.")
    if active_request(group, user.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya enviaste una solicitud a este grupo.")
    if any(i.usuario_id == user.id and i.estado == "pendiente" for i in group.invitaciones):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Ya te invitaron a este grupo: acepta la invitación en «Mis solicitudes».",
        )
    if not group.abierto:
        raise HTTPException(status.HTTP_409_CONFLICT, "Este grupo está cerrado.")
    if is_full(group):
        raise HTTPException(status.HTTP_409_CONFLICT, "Este grupo está lleno.")

    message = data.mensaje if data and data.mensaje else None
    group.solicitudes.append(Solicitud(usuario=user, mensaje=message))
    try:
        db.commit()
    except IntegrityError:
        # La misma persona la envió dos veces al mismo tiempo (índice uq_solicitud_pendiente)
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya enviaste una solicitud a este grupo.")
    return detail_out(group, user)


@router.delete("/{grupo_id}/solicitudes", response_model=GrupoDetalleOut)
def cancel_request(
    grupo_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    """Retira la solicitud pendiente del usuario antes de que el creador responda."""
    group = visible_group(db, grupo_id, user)
    request = active_request(group, user.id)
    if request is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No tienes una solicitud pendiente en este grupo."
        )
    request.estado = "cancelada"
    request.cancelada_en = now_utc()
    db.commit()
    return detail_out(group, user)


@router.post("", response_model=GrupoDetalleOut, status_code=status.HTTP_201_CREATED)
def create_group(
    data: GrupoIn, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    """Crea un grupo en la universidad del usuario. El creador queda como su primer integrante.

    La asignatura se busca por sigla y, si no existe, se crea con el nombre recibido.
    """
    code = normalize_code(data.sigla)
    if not VALID_CODE.fullmatch(code):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La sigla debe tener entre 3 y 20 letras o números, ej. MAT1620.",
        )
    check_group_details(data)

    subject = find_subject(db, code, user.universidad)
    if subject is None:
        name = " ".join((data.nombre_asignatura or "").split())
        if not name:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"{code} aún no está en Coestudia. Escribe su nombre para crear el grupo.",
            )
        subject = Asignatura(sigla=code, nombre=name, universidad=user.universidad)

    group = GrupoEstudio(asignatura=subject, creador=user)
    apply_group_details(group, data)
    group.miembros = [MiembroGrupo(usuario=user)]
    db.add(group)
    try:
        db.commit()
    except IntegrityError:
        # Otra persona creó la misma sigla al mismo tiempo
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No pudimos crear el grupo. Intenta de nuevo."
        )
    return detail_out(group, user)


def check_group_details(data: GrupoIn | GrupoEditIn) -> None:
    """Reglas que comparten crear y editar un grupo."""
    if data.vigente_hasta <= today_in_chile():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La fecha de vigencia debe ser posterior a hoy.",
        )
    if data.es_online:
        link = data.enlace or ""
        if not link.startswith(("https://", "http://")):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Escribe el enlace de la reunión, ej. https://meet.google.com/abc-defg-hij.",
            )
    elif not data.lugar:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Escribe dónde se van a juntar.")
    check_schedule(data.horarios)


def check_schedule(horarios) -> None:
    slots = sorted(slot(h) for h in horarios)
    for day, start, end in slots:
        if end <= start:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"El horario del {DAY_NAMES[day]} debe terminar después de empezar.",
            )
    # Ordenados por día e inicio, basta comparar cada horario con el siguiente
    for a, b in zip(slots, slots[1:]):
        if overlaps(a, b):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Hay dos horarios del {DAY_NAMES[a[0]]} que se cruzan.",
            )


def apply_group_details(group: GrupoEstudio, data: GrupoIn | GrupoEditIn) -> None:
    group.descripcion = data.descripcion or None
    group.es_online = data.es_online
    group.lugar = None if data.es_online else data.lugar
    group.enlace = data.enlace if data.es_online else None
    group.cupo_maximo = data.cupo_maximo
    group.vigente_hasta = data.vigente_hasta
    group.horarios = [
        HorarioGrupo(dia_semana=d, hora_inicio=start, hora_fin=end)
        for d, start, end in sorted(slot(h) for h in data.horarios)
    ]


@router.put("/{grupo_id}", response_model=GrupoDetalleOut)
def update_group(
    grupo_id: int,
    data: GrupoEditIn,
    tasks: BackgroundTasks,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Edita el grupo (solo el creador). La asignatura no cambia. Con `abierto: false` el grupo
    deja de recibir solicitudes; las que ya estaban siguen esperando hasta que lo reabra. Si el
    cupo queda igual a los integrantes, el grupo se llena y se rechazan las pendientes."""
    group = own_group(db, grupo_id, user)
    check_group_details(data)
    if data.cupo_maximo < len(group.miembros):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"El cupo no puede ser menor que los {len(group.miembros)} integrantes actuales.",
        )
    apply_group_details(group, data)
    group.abierto = data.abierto
    rejected = reject_if_full(group)
    db.commit()
    notify_full(tasks, rejected)
    return detail_out(group, user)


@router.delete("/{grupo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    grupo_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    """Elimina el grupo (solo el creador). Se elimina de forma lógica: deja de aparecer en el
    buscador, en «mis grupos» y en «mis solicitudes»."""
    group = own_group(db, grupo_id, user)
    group.eliminado_en = now_utc()
    group.eliminado_por_vigencia = False
    db.commit()


@router.delete("/{grupo_id}/integrantes/{usuario_id}", response_model=GrupoDetalleOut)
def remove_member(
    grupo_id: int,
    usuario_id: int,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Expulsa a un integrante (solo el creador)."""
    group = own_group(db, grupo_id, user)
    if usuario_id == user.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "No puedes expulsarte: si quieres salir, elimina el grupo."
        )
    member = membership(group, usuario_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esa persona no es integrante del grupo.")
    group.miembros.remove(member)
    db.commit()
    return detail_out(group, user)


@router.post("/{grupo_id}/abandonar", status_code=status.HTTP_204_NO_CONTENT)
def leave_group(
    grupo_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    """El usuario deja de ser integrante. El creador no puede abandonar: elimina el grupo."""
    group = visible_group(db, grupo_id, user)
    member = membership(group, user.id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No eres integrante de este grupo.")
    if group.creador_id == user.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Eres el creador del grupo: para dejarlo, elimínalo.",
        )
    group.miembros.remove(member)
    db.commit()


def detail_out(group: GrupoEstudio, user: Usuario) -> GrupoDetalleOut:
    """Detalle del grupo visto por `user`. Lugar y enlace solo si es integrante."""
    # El creador primero y después en el orden en que entraron
    members = sorted(
        group.miembros, key=lambda m: (m.usuario_id != group.creador_id, m.unido_en, m.usuario_id)
    )
    is_member = any(m.usuario_id == user.id for m in members)
    request = active_request(group, user.id)
    return GrupoDetalleOut(
        **summary_fields(group, len(members)),
        creador=group.creador.nombre,
        integrantes=[
            IntegranteOut(
                id=m.usuario.id,
                nombre=m.usuario.nombre,
                carrera=m.usuario.carrera,
                es_creador=m.usuario_id == group.creador_id,
            )
            for m in members
        ],
        es_miembro=is_member,
        solicitud_pendiente=request is not None,
        lugar=group.lugar if is_member else None,
        enlace=group.enlace if is_member else None,
    )


def mine_out(group: GrupoEstudio, user: Usuario) -> GrupoMioOut:
    """Grupo de «mis grupos». Solo el creador ve las solicitudes pendientes, las más antiguas
    primero, y las invitaciones que aún no responden."""
    is_owner = group.creador_id == user.id
    pending = (
        sorted(
            (s for s in group.solicitudes if s.estado == "pendiente"),
            key=lambda s: (s.creada_en, s.id),
        )
        if is_owner
        else []
    )
    invitations = (
        sorted(
            (i for i in group.invitaciones if i.estado == "pendiente"),
            key=lambda i: (i.creada_en, i.id),
        )
        if is_owner
        else []
    )
    return GrupoMioOut(
        **summary_fields(group, len(group.miembros)),
        es_creador=is_owner,
        lugar=group.lugar,
        enlace=group.enlace,
        solicitudes=[
            SolicitudOut(
                id=s.id,
                usuario=SolicitanteOut.model_validate(s.usuario),
                estado=s.estado,
                mensaje=s.mensaje,
                creada_en=s.creada_en,
            )
            for s in pending
        ],
        invitaciones=[
            InvitacionOut(
                id=i.id, usuario=SolicitanteOut.model_validate(i.usuario), creada_en=i.creada_en
            )
            for i in invitations
        ],
    )
