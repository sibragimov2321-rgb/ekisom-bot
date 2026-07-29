from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import aiohttp

from config import BookmakerApiConfig


class BookmakerApiError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BookmakerUser:
    user_id: str
    name: str
    currency_id: str
    raw: dict[str, Any]


def _get_any(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    lowered = {str(key).lower(): value for key, value in data.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value is not None:
            return value
    return None


def _payload_dict(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("data", "Data", "result", "Result", "user", "User"):
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return data


def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _confirm(value: str, cfg: BookmakerApiConfig) -> str:
    return _md5(f"{value}:{cfg.api_hash}")


def _amount_from_minor(amount_minor: int) -> str:
    value = (Decimal(amount_minor) / Decimal(100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _user_sign(cfg: BookmakerApiConfig, user_id: str) -> str:
    part_a = _sha256(
        f"hash={cfg.api_hash}&userid={user_id}&cashdeskid={cfg.cashdesk_id}"
    )
    part_b = _md5(
        f"userid={user_id}&cashierpass={cfg.cashier_password}&hash={cfg.api_hash}"
    )
    return _sha256(part_a + part_b)


def _deposit_sign(
    cfg: BookmakerApiConfig, user_id: str, amount: str, language: str
) -> str:
    part_a = _sha256(f"hash={cfg.api_hash}&lng={language}&userId={user_id}")
    part_b = _md5(
        f"summa={amount}&cashierpass={cfg.cashier_password}&cashdeskid={cfg.cashdesk_id}"
    )
    return _sha256(part_a + part_b)


async def find_bookmaker_user(
    cfg: BookmakerApiConfig,
    user_id: str,
    *,
    timeout_seconds: int = 15,
) -> BookmakerUser:
    if not cfg.is_configured:
        raise BookmakerApiError(f"{cfg.platform}: API is not configured")

    url = f"{cfg.base_url}/Users/{user_id}"
    params = {"confirm": _confirm(user_id, cfg), "cashdeskId": cfg.cashdesk_id}
    headers = {"sign": _user_sign(cfg, user_id)}

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
        ) as session:
            async with session.get(url, params=params, headers=headers) as response:
                data = await _read_json(response)
    except aiohttp.ClientError as exc:
        raise BookmakerApiError(f"{cfg.platform}: connection error") from exc

    if not isinstance(data, dict):
        raise BookmakerApiError(f"{cfg.platform}: unexpected API response")
    success = _get_any(data, "success", "Success")
    if success is False:
        raise BookmakerApiError(str(_get_any(data, "message", "Message", "error", "Error") or "User not found"))

    payload = _payload_dict(data)
    if not isinstance(payload, dict):
        raise BookmakerApiError(f"{cfg.platform}: empty user response")

    returned_user_id = str(_get_any(payload, "userId", "UserId", "userid", "id", "Id") or "").strip()
    currency_id = str(_get_any(payload, "currencyId", "CurrencyId", "currency_id", "currency") or "")
    name = str(_get_any(payload, "name", "Name", "login", "Login", "fullName", "FullName") or "")
    if not returned_user_id:
        raise BookmakerApiError(f"User not found; response fields: {', '.join(map(str, payload.keys()))}")
    return BookmakerUser(
        user_id=returned_user_id,
        name=name,
        currency_id=currency_id,
        raw=payload,
    )


async def add_bookmaker_deposit(
    cfg: BookmakerApiConfig,
    user_id: str,
    amount_minor: int,
    *,
    language: str = "ru",
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    if not cfg.is_configured:
        raise BookmakerApiError(f"{cfg.platform}: API is not configured")

    language = language if language in {"ru", "en"} else "ru"
    amount = _amount_from_minor(amount_minor)
    url = f"{cfg.base_url}/Deposit/{user_id}/Add"
    body = {
        "cashdeskId": cfg.cashdesk_id,
        "lng": language,
        "summa": amount,
        "confirm": _confirm(user_id, cfg),
    }
    headers = {"sign": _deposit_sign(cfg, user_id, amount, language)}

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
        ) as session:
            async with session.post(url, json=body, headers=headers) as response:
                data = await _read_json(response)
    except aiohttp.ClientError as exc:
        raise BookmakerApiError(f"{cfg.platform}: connection error") from exc

    if not isinstance(data, dict):
        raise BookmakerApiError(f"{cfg.platform}: unexpected API response")
    if data.get("success") is False:
        raise BookmakerApiError(str(data.get("message") or "Deposit rejected"))
    return data


async def get_bookmaker_balance(
    cfg: BookmakerApiConfig,
    *,
    timeout_seconds: int = 15,
) -> dict[str, Any]:
    if not cfg.is_configured:
        raise BookmakerApiError(f"{cfg.platform}: API is not configured")

    dt = datetime.now(timezone.utc).strftime("%Y.%m.%d%H:%M:%S")
    part_a = _sha256(
        f"hash={cfg.api_hash}&cashierpass={cfg.cashier_password}&dt={dt}"
    )
    part_b = _md5(
        f"dt={dt}&cashierpass={cfg.cashier_password}&cashdeskid={cfg.cashdesk_id}"
    )
    sign = _sha256(part_a + part_b)
    url = f"{cfg.base_url}/Cashdesk/{cfg.cashdesk_id}/Balance"
    params = {"confirm": _confirm(cfg.cashdesk_id, cfg), "dt": dt}

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
        ) as session:
            async with session.get(url, params=params, headers={"sign": sign}) as response:
                data = await _read_json(response)
    except aiohttp.ClientError as exc:
        raise BookmakerApiError(f"{cfg.platform}: connection error") from exc

    if not isinstance(data, dict):
        raise BookmakerApiError(f"{cfg.platform}: unexpected API response")
    if data.get("success") is False:
        raise BookmakerApiError(str(data.get("message") or "Balance request rejected"))
    return data


async def _read_json(response: aiohttp.ClientResponse) -> Any:
    text = await response.text()
    if response.status >= 400:
        raise BookmakerApiError(f"HTTP {response.status}: {text[:300]}")
    try:
        return await response.json(content_type=None)
    except Exception as exc:
        raise BookmakerApiError(f"Invalid JSON: {text[:300]}") from exc
