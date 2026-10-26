"""Catálogo de asignaturas de la universidad del usuario (autocompletar siglas)."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Asignatura, Usuario
from app.schemas import AsignaturaOut
from app.security import current_user

router = APIRouter(prefix="/api/asignaturas", tags=["asignaturas"])


@router.get("", response_model=list[AsignaturaOut])
def list_subjects(user: Usuario = Depends(current_user), db: Session = Depends(get_db)):
    """Asignaturas que ya existen en la universidad del usuario, ordenadas por sigla."""
    return db.scalars(
        select(Asignatura)
        .where(Asignatura.universidad == user.universidad)
        .order_by(Asignatura.sigla)
    ).all()
