from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

client = AsyncIOMotorClient(settings.mongodb_uri)
db = client[settings.database_name]
users=db.users; products=db.products; inventory=db.inventory; orders=db.orders
wallet_transactions=db.wallet_transactions; referrals=db.referrals; cashback=db.cashback
api_keys=db.api_keys; api_logs=db.api_logs; tickets=db.support_tickets
settings_col=db.settings; audit_logs=db.audit_logs

async def init_db():
    # Fail fast if the MongoDB server is unreachable (bad URI / no network).
    await client.admin.command('ping')
    await users.create_index('telegram_id', unique=True)
    await products.create_index('slug', unique=True)
    await orders.create_index('order_id', unique=True)
    await inventory.create_index([('product_id',1),('status',1)])
    await api_keys.create_index('key_hash', unique=True)
