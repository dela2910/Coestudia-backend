"""Sugerencias para el usuario: grupos que le pueden servir y una búsqueda sugerida de
asignaturas (autocompletar el buscador).

Las señales que se usan, de más a menos importante:

1. «Mis asignaturas»: lo que el usuario cursa.
2. Su carrera: lo que cursan (o estudian en grupos) otros estudiantes de la misma carrera.
3. Sus horarios: se bajan los grupos que chocan con los grupos donde ya participa.

Solo se sugieren grupos vigentes de su universidad que reciben solicitudes, tienen cupo y donde
el usuario aún no está ni tiene una solicitud activa o rechazada. Además, el grupo tiene que tener
relación con él: ser de una asignatura que cursa o que cursan estudiantes de su carrera, o tener
integrantes de su carrera. Un grupo sin relación no se sugiere aunque tenga cupo: para descubrir
otras asignaturas está la búsqueda sugerida.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import (
    Asignatura,
    GrupoEstudio,
    MiembroGrupo,
    Usuario,
    UsuarioAsignatura,
    now_utc,
    today_in_chile,
)
from app.routers.grupos import ACTIVE_REQUEST_STATES, is_active, matches_subject, summary_fields
from app.schemas import AsignaturaOut, AsignaturaSugeridaOut, GrupoSugeridoOut
from app.security import current_user
from app.text import normalize_text

router = APIRouter(prefix="/api/sugerencias", tags=["sugerencias"])

MAX_GROUPS = 20
MAX_SUBJECTS = 8
# Un grupo creado hace menos de esto se marca como nuevo
NEW_GROUP_DAYS = 7

# Puntaje de cada señal. La asignatura pesa más que todo lo demás junto: un grupo de un ramo que
# el usuario cursa siempre va antes que uno de un ramo que no cursa
SCORE_MY_SUBJECT = 100
SCORE_PEER_SUBJECT = 10
SCORE_PEER_MEMBER = 3
SCORE_NEW = 1
PENALTY_SCHEDULE_CLASH = 5


@dataclass
class Context:
    """Lo que se sabe del usuario para sugerirle grupos y asignaturas."""

    my_subjects: set[int]
    # asignatura_id → cuántos estudiantes de su carrera la cursan o la estudian en un grupo
    peer_subjects: Counter
    # Usuarios de su carrera (sin él)
    peers: set[int]
    # (día, es_mañana) de los grupos donde ya participa
    busy: set[tuple[int, bool]]
    # Grupos que se le pueden sugerir: vigentes, abiertos, con cupo y sin relación con él
    available: list[GrupoEstudio] = field(default_factory=list)


def same_career(a: str | None, b: str | None) -> bool:
    return bool(a and b) and normalize_text(a) == normalize_text(b)


def build_context(db: Session, user: Usuario) -> Context:
    peers = {
        u.id
        for u in db.scalars(
            select(Usuario).where(
                Usuario.universidad == user.universidad,
                Usuario.id != user.id,
                Usuario.eliminado_en.is_(None),
                Usuario.carrera.is_not(None),
            )
        )
        if same_career(u.carrera, user.carrera)
    }

    # Cada compañero cuenta una vez por asignatura, aunque la tenga en su lista y en un grupo
    peer_pairs: set[tuple[int, int]] = set()
    if peers:
        peer_pairs.update(
            db.execute(
                select(UsuarioAsignatura.usuario_id, UsuarioAsignatura.asignatura_id).where(
                    UsuarioAsignatura.usuario_id.in_(peers)
                )
            ).all()
        )
        peer_pairs.update(
            db.execute(
                select(MiembroGrupo.usuario_id, GrupoEstudio.asignatura_id)
                .join(GrupoEstudio, GrupoEstudio.id == MiembroGrupo.grupo_id)
                .where(MiembroGrupo.usuario_id.in_(peers), GrupoEstudio.eliminado_en.is_(None))
            ).all()
        )
    peer_subjects = Counter(subject_id for _, subject_id in peer_pairs)

    my_groups = [m.grupo for m in user.membresias if is_active(m.grupo)]
    busy = {(h.dia_semana, h.es_manana) for g in my_groups for h in g.horarios}

    # No se sugieren grupos donde ya está, ni donde espera respuesta o lo rechazaron
    excluded = {g.id for g in my_groups} | {
        s.grupo_id
        for s in user.solicitudes
        if s.estado in ACTIVE_REQUEST_STATES or s.estado == "rechazada"
    }

    candidates = db.scalars(
        select(GrupoEstudio)
        .join(Asignatura)
        .where(
            Asignatura.universidad == user.universidad,
            GrupoEstudio.eliminado_en.is_(None),
            GrupoEstudio.vigente_hasta >= today_in_chile(),
            GrupoEstudio.abierto,
        )
        .options(
            selectinload(GrupoEstudio.asignatura),
            selectinload(GrupoEstudio.horarios),
            selectinload(GrupoEstudio.miembros).selectinload(MiembroGrupo.usuario),
        )
    )
    available = [
        g
        for g in candidates
        if g.id not in excluded
        and len(g.miembros) < g.cupo_maximo
        and all(m.usuario_id != user.id for m in g.miembros)
    ]

    return Context(
        my_subjects={link.asignatura_id for link in user.asignaturas},
        peer_subjects=peer_subjects,
        peers=peers,
        busy=busy,
        available=available,
    )


def as_utc(moment: datetime) -> datetime:
    # SQLite devuelve las fechas sin zona horaria; se guardan siempre en UTC
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def score_group(group: GrupoEstudio, ctx: Context) -> tuple[int, list[str]] | None:
    """Puntaje del grupo para el usuario y los motivos que se le muestran, o None si el grupo no
    tiene relación con él (ni por su asignatura ni por su carrera)."""
    score = 0
    reasons: list[str] = []
    subject_id = group.asignatura_id
    if subject_id in ctx.my_subjects:
        score += SCORE_MY_SUBJECT
        reasons.append(f"Cursas {group.asignatura.sigla}")
    elif ctx.peer_subjects[subject_id]:
        n = ctx.peer_subjects[subject_id]
        score += SCORE_PEER_SUBJECT + min(n, 5)
        reasons.append(f"Lo cursan {plural(n, 'estudiante', 'estudiantes')} de tu carrera")

    peer_members = sum(1 for m in group.miembros if m.usuario_id in ctx.peers)
    if peer_members:
        score += SCORE_PEER_MEMBER
        reasons.append(f"{plural(peer_members, 'integrante', 'integrantes')} de tu carrera")

    # Horario y novedad solo ordenan: por sí solos no son motivo para sugerir un grupo
    if not reasons:
        return None

    slots = {(h.dia_semana, h.es_manana) for h in group.horarios}
    if ctx.busy and slots:
        if slots <= ctx.busy:
            # Todos sus horarios chocan con grupos donde ya participa
            score -= PENALTY_SCHEDULE_CLASH
        elif not slots & ctx.busy:
            reasons.append("No choca con tus otros grupos")

    if group.creado_en and as_utc(group.creado_en) >= now_utc() - timedelta(days=NEW_GROUP_DAYS):
        score += SCORE_NEW
        reasons.append("Grupo nuevo")

    return score, reasons


@router.get("/grupos", response_model=list[GrupoSugeridoOut])
def suggested_groups(
    limite: Annotated[int, Query(ge=1, le=MAX_GROUPS)] = 6,
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Grupos sugeridos para el usuario, del más al menos relevante, con los motivos. Puede
    devolver una lista vacía si ningún grupo disponible tiene relación con él."""
    ctx = build_context(db, user)
    scored = [(group, *result) for group in ctx.available if (result := score_group(group, ctx))]
    # Con el mismo puntaje: más integrantes (más activo), después el que vence antes
    scored.sort(
        key=lambda item: (-item[1], -len(item[0].miembros), item[0].vigente_hasta, item[0].id)
    )
    return [
        GrupoSugeridoOut(**summary_fields(group, len(group.miembros)), motivos=reasons)
        for group, _, reasons in scored[:limite]
    ]


@router.get("/asignaturas", response_model=list[AsignaturaSugeridaOut])
def suggested_subjects(
    q: Annotated[str, Query(max_length=100, description="Lo que el usuario lleva escrito")] = "",
    user: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Búsqueda sugerida: asignaturas de la universidad del usuario con cuántos grupos tienen
    disponibles para él.

    Sin texto, propone las que tienen grupos disponibles. Con texto, autocompleta con las que
    calzan por sigla o nombre, aunque aún no tengan grupos (así puede crear el primero).
    """
    ctx = build_context(db, user)
    open_groups = Counter(g.asignatura_id for g in ctx.available)
    subjects = db.scalars(select(Asignatura).where(Asignatura.universidad == user.universidad))
    if q.strip():
        subjects = [s for s in subjects if matches_subject(s, q)]
    else:
        subjects = [s for s in subjects if open_groups[s.id]]

    def rank(subject: Asignatura):
        return (
            subject.id not in ctx.my_subjects,
            -ctx.peer_subjects[subject.id],
            -open_groups[subject.id],
            subject.sigla,
        )

    return [
        AsignaturaSugeridaOut(
            asignatura=AsignaturaOut.model_validate(s),
            grupos_disponibles=open_groups[s.id],
            motivo=subject_reason(s.id, ctx),
        )
        for s in sorted(subjects, key=rank)[:MAX_SUBJECTS]
    ]


def subject_reason(subject_id: int, ctx: Context) -> str | None:
    if subject_id in ctx.my_subjects:
        return "Está en tus asignaturas"
    n = ctx.peer_subjects[subject_id]
    if n:
        return f"La cursan {plural(n, 'estudiante', 'estudiantes')} de tu carrera"
    return None
