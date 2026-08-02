from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import aiohttp

from config import BookmakerApiConfig


BOOKMAKER_API_CLIENT_VERSION = "2026-07-29-user-lookup-variants"


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


def _user_sign_variant(cfg: BookmakerApiConfig, user_id: str, *, camel_case: bool) -> str:
    if not camel_case:
        return _user_sign(cfg, user_id)
    part_a = _sha256(
        f"hash={cfg.api_hash}&userId={user_id}&cashdeskId={cfg.cashdesk_id}"
    )
    part_b = _md5(
        f"userId={user_id}&cashierpass={cfg.cashier_password}&hash={cfg.api_hash}"
    )
    return _sha256(part_a + part_b)


def _short_json(data: Any, *, limit: int = 700) -> str:
    try:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = str(data)
    return text[:limit]


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
    attempts = (
        (
            "docs",
            {"confirm": _confirm(user_id, cfg), "cashdeskId": cfg.cashdesk_id},
            {"sign": _user_sign_variant(cfg, user_id, camel_case=False)},
        ),
        (
            "camel-sign",
            {"confirm": _confirm(user_id, cfg), "cashdeskId": cfg.cashdesk_id},
            {"sign": _user_sign_variant(cfg, user_id, camel_case=True)},
        ),
        (
            "lower-cashdesk-param",
            {"confirm": _confirm(user_id, cfg), "cashdeskid": cfg.cashdesk_id},
            {"sign": _user_sign_variant(cfg, user_id, camel_case=False)},
        ),
    )
    errors: list[str] = []

    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
        ) as session:
            for attempt_name, params, headers in attempts:
                try:
                    async with session.get(url, params=params, headers=headers) as response:
                        data = await _read_json(response)
                except BookmakerApiError as exc:
                    errors.append(f"{attempt_name}: {exc}")
                    continue

                try:
                    return _parse_bookmaker_user_response(cfg, data)
                except BookmakerApiError as exc:
                    errors.append(f"{attempt_name}: {exc}; response={_short_json(data)}")
    except aiohttp.ClientError as exc:
        raise BookmakerApiError(f"{cfg.platform}: connection error") from exc

    raise BookmakerApiError(" | ".join(errors) or "User not found")


def _parse_bookmaker_user_response(
    cfg: BookmakerApiConfig, data: Any
) -> BookmakerUser:
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
    if cfg.uses_public_api:
        if not user_id.isdigit():
            raise BookmakerApiError("1WIN: user ID must contain digits only")
        amount = float(Decimal(amount_minor) / Decimal(100))
        url = "https://api.1win.win/v1/client/deposit"
        headers = {"X-API-KEY": cfg.api_key}
        body = {"userId": int(user_id), "amount": amount}
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout_seconds)
            ) as session:
                async with session.post(url, json=body, headers=headers) as response:
                    data = await _read_json(response)
        except aiohttp.ClientError as exc:
            raise BookmakerApiError("1WIN: connection error") from exc
        if not isinstance(data, dict):
            raise BookmakerApiError("1WIN: unexpected API response")
        return data

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
