"""Modelo de datos de Coestudia.

Parte del diagrama de la Entrega 2 (lámina «Modelo de datos») con los ajustes del feedback:
términos y condiciones, token de cambio de contraseña, eliminación de cuenta y de grupos
(por decisión o por vigencia), cancelar solicitudes, booleanos donde hay solo dos opciones y
fechas con zona horaria.

Los nombres de tablas y columnas siguen el diagrama, para que el código se pueda contrastar
con el modelo entregado.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TIMESTAMP

from app.db import Base

# Todas las fechas con hora se guardan como timestamptz (TIMESTAMP WITH TIME ZONE) en PostgreSQL
TIMESTAMPTZ = TIMESTAMP(timezone=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_in_chile() -> date:
    # vigente_hasta es una fecha sin hora: se compara con el día actual en Chile, no en UTC
    return datetime.now(ZoneInfo("America/Santiago")).date()


# Fecha de creación que se llena sola al insertar
def created_at() -> Mapped[datetime]:
    return mapped_column(TIMESTAMPTZ, default=now_utc)


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Correo institucional; se guarda en minúsculas
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nombre: Mapped[str] = mapped_column(String(120))
    # Se deduce del dominio del correo
    universidad: Mapped[str] = mapped_column(String(120))
    carrera: Mapped[str | None] = mapped_column(String(120))
    anio_ingreso: Mapped[int | None] = mapped_column(Integer)
    # Consentimiento de la Ley 19.628: no se puede registrar sin aceptar los términos
    terminos_aceptados_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    # null = correo sin verificar
    email_verificado_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    # Los JWT emitidos antes de esta fecha dejan de valer
    contrasena_cambiada_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    # null = cuenta activa; se elimina de forma lógica para no romper el historial de los grupos
    eliminado_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    creado_en: Mapped[datetime] = created_at()

    asignaturas: Mapped[list["UsuarioAsignatura"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan"
    )
    tokens: Mapped[list["TokenCorreo"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan"
    )
    grupos_creados: Mapped[list["GrupoEstudio"]] = relationship(back_populates="creador")
    membresias: Mapped[list["MiembroGrupo"]] = relationship(back_populates="usuario")
    solicitudes: Mapped[list["Solicitud"]] = relationship(back_populates="usuario")


class Asignatura(Base):
    __tablename__ = "asignatura"
    __table_args__ = (UniqueConstraint("sigla", "universidad"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Sigla normalizada (mayúsculas, sin espacios), ej. IIC2143
    sigla: Mapped[str] = mapped_column(String(20))
    nombre: Mapped[str] = mapped_column(String(200))
    # La del usuario que la creó
    universidad: Mapped[str] = mapped_column(String(120))

    grupos: Mapped[list["GrupoEstudio"]] = relationship(back_populates="asignatura")


class UsuarioAsignatura(Base):
    """«Mis asignaturas»: lo que cursa cada usuario."""

    __tablename__ = "usuario_asignatura"

    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), primary_key=True
    )
    asignatura_id: Mapped[int] = mapped_column(
        ForeignKey("asignatura.id", ondelete="CASCADE"), primary_key=True
    )
    agregada_en: Mapped[datetime] = created_at()

    usuario: Mapped[Usuario] = relationship(back_populates="asignaturas")
    asignatura: Mapped[Asignatura] = relationship()


class TokenCorreo(Base):
    __tablename__ = "token_correo"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('verificacion', 'recuperar_contrasena', 'cambio_contrasena')",
            name="ck_token_correo_tipo",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id", ondelete="CASCADE"))
    # SHA-256 del token enviado por correo; el token en claro nunca se guarda
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # verificacion: confirmar el correo al registrarse
    # recuperar_contrasena: olvidó la contraseña y no puede iniciar sesión
    # cambio_contrasena: confirmar por correo un cambio pedido desde el perfil
    tipo: Mapped[str] = mapped_column(String(30))
    expira_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    # null = no usado (un solo uso)
    usado_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    creado_en: Mapped[datetime] = created_at()

    usuario: Mapped[Usuario] = relationship(back_populates="tokens")


class GrupoEstudio(Base):
    __tablename__ = "grupo_estudio"
    __table_args__ = (
        CheckConstraint("cupo_maximo >= 2", name="ck_grupo_cupo"),
        # Lugar obligatorio si es presencial, enlace si es online
        CheckConstraint(
            "(es_online AND enlace IS NOT NULL) OR (NOT es_online AND lugar IS NOT NULL)",
            name="ck_grupo_lugar_enlace",
        ),
        # El motivo se registra solo cuando el grupo está eliminado
        CheckConstraint(
            "(eliminado_en IS NULL) = (eliminado_por_vigencia IS NULL)",
            name="ck_grupo_motivo_eliminacion",
        ),
        # Para el buscador (RNF: búsqueda con filtros en < 2 s)
        Index("ix_grupo_busqueda", "asignatura_id", "abierto", "es_online"),
        Index("ix_grupo_creador", "creador_id"),
        Index("ix_grupo_vigencia", "vigente_hasta"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    descripcion: Mapped[str | None] = mapped_column(Text)
    asignatura_id: Mapped[int] = mapped_column(ForeignKey("asignatura.id"))
    # Usuario que lo creó y lo administra
    creador_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"))
    # true = online, false = presencial
    es_online: Mapped[bool] = mapped_column(Boolean)
    # Solo presencial
    lugar: Mapped[str | None] = mapped_column(String(200))
    # Solo online
    enlace: Mapped[str | None] = mapped_column(String(500))
    cupo_maximo: Mapped[int] = mapped_column(Integer)
    # false = cerrado (se completó el cupo o lo cerró el creador)
    abierto: Mapped[bool] = mapped_column(Boolean, default=True)
    # Fecha que elige el creador; después de ella el grupo se elimina por vigencia
    vigente_hasta: Mapped[date] = mapped_column(Date)
    # null = grupo activo; se elimina de forma lógica
    eliminado_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    # true = venció su vigencia, false = el creador decidió eliminarlo
    eliminado_por_vigencia: Mapped[bool | None] = mapped_column(Boolean)
    creado_en: Mapped[datetime] = created_at()

    asignatura: Mapped[Asignatura] = relationship(back_populates="grupos")
    creador: Mapped[Usuario] = relationship(back_populates="grupos_creados")
    horarios: Mapped[list["HorarioGrupo"]] = relationship(
        back_populates="grupo", cascade="all, delete-orphan"
    )
    miembros: Mapped[list["MiembroGrupo"]] = relationship(
        back_populates="grupo", cascade="all, delete-orphan"
    )
    solicitudes: Mapped[list["Solicitud"]] = relationship(
        back_populates="grupo", cascade="all, delete-orphan"
    )
    archivos: Mapped[list["Archivo"]] = relationship(
        back_populates="grupo", cascade="all, delete-orphan"
    )
    mensajes: Mapped[list["MensajeAsistente"]] = relationship(
        back_populates="grupo", cascade="all, delete-orphan"
    )


class HorarioGrupo(Base):
    __tablename__ = "horario_grupo"
    __table_args__ = (
        CheckConstraint("dia_semana BETWEEN 0 AND 5", name="ck_horario_dia"),
        # Para filtrar por horario en el buscador
        Index("ix_horario_dia_bloque", "dia_semana", "es_manana"),
    )

    grupo_id: Mapped[int] = mapped_column(
        ForeignKey("grupo_estudio.id", ondelete="CASCADE"), primary_key=True
    )
    # 0 = lunes ... 5 = sábado
    dia_semana: Mapped[int] = mapped_column(Integer, primary_key=True)
    # true = bloque de la mañana, false = bloque de la tarde
    es_manana: Mapped[bool] = mapped_column(Boolean, primary_key=True)

    grupo: Mapped[GrupoEstudio] = relationship(back_populates="horarios")


class MiembroGrupo(Base):
    """Integrantes del grupo; el creador también es miembro."""

    __tablename__ = "miembro_grupo"
    __table_args__ = (Index("ix_miembro_usuario", "usuario_id"),)

    grupo_id: Mapped[int] = mapped_column(
        ForeignKey("grupo_estudio.id", ondelete="CASCADE"), primary_key=True
    )
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuario.id", ondelete="CASCADE"), primary_key=True
    )
    unido_en: Mapped[datetime] = created_at()

    grupo: Mapped[GrupoEstudio] = relationship(back_populates="miembros")
    usuario: Mapped[Usuario] = relationship(back_populates="membresias")


class Solicitud(Base):
    __tablename__ = "solicitud"
    __table_args__ = (
        # cancelada: quien la envió la retiró antes de que el creador respondiera
        CheckConstraint(
            "estado IN ('pendiente', 'aceptada', 'rechazada', 'cancelada')",
            name="ck_solicitud_estado",
        ),
        # Las fechas de cierre calzan con el estado
        CheckConstraint(
            "(respondida_en IS NOT NULL) = (estado IN ('aceptada', 'rechazada'))",
            name="ck_solicitud_respondida",
        ),
        CheckConstraint(
            "(cancelada_en IS NOT NULL) = (estado = 'cancelada')",
            name="ck_solicitud_cancelada",
        ),
        # Una sola solicitud pendiente por usuario y grupo
        Index(
            "uq_solicitud_pendiente",
            "grupo_id",
            "usuario_id",
            unique=True,
            sqlite_where=text("estado = 'pendiente'"),
            postgresql_where=text("estado = 'pendiente'"),
        ),
        Index("ix_solicitud_usuario", "usuario_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    grupo_id: Mapped[int] = mapped_column(ForeignKey("grupo_estudio.id", ondelete="CASCADE"))
    # Quien pide unirse
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id", ondelete="CASCADE"))
    mensaje: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    creada_en: Mapped[datetime] = created_at()
    # Cuando el creador la aceptó o rechazó
    respondida_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    # Cuando quien la envió la canceló
    cancelada_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    grupo: Mapped[GrupoEstudio] = relationship(back_populates="solicitudes")
    usuario: Mapped[Usuario] = relationship(back_populates="solicitudes")


class Archivo(Base):
    __tablename__ = "archivo"
    __table_args__ = (Index("ix_archivo_grupo", "grupo_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    grupo_id: Mapped[int] = mapped_column(ForeignKey("grupo_estudio.id", ondelete="CASCADE"))
    # Usuario que lo subió
    subido_por_id: Mapped[int] = mapped_column(ForeignKey("usuario.id"))
    # Nombre original, ej. Guía integrales.pdf
    nombre: Mapped[str] = mapped_column(String(255))
    tipo_mime: Mapped[str] = mapped_column(String(100))
    tamano_bytes: Mapped[int] = mapped_column(Integer)
    # Ruta del archivo en el storage externo (Backblaze B2)
    clave_almacenamiento: Mapped[str] = mapped_column(String(500), unique=True)
    # Texto que lee el asistente de IA
    texto_extraido: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = created_at()

    grupo: Mapped[GrupoEstudio] = relationship(back_populates="archivos")
    subido_por: Mapped[Usuario] = relationship()


class MensajeAsistente(Base):
    __tablename__ = "mensaje_asistente"
    __table_args__ = (Index("ix_mensaje_conversacion", "grupo_id", "usuario_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    grupo_id: Mapped[int] = mapped_column(ForeignKey("grupo_estudio.id", ondelete="CASCADE"))
    # Dueño de la conversación
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuario.id", ondelete="CASCADE"))
    # true = lo escribió el asistente, false = lo escribió el usuario
    es_asistente: Mapped[bool] = mapped_column(Boolean)
    contenido: Mapped[str] = mapped_column(Text)
    creado_en: Mapped[datetime] = created_at()

    grupo: Mapped[GrupoEstudio] = relationship(back_populates="mensajes")
    usuario: Mapped[Usuario] = relationship()
    # Archivos que el asistente citó en esta respuesta
    fuentes: Mapped[list[Archivo]] = relationship(secondary="fuente_mensaje")


class FuenteMensaje(Base):
    __tablename__ = "fuente_mensaje"

    mensaje_id: Mapped[int] = mapped_column(
        ForeignKey("mensaje_asistente.id", ondelete="CASCADE"), primary_key=True
    )
    archivo_id: Mapped[int] = mapped_column(
        ForeignKey("archivo.id", ondelete="CASCADE"), primary_key=True
    )
