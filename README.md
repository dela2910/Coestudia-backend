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

Para tener usuarios y grupos con los que probar, carga los datos de ejemplo (solo SQLite):

```bash
python -m app.seed               # entra con ana.rojas@uc.cl / Coestudia2026
```

Para usar variables locales (por ejemplo la API key de Brevo), crea un `.env` con solo las que
necesites (ver `.env.example`) y arranca con `uvicorn app.main:app --reload --env-file .env`.
No copies `DATABASE_URL` de ejemplo: si no la defines se usa SQLite.

**No pongas la `DATABASE_URL` de producción (Neon) en tu `.env`:** el servidor local escribiría en
la base real (usuarios, mensajes y pruebas). Las migraciones de producción las aplica el CD (ver
«Cómo publicar una versión»). Para probar con PostgreSQL, usa una base o branch de Neon aparte y
exporta su `DATABASE_URL` antes de correr `alembic` y `uvicorn`. Los tests (`pytest`) siempre usan
una SQLite temporal.

- http://localhost:8000/health
- http://localhost:8000/api/hello
- http://localhost:8000/docs (documentación automática)

## Lint y tests

```bash
ruff check .
pytest
pytest --cov=app                 # con el porcentaje de cobertura por archivo
```

Ambos se ejecutan automáticamente en GitHub Actions en cada pull request y en cada push a `main`.

Además, el CI analiza:

- **Dependencias (Trivy)**: busca vulnerabilidades conocidas en las dependencias. Falla si hay
  alguna alta o crítica que ya tenga versión corregida; el detalle queda en el resumen del job.
- **Calidad (SonarQube)**: analiza el código y la cobertura en SonarQube Cloud
  (`sonar-project.properties`) y falla si no se cumple el quality gate. Necesita el secret
  `SONAR_TOKEN`; sin él, el paso se omite con un aviso.

## Estructura

```
app/
├── main.py          # instancia FastAPI, CORS y registro de routers
├── db.py            # conexión a la base, sesión y dependencia get_db
├── models.py        # modelo de datos (tablas SQLAlchemy)
├── schemas.py       # entrada y salida de la API (Pydantic)
├── security.py      # contraseñas, JWT, tokens de correo y current_user
├── rate_limit.py    # límite de intentos fallidos de login
├── mailer.py        # envío de correos (Brevo)
├── storage.py       # dónde se guardan los archivos (Backblaze B2 o carpeta local)
├── universities.py  # universidad según el dominio del correo
├── text.py          # normalizar siglas y nombres (sin tildes ni espacios)
├── seed.py          # datos de ejemplo: python -m app.seed
└── routers/
    ├── hello.py     # /health y /api/hello
    ├── auth.py      # /api/auth: registro, verificación, login y /me
    ├── perfil.py    # /api/perfil: editar el perfil y «mis asignaturas»
    ├── asignaturas.py  # /api/asignaturas: catálogo para autocompletar siglas
    ├── grupos.py    # /api/grupos: buscar, detalle, crear, mis grupos, pedir unirse, administrar y abandonar
    ├── solicitudes.py  # /api/solicitudes: mis solicitudes; aceptar o rechazar (el creador)
    ├── archivos.py  # archivos del grupo: subir, listar, descargar y eliminar
    └── foro.py      # foro del grupo: temas y respuestas
migrations/          # migraciones de Alembic
tests/
├── conftest.py      # cliente de pruebas con base SQLite y bandeja de correos
├── test_auth.py
├── test_grupos.py
├── test_solicitudes.py
├── test_administrar.py
├── test_archivos.py
├── test_foro.py
├── test_hello.py
├── test_rate_limit.py
├── test_seed.py
└── test_models.py   # reglas del esquema y que las migraciones calcen con models.py
.github/workflows/
├── ci.yml           # lint → test → SonarQube, y Trivy (dependencias)
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
| `B2_BUCKET`     | Bucket de Backblaze B2 para los archivos. Sin él se usa `UPLOADS_DIR`. |
| `B2_ENDPOINT`, `B2_KEY_ID`, `B2_APPLICATION_KEY` | Endpoint S3 y application key de B2.   |
| `UPLOADS_DIR`   | Carpeta local para los archivos en desarrollo (por defecto `uploads`). |

`http://localhost:5173` (Vite) siempre está permitido.

## Autenticación

| Método y ruta                         | Qué hace                                                   |
| ------------------------------------- | ---------------------------------------------------------- |
| `POST /api/auth/registro`             | Crea la cuenta y envía el enlace de verificación (201)      |
| `POST /api/auth/verificar`            | Verifica el correo con el token del enlace e inicia sesión |
| `POST /api/auth/reenviar-verificacion`| Envía un enlace nuevo; los anteriores dejan de servir (202) |
| `POST /api/auth/login`                | Devuelve el JWT (401 si falla, 403 si falta verificar)     |
| `GET /api/auth/me`                    | Usuario de la sesión actual                                |

Después de 5 intentos fallidos de login con el mismo correo y conexión en 15 minutos, `/login`
responde 429 hasta que pasen los 15 minutos (`app/rate_limit.py`, en memoria). El reenvío del
correo de verificación está limitado a 1 por minuto y 5 al día por cuenta.

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

## Grupos

`GET /api/grupos` (requiere sesión) busca grupos de la universidad del usuario:

| Parámetro   | Ejemplo               | Qué filtra                                                 |
| ----------- | --------------------- | ---------------------------------------------------------- |
| `q`         | `calculo`, `mat 1620` | Sigla o nombre de la asignatura, sin importar tildes       |
| `es_online` | `true` / `false`      | Modalidad; sin el parámetro, cualquiera                    |
| `dia`       | `dia=0&dia=2`         | Días (0 = lunes … 5 = sábado)                              |
| `es_manana` | `true` / `false`      | Bloque; con `dia`, ambos deben calzar en un mismo horario  |

Devuelve solo grupos no eliminados y vigentes (`vigente_hasta` desde hoy, hora de Chile), con los
abiertos y con cupo primero. `abierto` es solo la decisión del creador: un grupo lleno sigue abierto
y las solicitudes nuevas quedan en espera. No incluye `lugar` ni `enlace`: solo los ven los integrantes.

`GET /api/grupos/{id}` (requiere sesión) devuelve el detalle: lo mismo que el buscador más
`creador`, `integrantes` (nombre, carrera y si es el creador; sin correo) y `es_miembro`. `lugar` y
`enlace` vienen solo si el usuario es integrante. Los grupos de otra universidad, eliminados o
vencidos responden 404.

`POST /api/grupos` (requiere sesión) crea un grupo en la universidad del usuario y responde 201 con
el mismo formato del detalle. El creador queda como primer integrante.

| Campo               | Ejemplo                                     | Regla                                   |
| ------------------- | ------------------------------------------- | --------------------------------------- |
| `sigla`             | `mat 1620`                                  | Se normaliza (`MAT1620`)                |
| `nombre_asignatura` | `Cálculo II`                                | Solo si la sigla aún no existe          |
| `descripcion`       | `Repaso para el certamen`                   | Opcional, hasta 280 caracteres          |
| `es_online`         | `false`                                     |                                         |
| `lugar` / `enlace`  | `Biblioteca` / `https://meet.google.com/…`  | Lugar si es presencial, enlace si online |
| `cupo_maximo`       | `4`                                         | Entre 2 y 10, incluye al creador        |
| `vigente_hasta`     | `2026-12-04`                                | Posterior a hoy                         |
| `horarios`          | `[{"dia_semana": 1, "es_manana": false}]`   | Al menos uno; los repetidos se ignoran  |

Si la sigla no existe y no viene `nombre_asignatura`, responde 404 para que el frontend pida el
nombre (igual que en «mis asignaturas»).

`GET /api/grupos/mios` (requiere sesión) devuelve `{creados, participo}`: los grupos vigentes donde
el usuario es integrante, con `lugar`, `enlace` y `es_creador`. En los que creó vienen además las
`solicitudes` pendientes (quién la envió, su carrera y el mensaje).

### Solicitudes para unirse

| Método y ruta                           | Quién        | Qué hace                                         |
| --------------------------------------- | ------------ | ------------------------------------------------ |
| `POST /api/grupos/{id}/solicitudes`     | Cualquiera   | Pide unirse; `{mensaje?}` opcional (201)         |
| `DELETE /api/grupos/{id}/solicitudes`   | Quien la pidió | Cancela su solicitud pendiente                 |
| `GET /api/solicitudes/mias`             | Quien las envió | «Mis solicitudes»: pendientes, en espera, aceptadas y rechazadas |
| `POST /api/solicitudes/{id}/aceptar`    | Creador      | Suma al integrante; si se llena el cupo, las demás quedan `en_espera` |
| `POST /api/solicitudes/{id}/rechazar`   | Creador      | La rechaza                                       |

No se puede pedir unirse a un grupo del que ya eres integrante, cerrado o con otra solicitud
activa tuya (409).

**Lista de espera.** Si el grupo está lleno, la solicitud se crea con estado `en_espera` (el
detalle trae `solicitud_en_espera: true`). Cuando el creador acepta la que completa el cupo, las
demás pendientes pasan a `en_espera`. Cuando se libera un cupo (alguien abandona, el creador lo
expulsa o sube el cupo), las que estaban en espera vuelven a `pendiente`. Las en espera se pueden
rechazar o cancelar, pero no aceptar (409). En «mis grupos» cada solicitud trae su `estado`. Pedir y cancelar devuelven el detalle del grupo, con
`solicitud_pendiente`; aceptar y rechazar devuelven el grupo como en «mis grupos». «Mis
solicitudes» trae solo las de grupos vigentes, las más recientes primero y sin las canceladas; el
grupo viene como en el buscador, sin lugar ni enlace. A quien no es el
creador, las rutas de `/api/solicitudes` le responden 404.

**Aviso por correo.** Al aceptar o rechazar, se le envía un correo a quien pidió unirse
(`send_request_answered_email` en `app/mailer.py`). Se envía en segundo plano, después de
responderle al creador; si Brevo falla, la respuesta igual queda guardada y el error queda en el
log. Sin `BREVO_API_KEY`, el correo se muestra en la consola del backend.

### Administrar y abandonar

| Método y ruta                                | Quién       | Qué hace                                       |
| -------------------------------------------- | ----------- | ---------------------------------------------- |
| `PUT /api/grupos/{id}`                       | Creador     | Edita los datos y `abierto` (cerrar o reabrir) |
| `DELETE /api/grupos/{id}`                    | Creador     | Elimina el grupo (lógico) (204)                |
| `DELETE /api/grupos/{id}/integrantes/{usuario}` | Creador  | Expulsa a un integrante                        |
| `POST /api/grupos/{id}/abandonar`            | Integrante  | Deja el grupo (204)                            |

`PUT` recibe lo mismo que crear, sin `sigla` ni `nombre_asignatura`, más `abierto`; el cupo no
puede quedar bajo los integrantes actuales (422). Editar y expulsar devuelven el detalle del grupo.
A quien no es el creador, las rutas de administrar le responden 403. El creador no puede
expulsarse ni abandonar (409): para dejar el grupo, lo elimina.

`app/text.py` tiene `normalize_code()` para guardar las siglas igual que se buscan (B05).

### Archivos del grupo

Solo los integrantes del grupo. Todas requieren sesión.

| Método y ruta                         | Qué hace                                                       |
| ------------------------------------- | -------------------------------------------------------------- |
| `GET /api/grupos/{id}/archivos`       | Archivos del grupo, los más recientes primero                  |
| `POST /api/grupos/{id}/archivos`      | Sube un archivo (`multipart/form-data`, campo `archivo`) (201) |
| `GET /api/archivos/{id}/descarga`     | Descarga el archivo (siempre como adjunto)                     |
| `DELETE /api/archivos/{id}`           | Lo elimina quien lo subió o el creador del grupo (204)         |

Hasta 10 MB por archivo (413) y 100 por grupo (409). Extensiones aceptadas (otras responden 422):

- Documentos: `pdf`, `doc`, `docx`, `odt`, `rtf`
- Presentaciones: `ppt`, `pptx`, `odp`
- Planillas: `xls`, `xlsx`, `ods`, `csv`
- Texto: `txt`, `md`
- Imágenes: `png`, `jpg`, `jpeg`, `gif`, `webp`, `heic`

 El tipo MIME se
deduce de la extensión. De los archivos de texto se guarda `texto_extraido` para el asistente.
A quien no es integrante, listar y subir le responden 403, y descargar o eliminar, 404.

Los archivos se guardan en Backblaze B2 si está definida `B2_BUCKET` (el disco de Render se borra
en cada deploy); si no, en la carpeta `UPLOADS_DIR`. Para configurar B2: crear un bucket
**privado**, una application key con acceso a ese bucket, y definir las cuatro variables `B2_*`
en Render (el endpoint aparece en el detalle del bucket, ej. `https://s3.us-west-004.backblazeb2.com`).

### Foro del grupo

Temas con su pregunta (por ejemplo «Guía 2 – Ejercicio 3») y las respuestas debajo, en orden. Solo
los integrantes del grupo. Todas requieren sesión.

| Método y ruta                         | Qué hace                                                       |
| ------------------------------------- | -------------------------------------------------------------- |
| `GET /api/grupos/{id}/foro`           | Temas del grupo, primero los de actividad más reciente         |
| `POST /api/grupos/{id}/foro`          | Abre un tema: `{titulo, contenido}` (201)                      |
| `GET /api/foro/{id}`                  | El tema con todas sus respuestas                               |
| `POST /api/foro/{id}/respuestas`      | Responde el tema: `{contenido}` (201); el tema sube en la lista |

Título de hasta 150 caracteres y textos de hasta 5000 (422 si no). A quien no es integrante, la
lista y abrir un tema le responden 403, y ver o responder un tema, 404. Lo que escribió quien deja
el grupo se conserva. No hay consultas periódicas: el frontend carga el foro al abrirlo y con el
botón «Actualizar», para no mantener despierta la base de datos (capa gratuita de Neon).

## Perfil y mis asignaturas

Todas requieren sesión.

| Método y ruta                         | Qué hace                                                    |
| ------------------------------------- | ----------------------------------------------------------- |
| `PUT /api/perfil`                     | Cambia `nombre`, `carrera` y `anio_ingreso` (no el correo)  |
| `GET /api/perfil/asignaturas`         | Asignaturas del usuario, ordenadas por sigla                |
| `POST /api/perfil/asignaturas`        | Agrega `{sigla, nombre?}`; si la sigla no existe, la crea (201) |
| `DELETE /api/perfil/asignaturas/{id}` | La quita de la lista del usuario, no del catálogo (204)     |
| `GET /api/asignaturas`                | Catálogo de asignaturas de la universidad del usuario       |

`POST /api/perfil/asignaturas` busca la sigla normalizada (`mat 1620` → `MAT1620`) en la
universidad del usuario. Si no existe y no viene `nombre`, responde 404 para que el frontend pida
el nombre. Agregar una que ya está en la lista no es error. Máximo 10 asignaturas por usuario.

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

El workflow `release.yml` tiene tres etapas: `Crear release` (release en GitHub con notas
autogeneradas) → `Migrar base de datos` (`alembic upgrade head` en Neon con el secret
`DATABASE_URL`) → `Deploy a Render` (deploy hook del secret `RENDER_DEPLOY_HOOK`). Si una migración
falla, no se despliega. Las migraciones deben ser compatibles con la versión anterior por un rato:
se aplican antes de que Render termine de levantar el código nuevo. Usar versionado semántico
(`vMAYOR.MENOR.PARCHE`).
