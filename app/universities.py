# La universidad no la elige el usuario: se deduce del dominio de su correo.
# Por ahora solo está habilitada la UC; sumar otra es agregar una línea aquí (y en el frontend,
# src/data/universities.js).
UNIVERSITIES_BY_DOMAIN = {
    "uc.cl": "Pontificia Universidad Católica de Chile",
}


def university_from_email(email: str) -> str | None:
    domain = email.strip().lower().rpartition("@")[2]
    return UNIVERSITIES_BY_DOMAIN.get(domain)


def allowed_domains_text() -> str:
    # Ej. "@uc.cl" o "@uc.cl, @uchile.cl"
    return ", ".join(f"@{domain}" for domain in UNIVERSITIES_BY_DOMAIN)
