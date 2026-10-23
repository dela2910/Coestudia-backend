"""Registro, verificación del correo e inicio de sesión (CU01–02, RF01–02, RF17)."""

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.mailer import send_verification_email
from app.models import TokenCorreo, Usuario
from app.schemas import EmailIn, LoginIn, MensajeOut, RegistroIn, SesionOut, TokenIn, UsuarioOut
from app.security import (
    DUMMY_PASSWORD_HASH,
    EMAIL_MAX_PER_DAY,
    EMAIL_RESEND_COOLDOWN,
    EMAIL_TOKEN_TTL,
    as_utc,
    create_access_token,
    current_user,
    hash_password,
    hash_token,
    new_email_token,
    now_utc,
    verify_password,
)
from app.universities import allowed_domains_text, university_from_email

logger = logging.getLogger("coestudia.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

INVALID_LINK = "El enlace de verificación no es válido o ya expiró. Pide uno nuevo."


def find_active_user(db: Session, email: str) -> Usuario | None:
    user = db.scalar(select(Usuario).where(Usuario.email == email.lower()))
    return user if user is not None and user.eliminado_en is None else None


def issue_verification_token(db: Session, user: Usuario) -> str:
    # Al pedir un enlace nuevo, los anteriores dejan de servir
    now = now_utc()
    for old in user.tokens:
        if old.tipo == "verificacion" and old.usado_en is None:
            old.usado_en = now
    token, token_hash = new_email_token()
    user.tokens.append(
        TokenCorreo(token_hash=token_hash, tipo="verificacion", expira_en=now + EMAIL_TOKEN_TTL)
    )
    return token


def has_pending_verification(user: Usuario) -> bool:
    """True si el usuario tiene un enlace de verificación sin usar y todavía vigente."""
    now = now_utc()
    return any(
        t.tipo == "verificacion" and t.usado_en is None and as_utc(t.expira_en) > now
        for t in user.tokens
    )


def can_resend(user: Usuario) -> bool:
    """Como máximo un correo por minuto y EMAIL_MAX_PER_DAY al día por cuenta."""
    now = now_utc()
    sent = [as_utc(t.creado_en) for t in user.tokens if t.tipo == "verificacion"]
    if any(now - created < EMAIL_RESEND_COOLDOWN for created in sent):
        return False
    return sum(now - created < timedelta(days=1) for created in sent) < EMAIL_MAX_PER_DAY


def send_verification(user: Usuario, token: str) -> None:
    # Si el correo falla la cuenta igual queda creada: el usuario puede pedir que se reenvíe
    try:
        send_verification_email(user.email, user.nombre, token)
    except Exception:
        logger.exception("No se pudo enviar el correo de verificación a %s", user.email)


def session_for(user: Usuario) -> SesionOut:
    return SesionOut(
        access_token=create_access_token(user), usuario=UsuarioOut.model_validate(user)
    )


@router.post("/registro", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED)
def register(data: RegistroIn, db: Session = Depends(get_db)):
    email = data.email.lower()
    university = university_from_email(email)
    if university is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Por ahora solo se aceptan correos {allowed_domains_text()}.",
        )
    if not data.acepta_terminos:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Debes aceptar los términos y condiciones."
        )

    user = db.scalar(select(Usuario).where(Usuario.email == email))
    if user is not None:
        if user.email_verificado_en is not None or user.eliminado_en is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una cuenta con ese correo.")
        # Cuenta sin verificar (ej. alguien se equivocó de correo). Mientras su enlace siga
        # vigente no se toca: si se reemplazara, otra persona podría poner su contraseña en una
        # cuenta que después activa el dueño del correo.
        if has_pending_verification(user):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Ya hay un registro pendiente con ese correo. Revisa tu bandeja o pide que te "
                "reenviemos el enlace.",
            )
    else:
        user = Usuario(email=email)
        db.add(user)

    # Cuenta nueva, o una sin verificar cuyo enlace ya venció: se guardan los datos nuevos
    user.password_hash = hash_password(data.password)
    user.nombre = data.nombre
    user.universidad = university
    user.carrera = data.carrera
    user.anio_ingreso = data.anio_ingreso
    user.terminos_aceptados_en = now_utc()
    token = issue_verification_token(db, user)
    try:
        db.commit()
    except IntegrityError:
        # El correo es único en la base (también cubre dos registros simultáneos)
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una cuenta con ese correo.")

    send_verification(user, token)
    return user


@router.post("/verificar", response_model=SesionOut)
def verify_email(data: TokenIn, db: Session = Depends(get_db)):
    """Marca el correo como verificado e inicia la sesión."""
    record = db.scalar(
        select(TokenCorreo).where(
            TokenCorreo.token_hash == hash_token(data.token), TokenCorreo.tipo == "verificacion"
        )
    )
    now = now_utc()
    if record is None or record.usado_en is not None or as_utc(record.expira_en) < now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_LINK)
    user = record.usuario
    if user.eliminado_en is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, INVALID_LINK)

    record.usado_en = now
    if user.email_verificado_en is None:
        user.email_verificado_en = now
    db.commit()
    return session_for(user)


@router.post(
    "/reenviar-verificacion", response_model=MensajeOut, status_code=status.HTTP_202_ACCEPTED
)
def resend_verification(data: EmailIn, db: Session = Depends(get_db)):
    user = find_active_user(db, data.email)
    if user is not None and user.email_verificado_en is None and can_resend(user):
        token = issue_verification_token(db, user)
        db.commit()
        send_verification(user, token)
    # La respuesta es la misma exista o no la cuenta (y aunque se haya alcanzado el límite), para
    # no revelar qué correos están registrados
    return MensajeOut(
        message="Si la cuenta existe y no está verificada, te enviamos un correo nuevo."
    )


@router.post("/login", response_model=SesionOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = find_active_user(db, data.email)
    password_ok = verify_password(
        data.password, user.password_hash if user else DUMMY_PASSWORD_HASH
    )
    if user is None or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Correo o contraseña incorrectos.")
    if user.email_verificado_en is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Debes verificar tu correo antes de iniciar sesión."
        )
    return session_for(user)


@router.get("/me", response_model=UsuarioOut)
def me(user: Usuario = Depends(current_user)):
    """Usuario de la sesión actual; el frontend lo usa para recuperar la sesión al recargar."""
    return user
