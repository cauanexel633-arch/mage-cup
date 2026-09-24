"""In-memory OTP storage.

Prototype only: Vercel Functions can restart and may serve requests from
multiple instances. Replace this module with Redis/KV/Supabase for production.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class OTPRecord:
    email: str
    hash: str
    createdAt: int
    expiresAt: int
    attempts: int


OTP_STORE: Dict[str, OTPRecord] = {}
SEND_HISTORY: Dict[str, list[int]] = {}


def now() -> int:
    return int(time.time())


def get(email: str) -> Optional[OTPRecord]:
    record = OTP_STORE.get(email)
    if record is None:
        return None

    if now() >= record.expiresAt:
        OTP_STORE.pop(email, None)
        return None

    return record


def set_record(record: OTPRecord) -> None:
    OTP_STORE[record.email] = record


def delete(email: str) -> None:
    OTP_STORE.pop(email, None)


def seconds_since_last_send(email: str) -> Optional[int]:
    history = SEND_HISTORY.get(email, [])
    if not history:
        return None
    return now() - history[-1]


def register_send(email: str) -> None:
    timestamp = now()
    history = SEND_HISTORY.setdefault(email, [])
    history.append(timestamp)

    # Keep only the last hour of sends.
    cutoff = timestamp - 3600
    SEND_HISTORY[email] = [value for value in history if value >= cutoff]


def sends_last_hour(email: str) -> int:
    timestamp = now()
    cutoff = timestamp - 3600
    history = SEND_HISTORY.get(email, [])
    filtered = [value for value in history if value >= cutoff]
    SEND_HISTORY[email] = filtered
    return len(filtered)
