# Coestudia — backend

API REST de Coestudia, hecha con FastAPI. El frontend vive en el repositorio `coestudia-frontend`.

## Ejecutar en local

Requiere Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate        # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head             # crea las tablas (por defecto en SQLite, ./coestudia.db)
uvicorn app.main:app --reload
```

Para trabajar contra PostgreSQL en vez de SQLite, exporta `DATABASE_URL` antes de correr
`alembic` y `uvicorn` (ver `.env.example`).

- http://localhost:8000/health
- http://localhost:8000/api/hello
- http://localhost:8000/docs (documentación automática)

## Lint y tests

```bash
ruff check .
pytest
```

Ambos se ejecutan automáticamente en GitHub Actions en cada pull request y en cada push a `main`.

## Estructura

```
app/
├── main.py          # instancia FastAPI, CORS y registro de routers
├── db.py            # conexión a la base, sesión y dependencia get_db
├── models.py        # modelo de datos (tablas SQLAlchemy)
├── schemas.py       # entrada y salida de la API (Pydantic)
├── security.py      # contraseñas, JWT, tokens de correo y current_user
├── mailer.py        # envío de correos (Brevo)
├── universities.py  # universidad según el dominio del correo
└── routers/
    ├── hello.py     # /health y /api/hello
    └── auth.py      # /api/auth: registro, verificación, login y /me
migrations/          # migraciones de Alembic
tests/
├── conftest.py      # cliente de pruebas con base SQLite y bandeja de correos
├── test_auth.py
├── test_hello.py
└── test_models.py   # reglas del esquema y que las migraciones calcen con models.py
.github/workflows/
├── ci.yml           # lint → test
└── release.yml      # tag v* → release en GitHub → deploy a Render
```

## Variables de entorno

| Variable       | Descripción                                                          |
| -------------- | -------------------------------------------------------------------- |
| `FRONTEND_URL` | URL del frontend en producción; se agrega a los orígenes CORS.       |
| `DATABASE_URL` | Connection string de PostgreSQL (Neon). Sin ella se usa SQLite local. |
| `JWT_SECRET`    | Clave para firmar las sesiones. Obligatoria en producción (Render).   |
| `BREVO_API_KEY` | API key de Brevo. Sin ella los correos se muestran en la consola.    |
| `MAIL_FROM`     | Remitente de los correos (debe estar validado en Brevo).             |

`http://localhost:5173` (Vite) siempre está permitido.

## Autenticación

| Método y ruta                         | Qué hace                                                   |
| ------------------------------------- | ---------------------------------------------------------- |
| `POST /api/auth/registro`             | Crea la cuenta y envía el enlace de verificación (201)      |
| `POST /api/auth/verificar`            | Verifica el correo con el token del enlace e inicia sesión |
| `POST /api/auth/reenviar-verificacion`| Envía un enlace nuevo; los anteriores dejan de servir (202) |
| `POST /api/auth/login`                | Devuelve el JWT (401 si falla, 403 si falta verificar)     |
| `GET /api/auth/me`                    | Usuario de la sesión actual                                |

El enlace del correo apunta a `FRONTEND_URL/?verificar=<token>`. En local, sin `BREVO_API_KEY`,
el enlace aparece en la consola del backend: cópialo en el navegador.

Para proteger un endpoint, pide el usuario actual. Si no hay una sesión válida, responde 401:

```python
from fastapi import Depends

from app.models import Usuario
from app.security import current_user


@router.get("/api/grupos/mios")
def my_groups(user: Usuario = Depends(current_user)):
    ...
```

En los tests, el fixture `client` de `tests/conftest.py` usa una base nueva y `outbox` guarda los
correos en vez de enviarlos (ver `tests/test_auth.py`).

## Base de datos y migraciones

El esquema vive en `app/models.py` y se aplica con migraciones de Alembic. Si cambias un modelo:

```bash
alembic revision --autogenerate -m "describe el cambio"   # revisa el archivo generado
alembic upgrade head
```

El test `test_migrations_match_models` falla si `models.py` cambia sin su migración.

## Flujo de ramas

```
feat/x ──PR──► dev ──► release/vX.Y.Z ──PR──► main ──► tag vX.Y.Z (deploy)
```

- `main`: producción. Solo recibe PRs desde ramas `release/*`. Protegida (PR + CI en verde).
- `dev`: integración. Todo el trabajo diario entra aquí por PR desde ramas `feat/...`, `fix/...`,
  `chore/...`.
- `dev` contiene documentación personal (`CLAUDE*.md`) que **no** debe llegar a `main`. Para
  publicar:

  ```bash
  git switch dev && git pull
  git switch -c release/vX.Y.Z
  git rm CLAUDE*.md
  git commit -m "chore: preparar release vX.Y.Z"
  git push -u origin release/vX.Y.Z
  gh pr create --base main
  ```

  El CI hace fallar cualquier PR a `main` que traiga un `CLAUDE*.md`.
- **Nunca mergear `main` hacia `dev`**: borraría el CLAUDE.md de `dev`. Los arreglos urgentes
  también se hacen en `dev` y se publican con una release nueva.

Los commits usan prefijos convencionales: `feat:`, `fix:`, `chore:`, `docs:`, `ci:`, `test:`.

## Publicar una versión

El deploy a producción (Render) se hace creando un tag desde `main` actualizado:

```bash
git switch main
git pull                         # main ya con la release mergeada
git tag v0.1.0
git push origin v0.1.0
```

El workflow `release.yml` crea el release en GitHub con notas autogeneradas y luego dispara el
deploy en Render mediante el secret `RENDER_DEPLOY_HOOK`. Usar versionado semántico
(`vMAYOR.MENOR.PARCHE`).
