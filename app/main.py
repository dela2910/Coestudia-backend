import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import hello

app = FastAPI(title="Coestudia API", version="0.1.0")

# Orígenes permitidos: Vite en desarrollo y, si existe, la URL del frontend en producción
allowed_origins = ["http://localhost:5173"]
frontend_url = os.getenv("FRONTEND_URL")
if frontend_url:
    allowed_origins.append(frontend_url.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(hello.router)
