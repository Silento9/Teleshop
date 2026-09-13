from fastapi import APIRouter, Header, HTTPException
from datetime import datetime, timezone
import hashlib, time
from app.database.mongo import api_keys, products, orders, users
from app.services.store import stock_count, atomic_take_inventory, release_inventory, spend_balance, create_wallet_tx
from bson import ObjectId

router = APIRouter(prefix="/api/v1")

# Simple in-process sliding-window rate limit per API key.
_RATE_LIMIT = 120  # requests per minute per key
_rate_bucket: dict[str, list[float]] = {}

async def auth(x_reseller_key: str | None):
    if not x_reseller_key:
        raise HTTPException(401, "Missing X-Reseller-Key")
    h = hashlib.sha256(x_reseller_key.encode()).hexdigest()
    key = await api_keys.find_one({"key_hash": h, "active": True})
    if not key:
        raise HTTPException(401, "Invalid API key")
    now_ts = time.time()
    bucket = [t for t in _rate_bucket.get(h, []) if now_ts - t < 60]
    if len(bucket) >= _RATE_LIMIT:
        raise HTTPException(429, "Rate limit exceeded. Try again later.")
    bucket.append(now_ts); _rate_bucket[h] = bucket
    await api_keys.update_one({"_id": key["_id"]}, {"$set":{"last_used_at":datetime.now(timezone.utc)}})
    return key["user_id"]

@router.get("/products")
async def products_list(x_reseller_key: str | None = Header(default=None)):
    await auth(x_reseller_key)
    docs = await products.find({"enabled":True}).sort("position",1).to_list(500)
    return [{"id":str(p["_id"]), "name":p["name"], "price":p["price"], "stock":await stock_count(p["_id"])} for p in docs]

@router.get("/products/{product_id}")
async def product_detail(product_id: str, x_reseller_key: str | None = Header(default=None)):
    await auth(x_reseller_key)
    try: p = await products.find_one({"_id":ObjectId(product_id),"enabled":True})
    except: p = None
    if not p: raise HTTPException(404,"Product not found")
    return {"id":str(p["_id"]),"name":p["name"],"price":p["price"],"stock":await stock_count(p["_id"]),"description":p.get("description","")}

@router.post("/orders")
async def create_order(product_id: str, x_reseller_key: str | None = Header(default=None)):
    uid = await auth(x_reseller_key)
    try:
        p = await products.find_one({"_id": ObjectId(product_id), "enabled": True})
    except Exception:
        p = None
    if not p:
        raise HTTPException(404, "Product not found")

    price = float(p.get("price", 0))
    if price <= 0:
        raise HTTPException(400, "Product has an invalid price")

    user = await users.find_one({"telegram_id": uid})
    if not user or float(user.get("balance", 0)) < price:
        raise HTTPException(402, "Insufficient wallet balance")

    oid = "API-" + __import__("uuid").uuid4().hex[:10].upper()

    # Reserve stock first. If the wallet charge fails, release the reservation.
    item = await atomic_take_inventory(p["_id"], oid)
    if not item:
        raise HTTPException(409, "Out of stock")

    if not await spend_balance(uid, price, oid):
        await release_inventory(oid)
        raise HTTPException(402, "Insufficient wallet balance")

    doc = {
        "order_id": oid, "user_id": uid, "product_id": p["_id"],
        "product_name": p["name"], "amount": price, "currency": "INR",
        "status": "delivered", "delivery": item["value"],
        "payment_provider": "api_wallet", "created_at": datetime.now(timezone.utc),
        "verified_at": datetime.now(timezone.utc),
    }
    try:
        await orders.insert_one(doc)
    except Exception:
        # Do not silently lose the customer's charge if order persistence fails.
        await users.update_one({"telegram_id": uid}, {"$inc": {"balance": price}})
        await create_wallet_tx(uid, price, "refund", "API order creation failed", oid)
        await release_inventory(oid)
        raise HTTPException(500, "Order could not be recorded; wallet charge was refunded")

    await users.update_one({"telegram_id": uid}, {"$inc": {"total_orders": 1}})
    await products.update_one({"_id": p["_id"]}, {"$inc": {"sold": 1}})
    return {"success": True, "order_id": oid, "product": p["name"], "delivery": item["value"]}

@router.get("/orders/{order_id}")
async def order_status(order_id: str, x_reseller_key: str | None = Header(default=None)):
    uid = await auth(x_reseller_key)
    d = await orders.find_one({"order_id":order_id,"user_id":uid})
    if not d: raise HTTPException(404,"Order not found")
    return {"order_id":d["order_id"],"status":d["status"],"product":d["product_name"],"amount":d["amount"],"delivery":d.get("delivery")}
