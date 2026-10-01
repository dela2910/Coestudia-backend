# CLAUDE.md — coestudia-backend

## Contexto del proyecto

**Coestudia** es una plataforma web para que estudiantes universitarios de pregrado (Chile) encuentren
y creen grupos de estudio por asignatura, modalidad (presencial/online) y disponibilidad horaria.
Proyecto del ramo de ingeniería de software, equipo de 5 personas, con presupuesto cercano a cero
(solo capas gratuitas).

Este repositorio es **solo el backend**. El frontend vive en otro repositorio (`coestudia-frontend`,
React + Vite) y se comunica con este por **API REST (JSON sobre HTTPS)**.

## Estado actual (actualizado 2026-10-01)

Entrega 2 (walking skeleton del backend) **completa**:

- Esqueleto FastAPI con `GET /health` y `GET /api/hello`, CORS por `FRONTEND_URL`, 3 tests.
- CI: workflow `CI` con jobs `Lint` → `Test` (PR #1).
- CD: workflow `Release y deploy` con jobs `Crear release` → `Deploy a Render` (PR #2).
- Release `v0.1.0` publicado y desplegado con el pipeline en verde.
- Producción: https://coestudia-backend.onrender.com (`/health`, `/api/hello`, `/docs`).
- `CLAUDE.md` y todos los `.md` (excepto `README.md`) están en `.gitignore`: son documentación
  personal.

`FRONTEND_URL=https://coestudia-frontend.vercel.app` definida en Render (CORS funcionando).

Flujo para publicar una versión nueva: rama → PR (CI en verde) → merge → `git tag vX.Y.Z` en `main`
→ `git push origin vX.Y.Z`.

## Objetivo: Entrega 2 — Walking Skeleton (backend)

Lo que pide la pauta del curso:

- "Hello world" conectado: el frontend consume un endpoint de este backend.
- Pipeline CI/CD con **tag + release en GitHub**.
- **CI con stages definidos** (lint → test).
- **CD: despliegue automatizado a producción**. Si no se logra automatizar, debe quedar manual y
  argumentado.

El esqueleto debe ser **mínimo**. No implementar funcionalidades del producto todavía.

### Alcance de esta tarea (hacer)

1. Estructura base del proyecto FastAPI.
2. Endpoints `GET /health` y `GET /api/hello`.
3. Configuración de CORS por variable de entorno.
4. Pruebas con pytest y lint con ruff.
5. Workflow de CI (`.github/workflows/ci.yml`).
6. Workflow de release + deploy (`.github/workflows/release.yml`).
7. `README.md` con instrucciones de ejecución local, estructura, variables de entorno y cómo
   publicar una versión.
8. `.gitignore` apropiado para Python y `.env.example`.

### Fuera de alcance (NO hacer ahora)

- Base de datos, SQLAlchemy, Alembic, modelos.
- Autenticación, JWT, usuarios.
- Endpoints de grupos, perfil o solicitudes.
- Docker, a menos que se pida explícitamente.
- Microservicios o cualquier abstracción adicional.

## Stack

- Python 3.12
- FastAPI + Uvicorn
- pytest + httpx (TestClient)
- ruff (lint y formato)
- GitHub Actions (CI y release)
- Render (despliegue del backend, capa gratuita)

## Estructura esperada

```
coestudia-backend/
├── app/
│   ├── __init__.py
│   ├── main.py          # instancia FastAPI, CORS, registro de routers
│   └── routers/
│       ├── __init__.py
│       └── hello.py     # /health y /api/hello
├── tests/
│   ├── __init__.py
│   └── test_hello.py
├── .github/workflows/
│   ├── ci.yml
│   └── release.yml
├── .env.example
├── .gitignore
├── pyproject.toml       # configuración de ruff y pytest
├── requirements.txt
├── CLAUDE.md
└── README.md
```

Se separa en `app/` y `routers/` desde el inicio para que los módulos futuros (auth, perfil, grupos,
solicitudes) tengan dónde crecer sin reestructurar. No crear esos módulos aún.

El comando de arranque es `uvicorn app.main:app`.

## Especificación de los endpoints

`GET /health` → `200` con `{"status": "ok"}`. Lo usa Render como health check.

`GET /api/hello` → `200` con `{"message": "Hola desde el backend de Coestudia"}`.

## CORS

Leer los orígenes permitidos de variables de entorno, sin dejar `"*"` en producción:

- Siempre permitir `http://localhost:5173` (Vite en desarrollo).
- Si existe la variable `FRONTEND_URL`, agregarla a la lista de orígenes permitidos.

## CI (`ci.yml`)

Se ejecuta en `pull_request` y en `push` a `main`. Dos jobs separados que sirven como stages
(si falla el lint, no se ejecutan los tests):

1. Job **`Lint`**: checkout → setup Python 3.12 (cache pip) → instalar dependencias → `ruff check .`
2. Job **`Test`** (`needs: lint`): checkout → setup Python 3.12 (cache pip) → instalar dependencias
   → `pytest`

Los nombres de los jobs (`Lint` y `Test`) son los checks requeridos por el ruleset de `main`:
si se renombran, hay que actualizar el ruleset o los PR quedan bloqueados.

## Release y deploy (`release.yml`)

Se dispara con tags que cumplan `v*` (por ejemplo `v0.1.0`):

1. Job `release`: crea el release en GitHub con notas autogeneradas (`softprops/action-gh-release@v2`,
   permiso `contents: write`).
2. Job `deploy` (`needs: release`): hace `curl -fsS -X POST` al deploy hook de Render, tomado del
   secret `RENDER_DEPLOY_HOOK`.

## Convenciones

- Código y nombres de variables en inglés; mensajes de commit, README y comentarios en español.
- Commits con prefijo convencional: `feat:`, `fix:`, `chore:`, `docs:`, `ci:`, `test:`.
- Flujo de ramas: `feat/...` → PR a **`dev`** → rama `release/vX.Y.Z` desde `dev` (con `git rm` del
  CLAUDE) → PR a **`main`** → tag `vX.Y.Z`. PRs sin aprobaciones obligatorias, pero con CI en verde.
- Este archivo está versionado **solo en `dev`** y ramas de trabajo (se agregó con `git add -f`
  porque `*.md` está en `.gitignore`). El job `Lint` falla en PRs a `main` si existe un `CLAUDE*.md`.
  El CI corre en push **solo a `main`** (no a `dev`): si corriera en `dev`, un run en verde sobre el
  mismo commit podría tapar el fallo de la guardia.
- **Nunca mergear `main` hacia `dev`**: borraría este archivo de `dev`. Los hotfix se hacen en `dev`.
- Mantener el código simple y legible; el equipo tiene experiencia limitada.
- Fijar las versiones principales en `requirements.txt` (por ejemplo `fastapi>=0.110`), sin sobre-especificar.

## Definición de terminado

- `ruff check .` pasa sin errores.
- `pytest` pasa.
- `uvicorn app.main:app --reload` levanta y responde en `/health`, `/api/hello` y `/docs`.
- El workflow de CI corre en verde en GitHub.
- El README permite a otro integrante levantar el proyecto desde cero.

## Pasos que debe hacer el humano (no intentar automatizarlos)

Claude Code no tiene acceso a estos paneles; dejar las instrucciones en el README o avisar en el
resumen final:

1. ~~Crear el servicio en Render~~ (hecho): `coestudia-backend`, Python 3, región Virginia (US East),
   plan Free, `PYTHON_VERSION=3.12.7`, health check `/health`, auto-deploy **Off**.
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
2. ~~Guardar el deploy hook como secret `RENDER_DEPLOY_HOOK`~~ (hecho).
3. ~~Definir `FRONTEND_URL` en Render~~ (hecho): `https://coestudia-frontend.vercel.app`.
4. ~~Activar la protección de `main`~~ (hecho): ruleset "Protect main" activo, PR obligatorio,
   0 aprobaciones (cada uno puede mergear su PR), checks requeridos `Lint` y `Test`,
   bloquea force push y borrado de `main`.

## Reglas de trabajo para Claude Code

- Si falta algo que no se puede inferir (por ejemplo el nombre de usuario de GitHub o si `gh` está
  autenticado), **preguntar antes de asumir**.
- Si el repositorio remoto aún no existe y `gh` está autenticado, se puede crear con
  `gh repo create coestudia-backend`. Si no, indicar al usuario que lo cree.
- **Nunca** escribir secretos, tokens ni URLs privadas en archivos versionados. Usar `.env` (ignorado
  por git) y `.env.example` con valores de ejemplo.
- No hacer `git push --force` ni reescribir historia.
- Antes de dar la tarea por terminada, ejecutar `ruff check .` y `pytest` y mostrar el resultado.
- No agregar dependencias que no se usen.
- Al terminar, entregar un resumen corto con: archivos creados, comandos ejecutados, y la lista de
  pasos manuales pendientes del bloque anterior.

## Para la presentación (evidencia que se va a mostrar)

Dejar el proyecto de modo que sea fácil capturar: el pipeline de GitHub Actions en verde con sus
stages visibles, la página de Releases con `v0.1.0`, la documentación automática en `/docs`, y la
respuesta de `/api/hello` en producción.
