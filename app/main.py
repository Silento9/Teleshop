import asyncio, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.config import settings
from app.database.mongo import init_db, client as mongo_client
from app.bot.handlers.user import router as user_router
from app.bot.handlers.admin import router as admin_router
from app.api.info import router as api_info_router
from app.api.routes import router as api_router

logging.basicConfig(level=logging.INFO)
logging.getLogger('aiogram.event').setLevel(logging.WARNING)
bot=Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp=Dispatcher(); dp.include_router(user_router); dp.include_router(admin_router)

@asynccontextmanager
async def lifespan(app:FastAPI):
    await init_db()
    task=asyncio.create_task(dp.start_polling(bot,allowed_updates=dp.resolve_used_update_types()))
    try:
        yield
    finally:
        task.cancel()
        try: await task
        except (asyncio.CancelledError, Exception): pass
        await bot.session.close()
        mongo_client.close()

app=FastAPI(title='Teleshop Reseller API',version='2.0.0',lifespan=lifespan)
app.include_router(api_info_router); app.include_router(api_router)
@app.get('/')
async def root(): return {'name':'Teleshop Reseller API','status':'ok','version':'2.0.0'}
@app.get('/health')
async def health(): return {'status':'ok'}
