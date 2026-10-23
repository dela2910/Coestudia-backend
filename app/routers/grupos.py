"""Grupos de estudio. Buscar y filtrar (CU04, RF06–07)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import Asignatura, GrupoEstudio, HorarioGrupo, MiembroGrupo, Usuario, today_in_chile
from app.schemas import AsignaturaOut, GrupoResumenOut, HorarioOut
from app.security import current_user
from app.text import normalize_code, normalize_text

router = APIRouter(prefix="/api/grupos", tags=["grupos"])

MAX_RESULTS = 100

# HORARIO_GRUPO.dia_semana: 0 = lunes ... 5 = sábado
DayOfWeek = Annotated[int, Field(ge=0, le=5)]


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

    return [
        GrupoResumenOut(
            id=group.id,
            asignatura=AsignaturaOut.model_validate(group.asignatura),
            descripcion=group.descripcion,
            es_online=group.es_online,
            cupo_maximo=group.cupo_maximo,
            miembros=total,
            abierto=group.abierto,
            vigente_hasta=group.vigente_hasta,
            horarios=[
                HorarioOut.model_validate(h)
                for h in sorted(group.horarios, key=lambda h: (h.dia_semana, not h.es_manana))
            ],
        )
        for group, total in db.execute(stmt)
    ]
