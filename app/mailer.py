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


def first_name(name: str) -> str:
    return name.split()[0] if name.split() else name


def send_verification_email(to: str, name: str, token: str) -> None:
    link = f"{frontend_url()}/?verificar={token}"
    send_email(
        to,
        "Verifica tu correo en Coestudia",
        html=(
            f"<p>Hola {escape(first_name(name))}:</p>"
            "<p>Para activar tu cuenta de Coestudia, verifica tu correo con este enlace:</p>"
            f'<p><a href="{escape(link)}">Verificar mi correo</a></p>'
            "<p>El enlace vence en 24 horas. Si no creaste una cuenta, ignora este correo.</p>"
        ),
        text=(
            f"Hola {first_name(name)}:\n\nPara activar tu cuenta de Coestudia abre este enlace:\n"
            f"{link}\n\nEl enlace vence en 24 horas."
        ),
    )


def send_password_reset_email(to: str, name: str, token: str) -> None:
    link = f"{frontend_url()}/?restablecer={token}"
    send_email(
        to,
        "Recupera tu contraseña de Coestudia",
        html=(
            f"<p>Hola {escape(first_name(name))}:</p>"
            "<p>Pediste cambiar tu contraseña de Coestudia. Elige una nueva con este enlace:</p>"
            f'<p><a href="{escape(link)}">Cambiar mi contraseña</a></p>'
            "<p>El enlace vence en 1 hora. Si no lo pediste, ignora este correo: tu contraseña "
            "sigue siendo la misma.</p>"
        ),
        text=(
            f"Hola {first_name(name)}:\n\nPara elegir una contraseña nueva abre este enlace:\n"
            f"{link}\n\nEl enlace vence en 1 hora. Si no lo pediste, ignora este correo."
        ),
    )


def send_request_answered_email(to: str, name: str, subject: str, accepted: bool) -> None:
    """Avisa a quien pidió unirse que el creador aceptó o rechazó su solicitud. `subject` es la
    asignatura del grupo, ej. «MAT1620 Cálculo II»."""
    link = frontend_url()
    if accepted:
        title = f"Te aceptaron en el grupo de {subject}"
        body = (
            "Ya eres integrante del grupo. En «Mis grupos» encuentras el lugar o el enlace de las "
            "sesiones, los archivos y el foro del grupo."
        )
        action = "Ir a mis grupos"
    else:
        title = f"Tu solicitud al grupo de {subject} no fue aceptada"
        body = (
            "El creador del grupo no aceptó tu solicitud esta vez. Puedes buscar otros grupos de "
            "la misma asignatura o crear uno."
        )
        action = "Buscar grupos"
    send_email(
        to,
        title,
        html=(
            f"<p>Hola {escape(first_name(name))}:</p>"
            f"<p><strong>{escape(title)}.</strong> {escape(body)}</p>"
            f'<p><a href="{escape(link)}">{action}</a></p>'
        ),
        text=f"Hola {first_name(name)}:\n\n{title}. {body}\n\n{action}: {link}",
    )
