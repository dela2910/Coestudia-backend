"""Core Web Vitals (LCP, INP, CLS, FCP, TTFB) medidos en los navegadores de los usuarios.

El frontend las manda con navigator.sendBeacon al ocultarse la página. sendBeacon no permite
agregar el token de sesión ni mandar Content-Type: application/json a otro origen sin una
consulta CORS previa, así que el endpoint es público y lee el cuerpo como texto. Las mediciones
son anónimas.
"""

import json
import math
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import METRICAS_WEB, MetricaWeb, Usuario, now_utc
from app.rate_limit import vitals_limiter
from app.schemas import MetricaWebIn, ResumenMetricaOut
from app.security import current_user

router = APIRouter(prefix="/api/metricas-web", tags=["metricas-web"])

# Un envío trae las métricas acumuladas de una página (5 como máximo, más algún reenvío)
MAX_METRICAS_POR_ENVIO = 20
MAX_BYTES_POR_ENVIO = 8 * 1024

_lote = TypeAdapter(list[MetricaWebIn])


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def record_metrics(request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else ""
    key = ("metricas-web", client_ip)
    wait = vitals_limiter.retry_after(key)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Demasiados envíos de métricas.",
            headers={"Retry-After": str(wait)},
        )
    vitals_limiter.record_failure(key)

    body = await read_limited_body(request)
    try:
        metricas = _lote.validate_python(json.loads(body))
    except (ValueError, ValidationError) as error:
        raise HTTPException(422, "Métricas inválidas.") from error
    if not 1 <= len(metricas) <= MAX_METRICAS_POR_ENVIO:
        raise HTTPException(422, "Métricas inválidas.")

    # La sesión de la base es síncrona: dentro de un endpoint async se corre en otro hilo para no
    # bloquear al servidor mientras responde la base
    await run_in_threadpool(save_metrics, db, metricas)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def read_limited_body(request: Request) -> bytes:
    """Lee el cuerpo por partes y corta apenas pasa del máximo.

    El Content-Length permite rechazar de inmediato, pero un envío por partes (chunked) no lo
    trae: por eso también se cuenta lo que va llegando, sin juntar más de lo permitido.
    """
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BYTES_POR_ENVIO):
        raise HTTPException(413, "Envío demasiado grande.")
    chunks = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_BYTES_POR_ENVIO:
            raise HTTPException(413, "Envío demasiado grande.")
        chunks.append(chunk)
    return b"".join(chunks)


def save_metrics(db: Session, metricas: list[MetricaWebIn]) -> None:
    db.add_all(MetricaWeb(**m.model_dump()) for m in metricas)
    db.commit()


def percentile_75(values: list[float]) -> float:
    """Percentil 75 por rango más cercano (el mismo criterio que usa CrUX)."""
    ordered = sorted(values)
    return ordered[math.ceil(0.75 * len(ordered)) - 1]


@router.get("/resumen", response_model=list[ResumenMetricaOut])
def summary(
    dias: Annotated[int, Query(ge=1, le=90)] = 7,
    vista: Annotated[str | None, Query(max_length=30)] = None,
    _: Usuario = Depends(current_user),
    db: Session = Depends(get_db),
):
    """p75 y porcentaje de mediciones «good» por métrica en los últimos días."""
    query = select(MetricaWeb.nombre, MetricaWeb.valor, MetricaWeb.calificacion).where(
        MetricaWeb.creada_en >= now_utc() - timedelta(days=dias)
    )
    if vista:
        query = query.where(MetricaWeb.vista == vista)

    values: dict[str, list[float]] = {name: [] for name in METRICAS_WEB}
    good: dict[str, int] = dict.fromkeys(METRICAS_WEB, 0)
    for name, value, rating in db.execute(query):
        values[name].append(value)
        good[name] += rating == "good"

    return [
        ResumenMetricaOut(
            nombre=name,
            muestras=len(values[name]),
            p75=percentile_75(values[name]),
            buenas=good[name] / len(values[name]),
        )
        for name in METRICAS_WEB
        if values[name]
    ]
