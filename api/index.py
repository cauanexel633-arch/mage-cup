"""Mage Cup Email Bridge - FastAPI/Vercel."""

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
from api.otp_store import OTPRecord, delete, get, now, register_send, seconds_since_last_send, set_record, sends_last_hour

app = FastAPI(title="Mage Cup Email Bridge", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET","POST","OPTIONS"], allow_headers=["*"])

EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

def error(message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "error": message})

def normalize_email(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""

def valid_email(email: str) -> bool:
    return bool(EMAIL_REGEX.fullmatch(email))

def otp_secret() -> bytes:
    value = os.getenv("OTP_SECRET", "").strip()
    if not value:
        raise RuntimeError("OTP_SECRET is not configured")
    return value.encode("utf-8")

def hash_otp(email: str, code: str) -> str:
    return hmac.new(otp_secret(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()

def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"

def ttl_seconds() -> int:
    try:
        value = int(os.getenv("OTP_TTL_SECONDS", "600"))
    except ValueError:
        value = 600
    return max(60, min(value, 3600))

def request_ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", "unknown").split(",")[0].strip()

@app.get("/", include_in_schema=False)
@app.get("/api", include_in_schema=False)
async def root() -> JSONResponse:
    return JSONResponse(content={"success": True, "service": "Mage Cup Email Bridge", "status": "online", "endpoints": ["/api/health", "/api/send-otp", "/api/verify-otp"]})

@app.api_route("/health", methods=["GET","OPTIONS"], include_in_schema=False)
@app.api_route("/api/health", methods=["GET","OPTIONS"], include_in_schema=False)
async def health(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse(status_code=204, content=None)
    return JSONResponse(content={"success": True, "service": "Mage Cup Email Bridge", "status": "online"})

async def _send_otp(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse(status_code=204, content=None)
    try:
        body = await request.json()
    except Exception:
        return error("JSON inválido.", 400)
    if not isinstance(body, dict):
        return error("JSON inválido.", 400)
    email = normalize_email(body.get("email"))
    if not valid_email(email):
        return error("E-mail inválido.", 400)
    last = seconds_since_last_send(email)
    if last is not None and last < 60:
        return error("Aguarde antes de solicitar outro código.", 429)
    if sends_last_hour(email) >= 10:
        return error("Limite temporário de solicitações excedido.", 429)
    ip_key = f"__ip__:{request_ip(request)}"
    ip_last = seconds_since_last_send(ip_key)
    if ip_last is not None and ip_last < 5:
        return error("Muitas solicitações. Aguarde alguns segundos.", 429)
    if sends_last_hour(ip_key) >= 30:
        return error("Muitas solicitações. Tente novamente mais tarde.", 429)
    code = generate_otp()
    created = now()
    try:
        send_otp_email(email, code)
    except Exception as exc:
        print(f"SMTP error: {type(exc).__name__}: {exc}")
        return error("Não foi possível enviar o e-mail.", 502)
    set_record(OTPRecord(email=email, hash=hash_otp(email, code), createdAt=created, expiresAt=created + ttl_seconds(), attempts=0))
    register_send(email)
    register_send(ip_key)
    return JSONResponse(content={"success": True, "message": "Código enviado."})

@app.api_route("/send-otp", methods=["POST","OPTIONS"])
@app.api_route("/api/send-otp", methods=["POST","OPTIONS"])
async def send_otp(request: Request) -> JSONResponse:
    return await _send_otp(request)

async def _verify_otp(request: Request) -> JSONResponse:
    if request.method == "OPTIONS":
        return JSONResponse(status_code=204, content=None)
    try:
        body = await request.json()
    except Exception:
        return error("JSON inválido.", 400)
    if not isinstance(body, dict):
        return error("JSON inválido.", 400)
    email = normalize_email(body.get("email"))
    code = body.get("code")
    if not valid_email(email):
        return error("E-mail inválido.", 400)
    if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code):
        return error("O código deve conter exatamente 6 números.", 400)
    record = get(email)
    if record is None:
        return error("O código expirou. Solicite outro.", 410)
    record.attempts += 1
    if record.attempts > 5:
        delete(email)
        return error("Número máximo de tentativas excedido. Solicite outro código.", 429)
    if not hmac.compare_digest(hash_otp(email, code), record.hash):
        if record.attempts >= 5:
            delete(email)
            return error("Número máximo de tentativas excedido. Solicite outro código.", 429)
        set_record(record)
        return error("Código incorreto.", 401)
    delete(email)
    return JSONResponse(content={"success": True, "message": "Código verificado.", "email": email})

@app.api_route("/verify-otp", methods=["POST","OPTIONS"])
@app.api_route("/api/verify-otp", methods=["POST","OPTIONS"])
async def verify_otp(request: Request) -> JSONResponse:
    return await _verify_otp(request)
