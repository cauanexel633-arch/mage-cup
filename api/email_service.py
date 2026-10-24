"""SMTP email sender using Python's standard library only."""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _send_message(message: EmailMessage) -> None:
    host = _required_env("SMTP_HOST")
    user = _required_env("SMTP_USER")
    password = _required_env("SMTP_PASSWORD")

    port = int(os.getenv("SMTP_PORT", "465"))
    security = os.getenv("SMTP_SECURITY", "ssl").strip().lower()
    timeout = int(os.getenv("SMTP_TIMEOUT_SECONDS", "15"))

    tls_context = ssl.create_default_context()

    if security == "starttls":
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            smtp.ehlo()
            smtp.starttls(context=tls_context)
            smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(message)
        return

    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, context=tls_context, timeout=timeout) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
        return

    raise RuntimeError("SMTP_SECURITY must be 'ssl' or 'starttls'")


def send_otp_email(email: str, code: str) -> None:
    from_email = _required_env("FROM_EMAIL")

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = email
    message["Subject"] = "Seu código de verificação — Mage Cup"

    text_body = (
        "MAGE CUP\n\n"
        "Seu código de verificação é:\n\n"
        f"{code}\n\n"
        "Esse código expira em 10 minutos.\n\n"
        "Se você não solicitou esse código, ignore este e-mail."
    )

    html_body = f"""
    <!doctype html>
    <html lang="pt-BR">
      <body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:32px;">
        <div style="max-width:560px;margin:0 auto;background:#ffffff;padding:32px;border-radius:12px;">
          <h1 style="margin-top:0;">MAGE CUP</h1>
          <p>Seu código de verificação é:</p>
          <div style="font-size:36px;font-weight:700;letter-spacing:8px;">{code}</div>
          <p>Esse código expira em 10 minutos.</p>
          <p>Se você não solicitou esse código, ignore este e-mail.</p>
        </div>
      </body>
    </html>
    """

    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    _send_message(message)


def send_custom_message_email(email: str, message_text: str) -> None:
    """Send plain-text feedback to a fixed, server-controlled destination."""
    from_email = _required_env("FROM_EMAIL")

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = email
    message["Subject"] = "Nova mensagem enviada pelo Mage Cup"
    message.set_content(
        "Você recebeu uma mensagem enviada pelo formulário do Mage Cup.\n\n"
        f"{message_text}\n"
    )
    _send_message(message)
