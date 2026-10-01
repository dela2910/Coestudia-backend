# Coestudia — backend

API REST de Coestudia, hecha con FastAPI. El frontend vive en el repositorio `coestudia-frontend`.

## Ejecutar en local

Requiere Python 3.12.

```bash
python -m venv .venv
source .venv/bin/activate        # En Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # opcional, solo si necesitas FRONTEND_URL
uvicorn app.main:app --reload
```

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
└── routers/
    └── hello.py     # /health y /api/hello
tests/
└── test_hello.py
.github/workflows/
├── ci.yml           # lint → test
└── release.yml      # tag v* → release en GitHub → deploy a Render
```

## Variables de entorno

| Variable       | Descripción                                                    |
| -------------- | -------------------------------------------------------------- |
| `FRONTEND_URL` | URL del frontend en producción; se agrega a los orígenes CORS. |

`http://localhost:5173` (Vite) siempre está permitido.

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
