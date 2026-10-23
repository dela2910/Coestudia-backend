"""Normalización de texto para buscar y guardar asignaturas."""

import unicodedata


def normalize_code(code: str) -> str:
    """Sigla en mayúsculas y sin espacios: " iic 2143 " → "IIC2143"."""
    return "".join(code.split()).upper()


def normalize_text(text: str) -> str:
    """Sin tildes, en minúsculas y con espacios simples: " Cálculo  II " → "calculo ii"."""
    without_accents = "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )
    return " ".join(without_accents.lower().split())
