"""Contraseñas, sesiones (JWT) y tokens de correo.

Para proteger un endpoint basta con pedir el usuario actual:

    @router.get("/api/algo")
    def algo(user: Usuario = Depends(current_user)): ...
"""

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Usuario

JWT_ALGORITHM = "HS256"
# Duración de la sesión antes de tener que iniciar sesión de nuevo
ACCESS_TOKEN_TTL = timedelta(days=7)
# Duración de los enlaces que se mandan por correo
EMAIL_TOKEN_TTL = timedelta(hours=24)
# El de recuperar contraseña dura menos: quien lo tenga puede tomar la cuenta
PASSWORD_RESET_TTL = timedelta(hours=1)
# Límites de reenvío: protegen la bandeja del usuario y la cuota diaria de Brevo (300 correos)
EMAIL_RESEND_COOLDOWN = timedelta(minutes=1)
EMAIL_MAX_PER_DAY = 5


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    # SQLite devuelve las fechas sin zona horaria; PostgreSQL (timestamptz) sí la trae
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if secret:
        return secret
    # Render define RENDER=true: en producción no se puede usar la clave de desarrollo
    if os.getenv("RENDER"):
        raise RuntimeError("Falta definir JWT_SECRET en el entorno")
    return "clave-de-desarrollo-no-usar-en-produccion"


# --- Contraseñas ---


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    encoded = password.encode()
    # Ninguna contraseña guardada pasa de 72 bytes; bcrypt lanza error si se le pasa una más larga
    if len(encoded) > 72:
        return False
    return bcrypt.checkpw(encoded, password_hash.encode())


# Se compara contra este hash cuando el correo no existe, para que la respuesta tarde lo mismo y
# no delate qué correos están registrados
DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(16))


# --- Tokens de correo (verificación, recuperar y cambiar contraseña) ---


def new_email_token() -> tuple[str, str]:
    """Devuelve (token para el enlace, hash para guardar en la base)."""
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- Sesión (JWT) ---


def create_access_token(user: Usuario) -> str:
    issued_at = now_utc()
    payload = {
        "sub": str(user.id),
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + ACCESS_TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, jwt_secret(), algorithm=JWT_ALGORITHM)


bearer = HTTPBearer(auto_error=False)


def unauthorized(detail: str = "Debes iniciar sesión.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Usuario:
    """Dependencia de FastAPI: el usuario dueño del JWT, o 401 si la sesión no es válida."""
    if credentials is None:
        raise unauthorized()
    try:
        payload = jwt.decode(credentials.credentials, jwt_secret(), algorithms=[JWT_ALGORITHM])
        user = db.get(Usuario, int(payload["sub"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized("Tu sesión no es válida. Inicia sesión de nuevo.")

    if user is None or user.eliminado_en is not None or user.email_verificado_en is None:
        raise unauthorized("Tu sesión no es válida. Inicia sesión de nuevo.")
    # Al cambiar la contraseña se cierran todas las sesiones abiertas antes de ese momento
    if user.contrasena_cambiada_en is not None:
        changed_at = int(as_utc(user.contrasena_cambiada_en).timestamp())
        if payload["iat"] < changed_at:
            raise unauthorized("Tu contraseña cambió. Inicia sesión de nuevo.")
    return user
