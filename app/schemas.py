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
        return check_password_strength(value)

    @field_validator("anio_ingreso")
    @classmethod
    def entry_year_not_in_future(cls, value: int) -> int:
        return check_entry_year(value)


def check_password_strength(value: str) -> str:
    # Mismas reglas que muestra el formulario de registro (src/data/password.js en el frontend)
    if len(value.encode()) > PASSWORD_MAX_BYTES:
        raise ValueError("La contraseña puede tener como máximo 72 caracteres.")
    if not any(c.isupper() for c in value):
        raise ValueError("La contraseña debe tener al menos una mayúscula.")
    if not any(c.islower() for c in value):
        raise ValueError("La contraseña debe tener al menos una minúscula.")
    if not any(c.isdigit() for c in value):
        raise ValueError("La contraseña debe tener al menos un número.")
    return value


def check_entry_year(value: int) -> int:
    if value > date.today().year:
        raise ValueError("El año de ingreso no puede ser futuro.")
    return value


class PerfilIn(BaseModel):
    """Datos del perfil académico que el usuario puede editar. El correo y la universidad no
    cambian: salen del correo institucional."""

    model_config = ConfigDict(str_strip_whitespace=True)

    nombre: str = Field(min_length=1, max_length=120)
    carrera: str = Field(min_length=1, max_length=120)
    anio_ingreso: int = Field(ge=1990)

    @field_validator("anio_ingreso")
    @classmethod
    def entry_year_not_in_future(cls, value: int) -> int:
        return check_entry_year(value)


class AsignaturaIn(BaseModel):
    """Asignatura para «mis asignaturas». Si la sigla no existe en la universidad del usuario,
    se crea con el nombre indicado."""

    model_config = ConfigDict(str_strip_whitespace=True)

    sigla: str = Field(min_length=1, max_length=20)
    nombre: str | None = Field(default=None, max_length=200)


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


class RestablecerIn(BaseModel):
    """Contraseña nueva, con el token del enlace de recuperación."""

    token: str = Field(max_length=200)
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def password_is_strong(cls, value: str) -> str:
        return check_password_strength(value)


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


class AsignaturaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sigla: str
    nombre: str


class HorarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    dia_semana: int
    es_manana: bool


class HorarioIn(BaseModel):
    # 0 = lunes ... 5 = sábado
    dia_semana: int = Field(ge=0, le=5)
    es_manana: bool


class GrupoIn(BaseModel):
    """Grupo nuevo. Si la sigla no existe en la universidad del creador, se crea la
    asignatura con `nombre_asignatura` ("buscar o crear")."""

    model_config = ConfigDict(str_strip_whitespace=True)

    sigla: str = Field(min_length=1, max_length=20)
    nombre_asignatura: str | None = Field(default=None, max_length=200)
    descripcion: str | None = Field(default=None, max_length=280)
    es_online: bool
    # Lugar si es presencial, enlace si es online; el otro se ignora
    lugar: str | None = Field(default=None, max_length=200)
    enlace: str | None = Field(default=None, max_length=500)
    # Incluye al creador
    cupo_maximo: int = Field(ge=2, le=10)
    vigente_hasta: date
    # 6 días x 2 bloques
    horarios: list[HorarioIn] = Field(min_length=1, max_length=12)


class GrupoResumenOut(BaseModel):
    """Grupo en los resultados del buscador. No incluye lugar ni enlace: solo los ven los
    integrantes (RNF de privacidad)."""

    id: int
    asignatura: AsignaturaOut
    descripcion: str | None
    es_online: bool
    cupo_maximo: int
    miembros: int
    abierto: bool
    vigente_hasta: date
    horarios: list[HorarioOut]


class IntegranteOut(BaseModel):
    """Integrante en el detalle del grupo. Sin correo: el contacto queda dentro del grupo."""

    id: int
    nombre: str
    carrera: str | None
    es_creador: bool


class GrupoDetalleOut(GrupoResumenOut):
    """Detalle del grupo (CU05). Lugar y enlace vienen solo si el usuario es integrante."""

    creador: str
    integrantes: list[IntegranteOut]
    es_miembro: bool
    # El usuario ya pidió unirse y el creador aún no responde
    solicitud_pendiente: bool
    lugar: str | None
    enlace: str | None


class SolicitudIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    mensaje: str | None = Field(default=None, max_length=280)


class SolicitanteOut(BaseModel):
    """Quien pide unirse, tal como lo ve el creador. Sin correo, igual que los integrantes."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    carrera: str | None


class SolicitudOut(BaseModel):
    id: int
    usuario: SolicitanteOut
    mensaje: str | None
    creada_en: datetime


class GrupoMioOut(GrupoResumenOut):
    """Grupo en «mis grupos». El usuario es integrante, así que ve lugar y enlace; si es el
    creador, también las solicitudes pendientes."""

    es_creador: bool
    lugar: str | None
    enlace: str | None
    solicitudes: list[SolicitudOut]


class MiSolicitudOut(BaseModel):
    """Solicitud enviada por el usuario, con el grupo como en el buscador (sin lugar ni enlace:
    los ve recién al entrar)."""

    id: int
    estado: str
    mensaje: str | None
    creada_en: datetime
    respondida_en: datetime | None
    grupo: GrupoResumenOut


class MisGruposOut(BaseModel):
    creados: list[GrupoMioOut]
    participo: list[GrupoMioOut]
