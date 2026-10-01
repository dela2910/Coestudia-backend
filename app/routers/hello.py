from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    # Usado por Render como health check
    return {"status": "ok"}


@router.get("/api/hello")
def hello():
    return {"message": "Hola desde el backend de Coestudia"}
