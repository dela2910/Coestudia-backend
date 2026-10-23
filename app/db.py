import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def database_url() -> str:
    # En local y en tests se usa SQLite; en producción, DATABASE_URL apunta a Neon (PostgreSQL)
    url = os.getenv("DATABASE_URL", "sqlite:///./coestudia.db")
    # Neon entrega URLs postgresql://...; SQLAlchemy necesita saber que el driver es psycopg 3
    if url.startswith("postgres://"):
        url = "postgresql://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        # SQLite no revisa las claves foráneas a menos que se active en cada conexión
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


engine = make_engine(database_url())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    # Dependencia de FastAPI: abre una sesión por request y la cierra al terminar
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
