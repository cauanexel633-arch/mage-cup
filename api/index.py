"""Mage Cup Email Bridge - FastAPI/Vercel."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
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
    allow_headers=["*"],
)

EMAIL_REGEX = re.compile(
    r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"
)


def error(message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "error": message,
        },
    )


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
    return hmac.new(
        otp_secret(),
        f"{email}:{code}".encode(),
        hashlib.sha256,
    ).hexdigest()


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def ttl_seconds() -> int:
    try:
        value = int(
            os.getenv(
                "OTP_TTL_SECONDS",
                "600",
            )
        )
    except ValueError:
        value = 600

    return max(
        60,
        min(value, 3600),
    )


def request_ip(request: Request) -> str:
    return (
        request.headers
        .get("x-forwarded-for", "unknown")
        .split(",")[0]
        .strip()
    )


# =========================================================
# STATELESS VERIFICATION TOKEN
# =========================================================
#
# O armazenamento em memória pode falhar em Vercel porque o
# envio e a verificação podem cair em instâncias diferentes.
#
# O token abaixo carrega somente:
# - e-mail
# - expiração
# - nonce
# - hash do código
# - número de tentativas
# - assinatura HMAC
#
# O código em si NÃO é colocado no token.
#
# =========================================================


def _sign_token_payload(
    email: str,
    expires_at: int,
    nonce: str,
    code_hash: str,
    attempts: int,
) -> str:
    message = (
        f"{email}|"
        f"{expires_at}|"
        f"{nonce}|"
        f"{code_hash}|"
        f"{attempts}"
    )

    return hmac.new(
        otp_secret(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()


def create_verification_token(
    email: str,
    code: str,
    expires_at: int,
    attempts: int = 0,
    nonce: str | None = None,
) -> str:

    if nonce is None:
        nonce = secrets.token_urlsafe(16)

    code_hash = hash_otp(
        email,
        code,
    )

    signature = _sign_token_payload(
        email,
        expires_at,
        nonce,
        code_hash,
        attempts,
    )

    payload = {
        "email": email,
        "expires_at": expires_at,
        "nonce": nonce,
        "code_hash": code_hash,
        "attempts": attempts,
        "signature": signature,
    }

    raw = json.dumps(
        payload,
        separators=(",", ":"),
    ).encode()

    return (
        base64.urlsafe_b64encode(raw)
        .decode()
        .rstrip("=")
    )


def read_verification_token(
    token: str,
) -> dict[str, Any] | None:

    if not token:
        return None

    try:
        padding = "=" * (
            (-len(token)) % 4
        )

        raw = base64.urlsafe_b64decode(
            token + padding
        )

        data = json.loads(
            raw.decode()
        )

    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    required = (
        "email",
        "expires_at",
        "nonce",
        "code_hash",
        "attempts",
        "signature",
    )

    if any(
        key not in data
        for key in required
    ):
        return None

    try:
        email = str(data["email"])
        expires_at = int(data["expires_at"])
        nonce = str(data["nonce"])
        code_hash = str(data["code_hash"])
        attempts = int(data["attempts"])
        signature = str(data["signature"])

    except (TypeError, ValueError):
        return None

    expected_signature = _sign_token_payload(
        email,
        expires_at,
        nonce,
        code_hash,
        attempts,
    )

    if not hmac.compare_digest(
        expected_signature,
        signature,
    ):
        return None

    return {
        "email": email,
        "expires_at": expires_at,
        "nonce": nonce,
        "code_hash": code_hash,
        "attempts": attempts,
        "signature": signature,
    }


# =========================================================
# ROOT
# =========================================================


@app.get(
    "/",
    include_in_schema=False,
)
@app.get(
    "/api",
    include_in_schema=False,
)
async def root() -> JSONResponse:

    return JSONResponse(
        content={
            "success": True,
            "service": "Mage Cup Email Bridge",
            "status": "online",
            "endpoints": [
                "/api/health",
                "/api/send-otp",
                "/api/verify-otp",
            ],
        }
    )


# =========================================================
# HEALTH
# =========================================================


@app.api_route(
    "/health",
    methods=["GET", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api/health",
    methods=["GET", "OPTIONS"],
    include_in_schema=False,
)
async def health(
    request: Request,
) -> JSONResponse:

    if request.method == "OPTIONS":

        return JSONResponse(
            status_code=204,
            content=None,
        )

    return JSONResponse(
        content={
            "success": True,
            "service": "Mage Cup Email Bridge",
            "status": "online",
        }
    )


# =========================================================
# SEND OTP
# =========================================================


async def _send_otp(
    request: Request,
) -> JSONResponse:

    if request.method == "OPTIONS":

        return JSONResponse(
            status_code=204,
            content=None,
        )

    try:
        body = await request.json()

    except Exception:

        return error(
            "JSON inválido.",
            400,
        )

    if not isinstance(
        body,
        dict,
    ):

        return error(
            "JSON inválido.",
            400,
        )

    email = normalize_email(
        body.get("email")
    )

    if not valid_email(email):

        return error(
            "E-mail inválido.",
            400,
        )

    # -----------------------------------------------------
    # RATE LIMIT
    # -----------------------------------------------------

    last = seconds_since_last_send(
        email
    )

    if last is not None and last < 60:

        return error(
            "Aguarde antes de solicitar outro código.",
            429,
        )

    if sends_last_hour(email) >= 10:

        return error(
            "Limite temporário de solicitações excedido.",
            429,
        )

    ip_key = f"__ip__:{request_ip(request)}"

    ip_last = seconds_since_last_send(
        ip_key
    )

    if ip_last is not None and ip_last < 5:

        return error(
            "Muitas solicitações. Aguarde alguns segundos.",
            429,
        )

    if sends_last_hour(ip_key) >= 30:

        return error(
            "Muitas solicitações. Tente novamente mais tarde.",
            429,
        )

    # -----------------------------------------------------
    # GERAR
    # -----------------------------------------------------

    code = generate_otp()

    created = now()

    expires_at = (
        created
        + ttl_seconds()
    )

    # -----------------------------------------------------
    # ENVIAR EMAIL
    # -----------------------------------------------------

    try:

        send_otp_email(
            email,
            code,
        )

    except Exception as exc:

        print(
            f"SMTP error: "
            f"{type(exc).__name__}: {exc}"
        )

        return error(
            "Não foi possível enviar o e-mail.",
            502,
        )

    # -----------------------------------------------------
    # TOKEN QUE FUNCIONA ENTRE INSTÂNCIAS VERCEL
    # -----------------------------------------------------

    verification_token = create_verification_token(
        email=email,
        code=code,
        expires_at=expires_at,
    )

    # -----------------------------------------------------
    # KEEP LEGACY MEMORY STORE TOO
    # -----------------------------------------------------

    set_record(
        OTPRecord(
            email=email,
            hash=hash_otp(
                email,
                code,
            ),
            createdAt=created,
            expiresAt=expires_at,
            attempts=0,
        )
    )

    register_send(email)
    register_send(ip_key)

    print(
        f"OTP enviado para {email}. "
        f"Expira em {expires_at}."
    )

    return JSONResponse(
        content={
            "success": True,
            "message": "Código enviado.",
            "verification_token": verification_token,
        }
    )


@app.api_route(
    "/send-otp",
    methods=["POST", "OPTIONS"],
)
@app.api_route(
    "/api/send-otp",
    methods=["POST", "OPTIONS"],
)
async def send_otp(
    request: Request,
) -> JSONResponse:

    return await _send_otp(
        request
    )


# =========================================================
# VERIFY OTP
# =========================================================


async def _verify_otp(
    request: Request,
) -> JSONResponse:

    if request.method == "OPTIONS":

        return JSONResponse(
            status_code=204,
            content=None,
        )

    try:
        body = await request.json()

    except Exception:

        return error(
            "JSON inválido.",
            400,
        )

    if not isinstance(
        body,
        dict,
    ):

        return error(
            "JSON inválido.",
            400,
        )

    email = normalize_email(
        body.get("email")
    )

    code = body.get("code")

    verification_token = body.get(
        "verification_token"
    )

    if not valid_email(email):

        return error(
            "E-mail inválido.",
            400,
        )

    if (
        not isinstance(code, str)
        or not re.fullmatch(
            r"\d{6}",
            code,
        )
    ):

        return error(
            "O código deve conter exatamente 6 números.",
            400,
        )

    # =====================================================
    # NOVO FLUXO STATELESS
    # =====================================================

    if isinstance(
        verification_token,
        str,
    ) and verification_token:

        token_data = read_verification_token(
            verification_token
        )

        if token_data is None:

            return error(
                "Token de verificação inválido. Solicite outro código.",
                401,
            )

        token_email = str(
            token_data["email"]
        )

        expires_at = int(
            token_data["expires_at"]
        )

        nonce = str(
            token_data["nonce"]
        )

        code_hash = str(
            token_data["code_hash"]
        )

        attempts = int(
            token_data["attempts"]
        )

        # -------------------------------------------------
        # EMAIL
        # -------------------------------------------------

        if not hmac.compare_digest(
            token_email,
            email,
        ):

            return error(
                "E-mail diferente do código solicitado.",
                401,
            )

        # -------------------------------------------------
        # EXPIRAÇÃO
        # -------------------------------------------------

        if now() >= expires_at:

            return error(
                "O código expirou. Solicite outro.",
                410,
            )

        # -------------------------------------------------
        # TENTATIVAS
        # -------------------------------------------------

        if attempts >= 5:

            return error(
                "Número máximo de tentativas excedido. Solicite outro código.",
                429,
            )

        # -------------------------------------------------
        # COMPARAR
        # -------------------------------------------------

        submitted_hash = hash_otp(
            email,
            code,
        )

        if not hmac.compare_digest(
            submitted_hash,
            code_hash,
        ):

            new_attempts = attempts + 1

            if new_attempts >= 5:

                return error(
                    "Número máximo de tentativas excedido. Solicite outro código.",
                    429,
                )

            next_token = create_verification_token_from_hash(
                email=email,
                expires_at=expires_at,
                nonce=nonce,
                code_hash=code_hash,
                attempts=new_attempts,
            )

            return JSONResponse(
                status_code=401,
                content={
                    "success": False,
                    "error": "Código incorreto.",
                    "verification_token": next_token,
                },
            )

        # -------------------------------------------------
        # SUCESSO
        # -------------------------------------------------

        print(
            f"OTP verificado para {email}."
        )

        return JSONResponse(
            content={
                "success": True,
                "message": "Código verificado.",
                "email": email,
            }
        )

    # =====================================================
    # FLUXO ANTIGO
    # =====================================================
    #
    # Mantido para não quebrar clientes antigos.
    #
    # =====================================================

    record = get(email)

    if record is None:

        return error(
            "O código expirou. Solicite outro.",
            410,
        )

    record.attempts += 1

    if record.attempts > 5:

        delete(email)

        return error(
            "Número máximo de tentativas excedido. Solicite outro código.",
            429,
        )

    if not hmac.compare_digest(
        hash_otp(
            email,
            code,
        ),
        record.hash,
    ):

        if record.attempts >= 5:

            delete(email)

            return error(
                "Número máximo de tentativas excedido. Solicite outro código.",
                429,
            )

        set_record(
            record
        )

        return error(
            "Código incorreto.",
            401,
        )

    delete(email)

    return JSONResponse(
        content={
            "success": True,
            "message": "Código verificado.",
            "email": email,
        }
    )


def create_verification_token_from_hash(
    email: str,
    expires_at: int,
    nonce: str,
    code_hash: str,
    attempts: int,
) -> str:

    signature = _sign_token_payload(
        email,
        expires_at,
        nonce,
        code_hash,
        attempts,
    )

    payload = {
        "email": email,
        "expires_at": expires_at,
        "nonce": nonce,
        "code_hash": code_hash,
        "attempts": attempts,
        "signature": signature,
    }

    raw = json.dumps(
        payload,
        separators=(",", ":"),
    ).encode()

    return (
        base64.urlsafe_b64encode(raw)
        .decode()
        .rstrip("=")
    )


@app.api_route(
    "/verify-otp",
    methods=["POST", "OPTIONS"],
)
@app.api_route(
    "/api/verify-otp",
    methods=["POST", "OPTIONS"],
)
async def verify_otp(
    request: Request,
) -> JSONResponse:

    return await _verify_otp(
        request
    )
