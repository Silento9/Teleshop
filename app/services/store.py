from datetime import datetime, timezone
import hashlib, secrets
from bson import ObjectId
from pymongo import ReturnDocument
from app.database.mongo import users, products, inventory, wallet_transactions, referrals, cashback, api_keys, settings_col

def now(): return datetime.now(timezone.utc)

async def ensure_user(tg_user, ref=None):
    existing = await users.find_one({"telegram_id": tg_user.id})
    if existing:
        await users.update_one({"telegram_id": tg_user.id}, {"$set": {"username": tg_user.username, "first_name": tg_user.first_name, "updated_at": now()}})
        return existing
    doc = {"telegram_id": tg_user.id, "username": tg_user.username, "first_name": tg_user.first_name, "balance": 0.0, "language": "en", "currency": "INR", "created_at": now(), "updated_at": now()}
    await users.insert_one(doc)
    if ref and str(ref).isdigit() and int(ref) != tg_user.id:
        await referrals.update_one({"referred_id": tg_user.id}, {"$setOnInsert": {"referrer_id": int(ref), "referred_id": tg_user.id, "rewarded": False, "created_at": now()}}, upsert=True)
    return doc

async def list_products(): return await products.find({"enabled": True}).sort("position", 1).to_list(500)
async def get_product(pid):
    try: return await products.find_one({"_id": ObjectId(pid)})
    except Exception: return None
async def stock_count(pid): return await inventory.count_documents({"product_id": pid, "status": "available"})
async def atomic_take_inventory(pid, order_id):
    return await inventory.find_one_and_update({"product_id": pid, "status": "available"}, {"$set": {"status": "sold", "sold_at": now(), "order_id": order_id}}, sort=[("_id", 1)], return_document=ReturnDocument.AFTER)
async def create_wallet_tx(uid, amount, tx_type, note="", ref_id=None):
    d = {"user_id": uid, "amount": float(amount), "type": tx_type, "note": note, "reference_id": ref_id, "created_at": now()}
    await wallet_transactions.insert_one(d); return d
async def spend_balance(uid, amount, order_ref):
    r = await users.update_one({"telegram_id": uid, "balance": {"$gte": float(amount)}}, {"$inc": {"balance": -float(amount)}})
    if r.modified_count != 1: return False
    await create_wallet_tx(uid, -float(amount), "purchase", "Product purchase", order_ref); return True

async def release_inventory(order_id):
    """Release an inventory reservation made for an order that was not charged."""
    return await inventory.update_one(
        {"order_id": order_id, "status": "sold"},
        {"$set": {"status": "available"}, "$unset": {"order_id": "", "sold_at": ""}}
    )

def new_api_key():
    raw = "sb_" + secrets.token_urlsafe(32); return raw, hashlib.sha256(raw.encode()).hexdigest()
async def create_api_key(uid):
    raw, h = new_api_key()
    # Regeneration removes ALL previous keys of this user from the database
    # (not just deactivates them), so an old key can never authenticate again
    # and stale rows do not pile up. Auth lookup is key_hash+active, so after
    # the delete only the freshly inserted key can ever match.
    await api_keys.delete_many({"user_id": uid})
    await api_keys.insert_one({"user_id": uid, "key_hash": h, "active": True, "created_at": now()})
    return raw

async def _reward_config() -> dict:
    doc = await settings_col.find_one({"_id": "payment"})
    return doc or {}

async def grant_referral_reward(user_id, order_amount):
    """Credit the referrer when a referred user's order is delivered.
    Runs once per referral (rewarded flag is flipped atomically). Amount is
    configured via payment settings 'referral_reward_inr' (0 = disabled)."""
    ref = await referrals.find_one({"referred_id": user_id, "rewarded": False})
    if not ref:
        return
    reward = float((await _reward_config()).get("referral_reward_inr", 0) or 0)
    if reward <= 0:
        return
    updated = await referrals.update_one(
        {"_id": ref["_id"], "rewarded": False},
        {"$set": {"rewarded": True, "rewarded_at": now(), "reward_amount": reward}}
    )
    if updated.modified_count != 1:
        return
    await users.update_one({"telegram_id": ref["referrer_id"]}, {"$inc": {"balance": reward}})
    await create_wallet_tx(ref["referrer_id"], reward, "referral_bonus", f"Referral reward — referred user {user_id}", str(ref["_id"]))

async def grant_cashback(user_id, order_amount):
    """Credit configurable cashback (percent of order amount) to the buyer's wallet.
    Configured via payment settings 'cashback_percent' (0 = disabled)."""
    percent = float((await _reward_config()).get("cashback_percent", 0) or 0)
    if percent <= 0:
        return
    amount = round(float(order_amount) * percent / 100.0, 2)
    if amount <= 0:
        return
    await cashback.insert_one({"user_id": user_id, "amount": amount, "percent": percent, "created_at": now()})
    await users.update_one({"telegram_id": user_id}, {"$inc": {"balance": amount}})
    await create_wallet_tx(user_id, amount, "cashback", f"Cashback {percent}% on order", None)
