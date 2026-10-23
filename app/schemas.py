"""Formato de entrada y salida de la API (Pydantic)."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# bcrypt usa solo los primeros 72 bytes de la contraseña
PASSWORD_MAX_BYTES = 72


class RegistroIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: EmailStr
    password: str = Field(min_length=8)
    nombre: str = Field(min_length=1, max_length=120)
    carrera: str = Field(min_length=1, max_length=120)
    anio_ingreso: int = Field(ge=1990)
    acepta_terminos: bool

    @field_validator("password")
    @classmethod
    def password_is_strong(cls, value: str) -> str:
        # Mismas reglas que muestra el formulario de registro (Login.jsx)
        if len(value.encode()) > PASSWORD_MAX_BYTES:
            raise ValueError("La contraseña puede tener como máximo 72 caracteres.")
        if not any(c.isupper() for c in value):
            raise ValueError("La contraseña debe tener al menos una mayúscula.")
        if not any(c.islower() for c in value):
            raise ValueError("La contraseña debe tener al menos una minúscula.")
        if not any(c.isdigit() for c in value):
            raise ValueError("La contraseña debe tener al menos un número.")
        return value

    @field_validator("anio_ingreso")
    @classmethod
    def entry_year_not_in_future(cls, value: int) -> int:
        if value > date.today().year:
            raise ValueError("El año de ingreso no puede ser futuro.")
        return value


class LoginIn(BaseModel):
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_not_too_long(cls, value: str) -> str:
        # Evita mandarle a bcrypt contraseñas gigantes (bcrypt falla sobre 72 bytes)
        if len(value.encode()) > PASSWORD_MAX_BYTES:
            raise ValueError("La contraseña puede tener como máximo 72 caracteres.")
        return value


class TokenIn(BaseModel):
    # Los tokens del correo miden 43 caracteres; el límite evita cuerpos enormes
    token: str = Field(max_length=200)


class EmailIn(BaseModel):
    email: EmailStr


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    nombre: str
    universidad: str
    carrera: str | None
    anio_ingreso: int | None
    email_verificado_en: datetime | None


class SesionOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    usuario: UsuarioOut


class MensajeOut(BaseModel):
    message: str
