"""Envío de correos con Brevo.

Si BREVO_API_KEY no está definida (desarrollo local), el correo no se envía: se muestra en la
consola del backend, con el enlace listo para copiar en el navegador.
"""

import logging
import os
from html import escape

import httpx

logger = logging.getLogger("coestudia.mailer")

BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def frontend_url() -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")


def send_email(to: str, subject: str, html: str, text: str) -> None:
    api_key = os.getenv("BREVO_API_KEY")
    if not api_key:
        logger.warning(
            "Correo sin enviar (falta BREVO_API_KEY)\nPara: %s\n%s\n%s", to, subject, text
        )
        return

    response = httpx.post(
        BREVO_URL,
        headers={"api-key": api_key, "accept": "application/json"},
        json={
            "sender": {
                "name": "Coestudia",
                "email": os.getenv("MAIL_FROM") or "no-reply@coestudia.cl",
            },
            "to": [{"email": to}],
            "subject": subject,
            "htmlContent": html,
            "textContent": text,
        },
        timeout=10,
    )
    response.raise_for_status()


def send_verification_email(to: str, name: str, token: str) -> None:
    link = f"{frontend_url()}/?verificar={token}"
    first_name = name.split()[0] if name.split() else name
    send_email(
        to,
        "Verifica tu correo en Coestudia",
        html=(
            f"<p>Hola {escape(first_name)}:</p>"
            "<p>Para activar tu cuenta de Coestudia, verifica tu correo con este enlace:</p>"
            f'<p><a href="{escape(link)}">Verificar mi correo</a></p>'
            "<p>El enlace vence en 24 horas. Si no creaste una cuenta, ignora este correo.</p>"
        ),
        text=(
            f"Hola {first_name}:\n\nPara activar tu cuenta de Coestudia abre este enlace:\n"
            f"{link}\n\nEl enlace vence en 24 horas."
        ),
    )
