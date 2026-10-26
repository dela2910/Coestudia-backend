"""Grupos de estudio. Buscar y filtrar (CU04, RF06–07) y ver el detalle (CU05, RF08)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import Field
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import Asignatura, GrupoEstudio, HorarioGrupo, MiembroGrupo, Usuario, today_in_chile
from app.schemas import (
    AsignaturaOut,
    GrupoDetalleOut,
    GrupoResumenOut,
    HorarioOut,
    IntegranteOut,
)
from app.security import current_user
from app.text import normalize_code, normalize_text

router = APIRouter(prefix="/api/grupos", tags=["grupos"])

MAX_RESULTS = 100

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
        "horarios": [
            HorarioOut.model_validate(h)
            for h in sorted(group.horarios, key=lambda h: (h.dia_semana, not h.es_manana))
        ],
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
    es_manana: Annotated[list[bool], Query(description="true = mañana, false = tarde")] = [],
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Grupos vigentes de la universidad del usuario; los abiertos primero.

    El día y el bloque elegidos tienen que calzar en un mismo horario del grupo.
    """
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
        .order_by(GrupoEstudio.abierto.desc(), GrupoEstudio.vigente_hasta, GrupoEstudio.id)
        .limit(MAX_RESULTS)
    )
    if es_online is not None:
        stmt = stmt.where(GrupoEstudio.es_online == es_online)
    if dia or es_manana:
        schedule = [HorarioGrupo.grupo_id == GrupoEstudio.id]
        if dia:
            schedule.append(HorarioGrupo.dia_semana.in_(dia))
        if es_manana:
            schedule.append(HorarioGrupo.es_manana.in_(es_manana))
        stmt = stmt.where(exists().where(*schedule))

    return [GrupoResumenOut(**summary_fields(group, total)) for group, total in db.execute(stmt)]


@router.get("/{grupo_id}", response_model=GrupoDetalleOut)
def group_detail(
    grupo_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    """Detalle de un grupo vigente de la universidad del usuario, con sus integrantes.

    Lugar y enlace se muestran solo a los integrantes (RNF de privacidad).
    """
    group = db.get(GrupoEstudio, grupo_id)
    # Los grupos de otra universidad, eliminados o vencidos no aparecen en el buscador: tampoco
    # se pueden abrir con el id
    if (
        group is None
        or group.asignatura.universidad != user.universidad
        or group.eliminado_en is not None
        or group.vigente_hasta < today_in_chile()
    ):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Este grupo no existe o ya no está disponible."
        )

    # El creador primero y después en el orden en que entraron
    members = sorted(
        group.miembros, key=lambda m: (m.usuario_id != group.creador_id, m.unido_en, m.usuario_id)
    )
    is_member = any(m.usuario_id == user.id for m in members)
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
        lugar=group.lugar if is_member else None,
        enlace=group.enlace if is_member else None,
    )
