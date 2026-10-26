"""Perfil académico y «mis asignaturas» (CU03, RF03–04)."""

import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Asignatura, Usuario, UsuarioAsignatura
from app.schemas import AsignaturaIn, AsignaturaOut, PerfilIn, UsuarioOut
from app.security import current_user
from app.text import normalize_code

router = APIRouter(prefix="/api/perfil", tags=["perfil"])

# Un semestre normal tiene 5 o 6 ramos; el tope evita listas sin sentido
MAX_SUBJECTS = 10

# Sigla ya normalizada: letras y números, ej. MAT1620 o IIC2143
VALID_CODE = re.compile(r"[A-Z0-9]{3,20}")


def find_subject(db: Session, code: str, university: str) -> Asignatura | None:
    return db.scalar(
        select(Asignatura).where(Asignatura.sigla == code, Asignatura.universidad == university)
    )


def my_subjects(user: Usuario) -> list[AsignaturaOut]:
    subjects = sorted((link.asignatura for link in user.asignaturas), key=lambda s: s.sigla)
    return [AsignaturaOut.model_validate(s) for s in subjects]


@router.put("", response_model=UsuarioOut)
def update_profile(
    data: PerfilIn, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    user.nombre = data.nombre
    user.carrera = data.carrera
    user.anio_ingreso = data.anio_ingreso
    db.commit()
    return user


@router.get("/asignaturas", response_model=list[AsignaturaOut])
def list_my_subjects(user: Usuario = Depends(current_user)):
    return my_subjects(user)


@router.post("/asignaturas", response_model=AsignaturaOut, status_code=status.HTTP_201_CREATED)
def add_my_subject(
    data: AsignaturaIn, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    """Agrega una asignatura a «mis asignaturas». Si la sigla no existe en la universidad del
    usuario, la crea con el nombre recibido ("buscar o crear")."""
    code = normalize_code(data.sigla)
    if not VALID_CODE.fullmatch(code):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "La sigla debe tener entre 3 y 20 letras o números, ej. MAT1620.",
        )

    subject = find_subject(db, code, user.universidad)
    if subject is not None and any(link.asignatura_id == subject.id for link in user.asignaturas):
        # Ya estaba en la lista: no es un error
        return subject
    if len(user.asignaturas) >= MAX_SUBJECTS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Puedes tener como máximo {MAX_SUBJECTS} asignaturas.",
        )
    if subject is None:
        name = " ".join((data.nombre or "").split())
        if not name:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"{code} aún no está en Coestudia. Escribe su nombre para agregarla.",
            )
        subject = Asignatura(sigla=code, nombre=name, universidad=user.universidad)

    user.asignaturas.append(UsuarioAsignatura(asignatura=subject))
    try:
        db.commit()
    except IntegrityError:
        # Otra persona creó la misma sigla al mismo tiempo
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "No pudimos agregarla. Intenta de nuevo.")
    return subject


@router.delete("/asignaturas/{asignatura_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_my_subject(
    asignatura_id: int, user: Usuario = Depends(current_user), db: Session = Depends(get_db)
):
    link = db.get(UsuarioAsignatura, (user.id, asignatura_id))
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esa asignatura no está en tu lista.")
    db.delete(link)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
