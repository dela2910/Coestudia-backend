"""Cliente de la API de Anthropic (Claude) para el asistente de estudio (B13).

La clave va en la variable de entorno ANTHROPIC_API_KEY, solo en el backend. Sin ella el asistente
responde que no está disponible y el resto de la API funciona igual.
"""

import os
from functools import lru_cache

import anthropic

# Haiku 5.5 es el modelo más económico; se puede cambiar con ANTHROPIC_MODEL sin tocar el código
DEFAULT_MODEL = "claude-haiku-5-5"


def model_name() -> str:
    return os.getenv("ANTHROPIC_MODEL") or DEFAULT_MODEL


@lru_cache
def get_claude() -> anthropic.Anthropic | None:
    """Dependencia de FastAPI: el cliente de Claude, o None si falta ANTHROPIC_API_KEY."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    # Render corta las peticiones largas: se limita la espera y se reintenta una vez
    return anthropic.Anthropic(timeout=90.0, max_retries=1)
