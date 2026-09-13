from datetime import datetime, timezone
from typing import Any
import httpx
from app.database.mongo import settings_col

DEFAULT_HANDLER = "https://paytm-v2.codescan.workers.dev/"

async def get_payment_settings() -> dict[str, Any]:
    doc = await settings_col.find_one({"_id": "payment"})
    defaults = {
        "_id": "payment", "paytm_enabled": True, "stars_enabled": True, "usdt_enabled": False,
        "handler_url": DEFAULT_HANDLER, "merchant_id": "", "upi_id": "", "env": "prod",
        "usdt_address": "", "usdt_network": "TRC20", "usdt_inr_rate": 90.0,
        "star_inr_rate": 1.0,
    }
    if doc:
        defaults.update(doc)
    return defaults

async def save_payment_settings(data: dict[str, Any]) -> None:
    payload = dict(data)
    payload["_id"] = "payment"
    payload["updated_at"] = datetime.now(timezone.utc)
    await settings_col.replace_one({"_id": "payment"}, payload, upsert=True)

async def generate_paytm_qr(*, amount: float, order_id: str) -> dict[str, Any]:
    cfg = await get_payment_settings()
    if not cfg.get("paytm_enabled", True):
        raise ValueError("Paytm payment is disabled.")
    handler = str(cfg.get("handler_url") or DEFAULT_HANDLER).rstrip("/")
    upi_id = str(cfg.get("upi_id") or "").strip()
    if not upi_id:
        raise ValueError("Paytm UPI ID is not configured.")
    url = f"{handler}/api/generate-qr"
    payload = {"upi_id": upi_id, "amount": f"{amount:.2f}", "order_id": order_id}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
    if data.get("status") != "success" or not data.get("qr_url"):
        raise ValueError(str(data.get("message") or "QR generation failed."))
    return data

async def verify_paytm_payment(*, order_id: str) -> dict[str, Any]:
    cfg = await get_payment_settings()
    handler = str(cfg.get("handler_url") or DEFAULT_HANDLER).rstrip("/")
    merchant_id = str(cfg.get("merchant_id") or "").strip()
    if not merchant_id:
        raise ValueError("Paytm Merchant ID is not configured.")
    payload = {"mid": merchant_id, "order_id": order_id, "env": str(cfg.get("env") or "prod")}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.post(f"{handler}/api/verify", json=payload)
        response.raise_for_status()
        return response.json()


def usdt_amount_from_inr(inr: float, rate: float) -> float:
    if rate <= 0:
        raise ValueError("Invalid USDT rate")
    return round(float(inr) / float(rate), 6)


def stars_amount_from_inr(inr: float, rate: float) -> int:
    if rate <= 0:
        raise ValueError("Invalid Stars rate")
    return max(1, int(round(float(inr) / float(rate))))
