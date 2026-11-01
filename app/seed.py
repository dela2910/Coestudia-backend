"""Carga datos de ejemplo en la base local para probar la app y hacer demos.

    python -m app.seed

Crea usuarios ya verificados (contraseña: Coestudia2026), asignaturas y grupos con horarios e
integrantes. Si los datos ya están cargados, no hace nada. Por seguridad solo corre sobre SQLite;
para otra base hay que pasar --forzar.
"""

import sys
from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import (
    Asignatura,
    GrupoEstudio,
    HorarioGrupo,
    MiembroGrupo,
    Usuario,
    now_utc,
    today_in_chile,
)
from app.security import hash_password

UC = "Pontificia Universidad Católica de Chile"
PASSWORD = "Coestudia2026"

USERS = [
    ("ana.rojas@uc.cl", "Ana Rojas", "Ingeniería Civil", 2023),
    ("matias.soto@uc.cl", "Matías Soto", "Ingeniería Civil Eléctrica", 2022),
    ("diego.munoz@uc.cl", "Diego Muñoz", "Ingeniería Civil", 2024),
    ("fernanda.diaz@uc.cl", "Fernanda Díaz", "Física", 2023),
    ("benjamin.vera@uc.cl", "Benjamín Vera", "Ingeniería Civil Informática", 2023),
    ("sofia.perez@uc.cl", "Sofía Pérez", "Ingeniería Civil Industrial", 2024),
    ("luis.castro@uc.cl", "Luis Castro", "Ingeniería Comercial", 2022),
]

SUBJECTS = [
    ("MAT1620", "Cálculo II"),
    ("FIS1513", "Física I"),
    ("MAT1203", "Álgebra"),
    ("IIC2143", "Ingeniería de Software"),
]

# (sigla, creador, online, lugar o enlace, cupo, días de vigencia, horarios, otros integrantes,
#  descripción)
GROUPS = [
    (
        "MAT1620",
        "ana.rojas@uc.cl",
        False,
        "Biblioteca central, sala 2",
        5,
        35,
        [(1, False)],
        ["luis.castro@uc.cl", "sofia.perez@uc.cl"],
        "Repasamos integrales múltiples y series antes del certamen.",
    ),
    (
        "MAT1620",
        "matias.soto@uc.cl",
        True,
        "https://meet.google.com/abc-defg-hij",
        4,
        42,
        [(3, True)],
        [],
        "Sesiones cortas de 1 hora resolviendo ejercicios de controles anteriores.",
    ),
    (
        "MAT1620",
        "diego.munoz@uc.cl",
        False,
        "Sala de estudio Facultad de Ingeniería",
        3,
        36,
        [(5, True)],
        ["ana.rojas@uc.cl", "benjamin.vera@uc.cl"],
        "Grupo intensivo de fin de semana.",
    ),
    (
        "FIS1513",
        "fernanda.diaz@uc.cl",
        True,
        "https://discord.gg/fisica1513",
        4,
        49,
        [(0, False), (2, False)],
        [],
        "Dinámica y trabajo-energía. Nos juntamos a hacer la tarea semanal.",
    ),
    (
        "MAT1203",
        "benjamin.vera@uc.cl",
        False,
        "Casino central",
        6,
        42,
        [(2, True)],
        ["sofia.perez@uc.cl"],
        "Matrices, determinantes y espacios vectoriales.",
    ),
    (
        "IIC2143",
        "sofia.perez@uc.cl",
        True,
        "https://meet.google.com/ing-soft-uc",
        5,
        30,
        [(1, True), (3, True)],
        ["benjamin.vera@uc.cl"],
        "Preparación de las entregas del proyecto y del examen.",
    ),
]


def seed(db) -> bool:
    """Carga los datos; devuelve False si ya estaban."""
    if db.scalar(select(Usuario).where(Usuario.email == USERS[0][0])):
        return False

    now = now_utc()
    password_hash = hash_password(PASSWORD)
    users = {}
    for email, name, career, year in USERS:
        users[email] = Usuario(
            email=email,
            password_hash=password_hash,
            nombre=name,
            universidad=UC,
            carrera=career,
            anio_ingreso=year,
            terminos_aceptados_en=now,
            email_verificado_en=now,
        )
        db.add(users[email])

    subjects = {}
    for code, name in SUBJECTS:
        # Puede existir si alguien ya la creó al probar
        subject = db.scalar(
            select(Asignatura).where(Asignatura.sigla == code, Asignatura.universidad == UC)
        )
        subjects[code] = subject or Asignatura(sigla=code, nombre=name, universidad=UC)

    for code, owner, online, where, capacity, days, schedule, others, description in GROUPS:
        members = [users[owner], *(users[e] for e in others)]
        group = GrupoEstudio(
            asignatura=subjects[code],
            creador=users[owner],
            es_online=online,
            lugar=None if online else where,
            enlace=where if online else None,
            cupo_maximo=capacity,
            # Se cierra solo al completar el cupo
            abierto=len(members) < capacity,
            vigente_hasta=today_in_chile() + timedelta(days=days),
            descripcion=description,
        )
        group.horarios = [HorarioGrupo(dia_semana=d, es_manana=m) for d, m in schedule]
        group.miembros = [MiembroGrupo(usuario=u) for u in members]
        db.add(group)

    db.commit()
    return True


def main() -> None:
    if engine.dialect.name != "sqlite" and "--forzar" not in sys.argv:
        sys.exit(f"La base es {engine.dialect.name}, no SQLite. Usa --forzar si estás seguro.")
    with SessionLocal() as db:
        if seed(db):
            print(f"Datos de ejemplo cargados: {len(USERS)} usuarios y {len(GROUPS)} grupos.")
            print(f"Puedes entrar con {USERS[0][0]} y la contraseña {PASSWORD}.")
        else:
            print("Los datos de ejemplo ya estaban cargados.")


if __name__ == "__main__":
    main()
