"""Mage Cup Email Bridge - Python/FastAPI Vercel Function."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.email_service import send_otp_email
from api.otp_store import (
    OTPRecord,
    delete,
    get,
    now,
    register_send,
    seconds_since_last_send,
    set_record,
    sends_last_hour,
)

app = FastAPI(
    title="Mage Cup Email Bridge",
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def json_error(message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"success": False, "error": message},
    )


def normalize_email(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.fullmatch(email))


def otp_secret() -> bytes:
    secret = os.getenv("OTP_SECRET", "").strip()
    if not secret:
        raise RuntimeError("OTP_SECRET is not configured")
    return secret.encode("utf-8")


def hash_otp(email: str, code: str) -> str:
    payload = f"{email}:{code}".encode("utf-8")
    return hmac.new(otp_secret(), payload, hashlib.sha256).hexdigest()


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def ttl_seconds() -> int:
    value = int(os.getenv("OTP_TTL_SECONDS", "600"))
    return max(60, min(value, 3600))


def safe_request_ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", "unknown").split(",")[0].strip()


@app.get("/", include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "service": "Mage Cup Email Bridge",
            "status": "online",
            "api": "/api",
            "health": "/api/health",
        },
    )


@app.api_route("/health", methods=["GET", "OPTIONS"])
async def health() -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "service": "Mage Cup Email Bridge",
            "status": "online",
        },
    )


@app.api_route("/send-otp", methods=["POST", "OPTIONS"])
async def send_otp(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse(status_code=204, content=None)

    try:
        body = await request.json()
    except Exception:
        return json_error("JSON inválido.", 400)

    if not isinstance(body, dict):
        return json_error("JSON inválido.", 400)

    email = normalize_email(body.get("email"))

    if not email or not valid_email(email):
        return json_error("E-mail inválido.", 400)

    cooldown_seconds = 60
    last_send = seconds_since_last_send(email)
    if last_send is not None and last_send < cooldown_seconds:
        return json_error("Aguarde antes de solicitar outro código.", 429)

    if sends_last_hour(email) >= 10:
        return json_error("Limite temporário de solicitações excedido.", 429)

    ip = safe_request_ip(request)
    ip_key = f"__ip__:{ip}"
    ip_last_send = seconds_since_last_send(ip_key)
    if ip_last_send is not None and ip_last_send < 5:
        return json_error("Muitas solicitações. Aguarde alguns segundos.", 429)
    if sends_last_hour(ip_key) >= 30:
        return json_error("Muitas solicitações. Tente novamente mais tarde.", 429)

    code = generate_otp()
    created_at = now()
    expires_at = created_at + ttl_seconds()

    try:
        send_otp_email(email, code)
    except Exception as exc:
        print(f"Email send failed: {type(exc).__name__}: {exc}")
        return json_error("Não foi possível enviar o e-mail.", 502)

    set_record(
        OTPRecord(
            email=email,
            hash=hash_otp(email, code),
            createdAt=created_at,
            expiresAt=expires_at,
            attempts=0,
        )
    )
    register_send(email)
    register_send(ip_key)

    return JSONResponse(
        status_code=200,
        content={"success": True, "message": "Código enviado."},
    )


@app.api_route("/verify-otp", methods=["POST", "OPTIONS"])
async def verify_otp(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse(status_code=204, content=None)

    try:
        body = await request.json()
    except Exception:
        return json_error("JSON inválido.", 400)

    if not isinstance(body, dict):
        return json_error("JSON inválido.", 400)

    email = normalize_email(body.get("email"))
    code = body.get("code")

    if not email or not valid_email(email):
        return json_error("E-mail inválido.", 400)

    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        return json_error("O código deve conter exatamente 6 números.", 400)

    record = get(email)

    if record is None:
        return json_error("O código expirou. Solicite outro.", 410)

    if record.attempts >= 5:
        delete(email)
        return json_error(
            "Número máximo de tentativas excedido. Solicite outro código.",
            429,
        )

    record.attempts += 1
    candidate = hash_otp(email, code)

    if not hmac.compare_digest(candidate, record.hash):
        if record.attempts >= 5:
            delete(email)
            return json_error(
                "Número máximo de tentativas excedido. Solicite outro código.",
                429,
            )
        set_record(record)
        return json_error("Código incorreto.", 401)

    delete(email)

    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "message": "Código verificado.",
            "email": email,
        },
    )
