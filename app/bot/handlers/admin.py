from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timezone
from html import escape
from bson import ObjectId
import re, uuid

from app.config import settings
from app.database.mongo import products, inventory, users, orders, cashback, referrals, api_keys, wallet_transactions, db as database
from app.services.store import stock_count, atomic_take_inventory, grant_cashback, grant_referral_reward
from app.services.payment import get_payment_settings, save_payment_settings

router = Router()

def is_admin(uid): return uid in settings.admins

def now(): return datetime.now(timezone.utc)

class AddProduct(StatesGroup):
    name=State(); price=State(); description=State(); image=State(); warranty=State(); delivery=State(); confirm=State()
class StockAdd(StatesGroup): items=State()
class EditProduct(StatesGroup): value=State()
class PaymentConfig(StatesGroup): value=State()
class Broadcast(StatesGroup): message=State()

# ---------- keyboards ----------
def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📦 Products',callback_data='adm:products'),InlineKeyboardButton(text='📊 Orders',callback_data='adm:orders')],
        [InlineKeyboardButton(text='👥 Users',callback_data='adm:users'),InlineKeyboardButton(text='📈 Statistics',callback_data='adm:stats')],
        [InlineKeyboardButton(text='💳 Payments',callback_data='adm:payments'),InlineKeyboardButton(text='🎁 Cashback',callback_data='adm:cashback')],
        [InlineKeyboardButton(text='👥 Referral',callback_data='adm:referral'),InlineKeyboardButton(text='🔌 API',callback_data='adm:api')],
        [InlineKeyboardButton(text='📢 Broadcast',callback_data='adm:broadcast'),InlineKeyboardButton(text='⚙️ Settings',callback_data='adm:settings')],
    ])

def products_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='➕ Add Product',callback_data='adm:add_product')],[InlineKeyboardButton(text='📋 Product List',callback_data='adm:list_products')],[InlineKeyboardButton(text='⬅️ Admin Home',callback_data='adm:home')]])

async def product_list_kb():
    rows=[]
    for p in await products.find().sort('position',1).to_list(500):
        n=await stock_count(p['_id']); icon='🟢' if p.get('enabled',True) else '🔴'
        rows.append([InlineKeyboardButton(text=f"{icon} {p['name']} • ₹{float(p.get('price',0)):.2f} • 📦 {n}",callback_data=f"adm:product:{p['_id']}")])
    rows += [[InlineKeyboardButton(text='➕ Add Product',callback_data='adm:add_product')],[InlineKeyboardButton(text='⬅️ Back',callback_data='adm:products')]]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def manage_kb(pid,en):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✏️ Edit',callback_data=f'adm:edit:{pid}'),InlineKeyboardButton(text='📦 Add Stock',callback_data=f'adm:stock:{pid}')],
        [InlineKeyboardButton(text='📊 View Stock',callback_data=f'adm:viewstock:{pid}')],
        [InlineKeyboardButton(text=('🔴 Disable' if en else '🟢 Enable'),callback_data=f'adm:toggle:{pid}')],
        [InlineKeyboardButton(text='🗑 Delete',callback_data=f'adm:delete:{pid}')],
        [InlineKeyboardButton(text='⬅️ Product List',callback_data='adm:list_products')]])

# ---------- admin home ----------
@router.message(Command('admin'))
async def admin_cmd(m:Message):
    if is_admin(m.from_user.id): await m.answer('⚙️ <b>Admin Panel</b>\n\nAll store controls are below:',reply_markup=admin_kb(),parse_mode='HTML')

@router.callback_query(F.data=='adm:home')
async def adm_home(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    await c.message.edit_text('⚙️ <b>Admin Panel</b>\n\nAll store controls are below:',reply_markup=admin_kb(),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data=='adm:products')
async def adm_products(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    await c.message.edit_text('📦 <b>Products</b>\n\nChoose an action:',reply_markup=products_kb(),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data=='adm:list_products')
async def adm_list(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    await c.message.edit_text('📋 <b>Product List</b>',reply_markup=await product_list_kb(),parse_mode='HTML'); await c.answer()

# ---------- product creation ----------
@router.callback_query(F.data=='adm:add_product')
async def add_start(c:CallbackQuery,state:FSMContext):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    await state.set_state(AddProduct.name); await c.message.edit_text('📝 <b>Add Product</b>\n\nSend product name:'); await c.answer()

@router.message(AddProduct.name)
async def add_name(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    if not (m.text or '').strip(): return await m.answer('❌ Send a valid name.')
    await state.update_data(name=m.text.strip()); await state.set_state(AddProduct.price); await m.answer('💵 Send base price in INR, e.g. <code>499</code>.',parse_mode='HTML')

@router.message(AddProduct.price)
async def add_price(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    try: price=round(float(m.text.replace(',','')),2); assert price>0
    except Exception: return await m.answer('❌ Invalid price. Send a positive INR amount.')
    await state.update_data(price=price); await state.set_state(AddProduct.description); await m.answer('📝 Send product description:')

@router.message(AddProduct.description)
async def add_desc(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    await state.update_data(description=(m.text or '').strip()); await state.set_state(AddProduct.image)
    await m.answer('🖼 <b>Product logo</b>\n\nSend a photo to use as the product logo, or tap Skip.',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⏭ Skip Logo',callback_data='adm:skip_image')]]),parse_mode='HTML')

@router.message(AddProduct.image,F.photo)
async def add_image(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    await state.update_data(image=m.photo[-1].file_id); await state.set_state(AddProduct.warranty); await m.answer('🛡 Warranty days (0 for none):')

@router.callback_query(F.data=='adm:skip_image',AddProduct.image)
async def skip_image(c:CallbackQuery,state:FSMContext):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    await state.update_data(image=None); await state.set_state(AddProduct.warranty); await c.message.edit_text('🛡 Warranty days (0 for none):'); await c.answer()

@router.message(AddProduct.warranty)
async def add_warranty(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    try: w=int(m.text); assert w>=0
    except Exception: return await m.answer('❌ Enter a whole number.')
    await state.update_data(warranty_days=w); await state.set_state(AddProduct.delivery)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔑 License / Code',callback_data='deliv:code')],[InlineKeyboardButton(text='🔗 Download Link',callback_data='deliv:link')],[InlineKeyboardButton(text='📄 File',callback_data='deliv:file')],[InlineKeyboardButton(text='📝 Text',callback_data='deliv:text')]])
    await m.answer('📦 Choose delivery type:',reply_markup=kb)

@router.callback_query(F.data.startswith('deliv:'),AddProduct.delivery)
async def add_delivery(c:CallbackQuery,state:FSMContext):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    await state.update_data(delivery_type=c.data.split(':',1)[1]); d=await state.get_data(); await state.set_state(AddProduct.confirm)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ Create Product',callback_data='adm:create_product')],[InlineKeyboardButton(text='❌ Cancel',callback_data='adm:cancel_add')]])
    await c.message.edit_text(f"📦 <b>Confirm Product</b>\n\nName: {escape(d['name'])}\nPrice: ₹{d['price']:.2f}\nWarranty: {d['warranty_days']} days\nDelivery: {d['delivery_type']}\nLogo: {'Yes' if d.get('image') else 'No'}\n\n{escape(d['description'])}",reply_markup=kb,parse_mode='HTML'); await c.answer()

@router.callback_query(F.data=='adm:create_product',AddProduct.confirm)
async def create_product(c:CallbackQuery,state:FSMContext):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    d=await state.get_data(); slug=re.sub(r'[^a-z0-9]+','-',d['name'].lower()).strip('-')+'-'+uuid.uuid4().hex[:6]
    await products.insert_one({'name':d['name'],'slug':slug,'description':d['description'],'price':d['price'],'currency':'INR','image':d.get('image'),'delivery_type':d['delivery_type'],'warranty_days':d['warranty_days'],'enabled':True,'sold':0,'position':await products.count_documents({}),'created_at':now()})
    await state.clear(); await c.message.edit_text('✅ <b>Product created successfully.</b>\n\nOpen Product List → select it → Add Stock.',reply_markup=products_kb(),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data=='adm:cancel_add',AddProduct.confirm)
async def cancel_add(c:CallbackQuery,state:FSMContext):
    await state.clear(); await c.message.edit_text('❌ Product creation cancelled.',reply_markup=products_kb()); await c.answer()

# ---------- product manager ----------
@router.callback_query(F.data.startswith('adm:product:'))
async def manage_product(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    pid=c.data.rsplit(':',1)[1]
    try: p=await products.find_one({'_id':ObjectId(pid)})
    except Exception: p=None
    if not p: return await c.answer('Product not found.',show_alert=True)
    n=await stock_count(p['_id']); en=p.get('enabled',True)
    text=f"📦 <b>{escape(p['name'])}</b>\n\n💵 ₹{float(p['price']):.2f}\n📦 Available: {n}\n📊 Sold: {p.get('sold',0)}\n🛡 Warranty: {p.get('warranty_days',0)} days\n🖼 Logo: {'Yes' if p.get('image') else 'No'}\nStatus: {'🟢 Enabled' if en else '🔴 Disabled'}\n\n{escape(p.get('description',''))}"
    await c.message.edit_text(text,reply_markup=manage_kb(pid,en),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data.startswith('adm:toggle:'))
async def toggle(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    pid=c.data.rsplit(':',1)[1]; p=await products.find_one({'_id':ObjectId(pid)})
    if not p:return await c.answer('Not found.',show_alert=True)
    await products.update_one({'_id':p['_id']},{'$set':{'enabled':not p.get('enabled',True)}}); await c.answer('Updated'); await manage_product(c)

@router.callback_query(F.data.startswith('adm:delete:'))
async def delete(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    pid=c.data.rsplit(':',1)[1]; kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⚠️ Confirm Delete',callback_data=f'adm:confirmdelete:{pid}')],[InlineKeyboardButton(text='❌ Cancel',callback_data=f'adm:product:{pid}')]])
    await c.message.edit_text('⚠️ <b>Delete Product?</b>\n\nIf it has order history, it will be disabled instead of deleted.',reply_markup=kb,parse_mode='HTML'); await c.answer()

@router.callback_query(F.data.startswith('adm:confirmdelete:'))
async def confirm_delete(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    pid=c.data.rsplit(':',1)[1]; p=await products.find_one({'_id':ObjectId(pid)})
    if not p:return await c.answer('Not found.',show_alert=True)
    if await orders.count_documents({'product_id':p['_id']}): await products.update_one({'_id':p['_id']},{'$set':{'enabled':False}}); await c.answer('Disabled; order history preserved.')
    else: await products.delete_one({'_id':p['_id']}); await c.answer('Deleted.')
    await c.message.edit_text('📋 <b>Product List</b>',reply_markup=await product_list_kb(),parse_mode='HTML')

# ---------- stock ----------
@router.callback_query(F.data.startswith('adm:stock:'))
async def stock_button(c:CallbackQuery,state:FSMContext):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    pid=c.data.rsplit(':',1)[1]
    try:p=await products.find_one({'_id':ObjectId(pid)})
    except Exception:p=None
    if not p:return await c.answer('Product not found.',show_alert=True)
    await state.update_data(product_id=pid); await state.set_state(StockAdd.items)
    await c.message.edit_text(f"📦 <b>Add Stock — {escape(p['name'])}</b>\n\nSend one legitimate digital item per line.\nExample:\n<code>LICENSE-001\nLICENSE-002</code>\n\nSend /cancel to abort.",parse_mode='HTML'); await c.answer()

@router.message(Command('stock'))
async def stock_command(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    parts=(m.text or '').split(maxsplit=1)
    if len(parts)<2:return await m.answer('Usage: <code>/stock PRODUCT_ID</code>',parse_mode='HTML')
    pid=parts[1].strip()
    try:p=await products.find_one({'_id':ObjectId(pid)})
    except Exception:p=None
    if not p:return await m.answer('❌ Product not found.')
    await state.update_data(product_id=pid); await state.set_state(StockAdd.items); await m.answer(f"📦 <b>Add Stock — {escape(p['name'])}</b>\n\nSend one item per line. Send /cancel to abort.",parse_mode='HTML')

@router.message(StockAdd.items)
async def stock_save(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    if (m.text or '').strip().lower()=='/cancel': await state.clear(); return await m.answer('❌ Stock entry cancelled.')
    d=await state.get_data(); pid=d.get('product_id'); lines=[x.strip() for x in (m.text or '').splitlines() if x.strip()]
    if not pid or not lines:return await m.answer('❌ Send at least one item, one per line.')
    try: oid=ObjectId(pid); p=await products.find_one({'_id':oid})
    except Exception:p=None
    if not p: await state.clear(); return await m.answer('❌ Product not found.')
    docs=[{'product_id':oid,'value':v,'status':'available','created_at':now()} for v in lines]
    await inventory.insert_many(docs); await state.clear(); await m.answer(f"✅ <b>{len(docs)}</b> stock item(s) added to <b>{escape(p['name'])}</b>.",parse_mode='HTML')

@router.callback_query(F.data.startswith('adm:viewstock:'))
async def viewstock(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    pid=c.data.rsplit(':',1)[1]; oid=ObjectId(pid); items=await inventory.find({'product_id':oid,'status':'available'}).limit(30).to_list(30); sold=await inventory.count_documents({'product_id':oid,'status':'sold'})
    text=f"📦 <b>Stock</b>\n\nAvailable: {len(items)}\nSold: {sold}\n\n"+('\n'.join('• '+escape(str(x.get('value','')))[:80] for x in items) if items else 'No available stock.')
    p=await products.find_one({'_id':oid}); await c.message.edit_text(text,reply_markup=manage_kb(pid,p.get('enabled',True) if p else True),parse_mode='HTML'); await c.answer()

# ---------- edit product ----------
@router.callback_query(F.data.startswith('adm:edit:'))
async def edit_menu(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    pid=c.data.rsplit(':',1)[1]
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📝 Name',callback_data=f'adm:editfield:{pid}:name'),InlineKeyboardButton(text='💵 Price',callback_data=f'adm:editfield:{pid}:price')],[InlineKeyboardButton(text='📄 Description',callback_data=f'adm:editfield:{pid}:description'),InlineKeyboardButton(text='🛡 Warranty',callback_data=f'adm:editfield:{pid}:warranty')],[InlineKeyboardButton(text='🖼 Replace Logo',callback_data=f'adm:editfield:{pid}:image')],[InlineKeyboardButton(text='⬅️ Back',callback_data=f'adm:product:{pid}')]])
    await c.message.edit_text('✏️ <b>Edit Product</b>\n\nChoose what to change:',reply_markup=kb,parse_mode='HTML'); await c.answer()

@router.callback_query(F.data.startswith('adm:editfield:'))
async def edit_field(c:CallbackQuery,state:FSMContext):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    _,_,pid,field=c.data.split(':',3); await state.update_data(product_id=pid,field=field); await state.set_state(EditProduct.value)
    prompt={'name':'Send new name:','price':'Send new INR price:','description':'Send new description:','warranty':'Send warranty days:','image':'Send the new logo photo:'}.get(field,'Send new value:')
    await c.message.edit_text('✏️ <b>Edit Product</b>\n\n'+prompt,parse_mode='HTML'); await c.answer()

@router.message(EditProduct.value)
async def edit_save(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id): return
    d=await state.get_data(); pid=d.get('product_id'); field=d.get('field')
    try: oid=ObjectId(pid); p=await products.find_one({'_id':oid})
    except Exception:p=None
    if not p: await state.clear(); return await m.answer('❌ Product not found.')
    if field=='image':
        if not m.photo:return await m.answer('❌ Send a photo.')
        value=m.photo[-1].file_id
    elif field=='price':
        try:value=round(float(m.text.replace(',','')),2); assert value>0
        except Exception:return await m.answer('❌ Invalid price.')
    elif field=='warranty':
        try:value=int(m.text); assert value>=0
        except Exception:return await m.answer('❌ Invalid warranty days.')
    else:
        value=(m.text or '').strip()
        if not value:return await m.answer('❌ Value cannot be empty.')
    await products.update_one({'_id':oid},{'$set':{field if field!='warranty' else 'warranty_days':value,'updated_at':now()}})
    await state.clear(); await m.answer('✅ Product updated successfully.')

# ---------- orders / users / stats ----------
@router.callback_query(F.data=='adm:orders')
async def adm_orders(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    docs=await orders.find().sort('created_at',-1).limit(20).to_list(20); lines=[]
    for d in docs:
        lines.append(f"• <code>{d['order_id']}</code> — {escape(str(d.get('product_name','')))} — ₹{float(d.get('amount',0)):.2f} — {d.get('status','')}")
    kb=[[InlineKeyboardButton(text='🪙 Pending USDT',callback_data='adm:usdtpending')],[InlineKeyboardButton(text='⬅️ Admin Home',callback_data='adm:home')]]
    await c.message.edit_text('📊 <b>Recent Orders</b>\n\n'+('\n'.join(lines) if lines else 'No orders.'),reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data=='adm:users')
async def adm_users(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    total=await users.count_documents({}); active=await users.count_documents({'updated_at':{'$exists':True}})
    await c.message.edit_text(f'👥 <b>Users</b>\n\nTotal registered: <b>{total}</b>\nProfiles with activity data: <b>{active}</b>',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Admin Home',callback_data='adm:home')]]),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data=='adm:stats')
async def stats(c:CallbackQuery):
    if not is_admin(c.from_user.id): return await c.answer('Not allowed',show_alert=True)
    await c.message.edit_text(f"📈 <b>Statistics</b>\n\n👥 Users: {await users.count_documents({})}\n📦 Products: {await products.count_documents({})}\n🧾 Orders: {await orders.count_documents({})}\n💳 Wallet transactions: {await wallet_transactions.count_documents({})}\n📦 Available stock: {await inventory.count_documents({'status':'available'})}\n💰 Delivered revenue: ₹{float((await orders.aggregate([{'$match':{'status':'delivered'}},{'$group':{'_id':None,'x':{'$sum':'$amount'}}}]).to_list(1) or [{'x':0}])[0]['x']):.2f}",reply_markup=admin_kb(),parse_mode='HTML'); await c.answer()

# ---------- payments ----------
def payment_admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='📱 Paytm: ON/OFF',callback_data='paycfg:toggle:paytm')],
        [InlineKeyboardButton(text='⭐ Stars: ON/OFF',callback_data='paycfg:toggle:stars')],
        [InlineKeyboardButton(text='🪙 USDT: ON/OFF',callback_data='paycfg:toggle:usdt')],
        [InlineKeyboardButton(text='🆔 Merchant ID',callback_data='paycfg:set:merchant_id'),InlineKeyboardButton(text='📱 UPI ID',callback_data='paycfg:set:upi_id')],
        [InlineKeyboardButton(text='🌐 Paytm Handler',callback_data='paycfg:set:handler_url')],
        [InlineKeyboardButton(text='🪙 USDT Address',callback_data='paycfg:set:usdt_address')],
        [InlineKeyboardButton(text='🔗 USDT Network',callback_data='paycfg:set:usdt_network')],
        [InlineKeyboardButton(text='💱 USDT Rate',callback_data='paycfg:set:usdt_inr_rate'),InlineKeyboardButton(text='⭐ Stars Rate',callback_data='paycfg:set:star_inr_rate')],
        [InlineKeyboardButton(text='🎁 Cashback %',callback_data='paycfg:set:cashback_percent'),InlineKeyboardButton(text='👥 Referral Reward ₹',callback_data='paycfg:set:referral_reward_inr')],
        [InlineKeyboardButton(text='🔄 Refresh',callback_data='adm:payments'),InlineKeyboardButton(text='⬅️ Home',callback_data='adm:home')],
    ])

async def payment_text():
    c=await get_payment_settings()
    return (f"💳 <b>Payment Settings</b>\n\n📱 Paytm (external checkout): {'🟢 ON' if c.get('paytm_enabled',True) else '🔴 OFF'}\n"
            f"⭐ Telegram Stars (Telegram checkout): {'🟢 ON' if c.get('stars_enabled',True) else '🔴 OFF'}\n🪙 USDT (external/manual): {'🟢 ON' if c.get('usdt_enabled',False) else '🔴 OFF'}\n\n"
            f"Merchant ID: <code>{escape(str(c.get('merchant_id') or 'Not set'))}</code>\nUPI ID: <code>{escape(str(c.get('upi_id') or 'Not set'))}</code>\n"
            f"Handler: <code>{escape(str(c.get('handler_url') or 'Not set'))}</code>\nUSDT address: <code>{escape(str(c.get('usdt_address') or 'Not set'))}</code>\n"
            f"USDT network: <b>{escape(str(c.get('usdt_network','TRC20')))}</b>\n1 USDT = ₹{float(c.get('usdt_inr_rate',90)):.2f}\n1 Star = ₹{float(c.get('star_inr_rate',1)):.2f}\n\n"
            f"🎁 Cashback: {float(c.get('cashback_percent',0) or 0):.2f}% {'(OFF)' if float(c.get('cashback_percent',0) or 0)<=0 else 'on every delivered order'}\n"
            f"👥 Referral reward: ₹{float(c.get('referral_reward_inr',0) or 0):.2f} {'(OFF)' if float(c.get('referral_reward_inr',0) or 0)<=0 else 'per referred user\'s first delivered order'}")

@router.callback_query(F.data=='adm:payments')
async def adm_payments(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    await c.message.edit_text(await payment_text(),reply_markup=payment_admin_kb(),parse_mode='HTML'); await c.answer()

PAYMENT_ENABLED_DEFAULTS = {'paytm_enabled': True, 'stars_enabled': True, 'usdt_enabled': False}

@router.callback_query(F.data.startswith('paycfg:toggle:'))
async def pay_toggle(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    key=c.data.rsplit(':',1)[1]+'_enabled'
    if key not in PAYMENT_ENABLED_DEFAULTS: return await c.answer('Unknown setting.',show_alert=True)
    cfg=await get_payment_settings(); cfg[key]=not cfg.get(key, PAYMENT_ENABLED_DEFAULTS[key]); await save_payment_settings(cfg); await c.answer('Updated'); await adm_payments(c)

@router.callback_query(F.data.startswith('paycfg:set:'))
async def pay_set(c:CallbackQuery,state:FSMContext):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    field=c.data.rsplit(':',1)[1]; await state.update_data(field=field); await state.set_state(PaymentConfig.value)
    labels={'merchant_id':'Merchant ID','upi_id':'Paytm UPI / QR ID','handler_url':'Paytm Handler URL','usdt_address':'USDT wallet address','usdt_network':'USDT network (e.g. TRC20)','usdt_inr_rate':'USDT INR rate (e.g. 90)','star_inr_rate':'Stars INR rate (e.g. 1)','cashback_percent':'Cashback percent (0-100, 0 = OFF, e.g. 5)','referral_reward_inr':'Referral reward in INR (0 = OFF, e.g. 50)'}
    await c.message.edit_text(f"✏️ <b>{labels.get(field,field)}</b>\n\nSend the new value:",parse_mode='HTML'); await c.answer()

@router.message(PaymentConfig.value)
async def pay_save(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id):return
    d=await state.get_data(); field=d.get('field'); raw=(m.text or '').strip()
    if not raw:return await m.answer('❌ Value cannot be empty.')
    if field in ('usdt_inr_rate','star_inr_rate'):
        try: value=float(raw); assert value>0
        except Exception:return await m.answer('❌ Enter a positive number.')
    elif field=='cashback_percent':
        try: value=float(raw); assert 0<=value<=100
        except Exception:return await m.answer('❌ Enter a percent between 0 and 100 (0 = OFF).')
    elif field=='referral_reward_inr':
        try: value=float(raw); assert 0<=value<=100000
        except Exception:return await m.answer('❌ Enter an amount between 0 and 100000 (0 = OFF).')
    else:value=raw
    cfg=await get_payment_settings(); cfg[field]=value; await save_payment_settings(cfg); await state.clear(); await m.answer('✅ Payment setting saved.'); await m.answer(await payment_text(),reply_markup=payment_admin_kb(),parse_mode='HTML')

# ---------- USDT manual verification ----------
@router.callback_query(F.data=='adm:usdtpending')
async def usdt_pending(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    docs=await orders.find({'status':'pending_usdt'}).sort('created_at',1).limit(30).to_list(30); rows=[]
    for d in docs:
        inr=float(d.get('amount',0)); usdt=float(d.get('usdt_amount') or 0)
        label=f"{d['order_id']} • ₹{inr:.2f}" + (f" • {usdt:.6f} USDT" if usdt else '')
        rows.append([InlineKeyboardButton(text=f"✅ {label}",callback_data=f"adm:usdtapprove:{d['order_id']}")])
    rows.append([InlineKeyboardButton(text='⬅️ Orders',callback_data='adm:orders')])
    await c.message.edit_text('🪙 <b>Pending USDT Orders</b>\n\n'+(f'{len(docs)} pending order(s).' if docs else 'No pending USDT orders.'),reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data.startswith('adm:usdtapprove:'))
async def usdt_approve(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    oid=c.data.rsplit(':',1)[1]; order=await orders.find_one({'order_id':oid,'status':'pending_usdt'})
    if not order:return await c.answer('Order already processed.',show_alert=True)
    # Atomically transition pending_usdt -> paid_processing so retries cannot double-deliver.
    transitioned=await orders.update_one({'order_id':oid,'status':'pending_usdt'},{'$set':{'status':'paid_processing','payment_provider':'usdt_manual','verified_at':datetime.now(timezone.utc)}})
    if transitioned.modified_count!=1:return await c.answer('Order already processed.',show_alert=True)
    item=await atomic_take_inventory(order['product_id'],oid)
    if not item:
        await orders.update_one({'order_id':oid,'status':'paid_processing'},{'$set':{'status':'paid_no_stock','verified_at':datetime.now(timezone.utc)}})
        try: await c.bot.send_message(order['user_id'],f"⚠️ <b>USDT payment received, but the product is out of stock.</b>\n\nOrder: <code>{oid}</code>\nYour payment is recorded — contact support for a refund or delivery once stock is added.",parse_mode='HTML')
        except Exception: pass
        return await c.answer('No stock available. User has been notified.',show_alert=True)
    await orders.update_one({'order_id':oid,'status':'paid_processing'},{'$set':{'status':'delivered','delivery':item['value']}})
    await users.update_one({'telegram_id':order['user_id']},{'$inc':{'total_orders':1}})
    # Rewards parity: USDT-approved orders earn the same cashback/referral as bot-checkout orders.
    try:
        await grant_cashback(order['user_id'], float(order.get('amount',0)))
        await grant_referral_reward(order['user_id'], float(order.get('amount',0)))
    except Exception: pass
    try: await c.bot.send_message(order['user_id'],f"✅ <b>USDT Payment Approved</b>\n\nOrder: <code>{oid}</code>\n🛍 {escape(order['product_name'])}\n\n📦 <b>Your item:</b>\n<code>{escape(str(item['value']))}</code>",parse_mode='HTML')
    except Exception: pass
    await c.answer('Order approved and delivered.'); await usdt_pending(c)

# ---------- cashback/referral/api/settings ----------
@router.callback_query(F.data=='adm:cashback')
async def admin_cashback(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    total=await cashback.aggregate([{'$group':{'_id':None,'x':{'$sum':'$amount'}}}]).to_list(1); amount=float(total[0]['x']) if total else 0
    await c.message.edit_text(f'🎁 <b>Cashback</b>\n\nTotal credited: ₹{amount:.2f}\nRecords: {await cashback.count_documents({})}',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Home',callback_data='adm:home')]]),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data=='adm:referral')
async def admin_referral(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    count=await referrals.count_documents({}); rewarded=await referrals.count_documents({'rewarded':True})
    await c.message.edit_text(f'👥 <b>Referral</b>\n\nTotal: {count}\nRewarded: {rewarded}',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Home',callback_data='adm:home')]]),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data=='adm:api')
async def admin_api(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    await c.message.edit_text(f'🔌 <b>API</b>\n\nActive keys: {await api_keys.count_documents({"active":True})}\nTotal keys: {await api_keys.count_documents({})}\n\nAPI integration guide is available from the user API menu.',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='⬅️ Home',callback_data='adm:home')]]),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data=='adm:settings')
async def admin_settings(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    await c.message.edit_text(f"⚙️ <b>Settings</b>\n\nDatabase: MongoDB\nPublic URL: <code>{escape(settings.public_base_url or 'Not configured')}</code>\nSupport: @{escape(settings.support_username or 'Not configured')}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='💳 Payment Settings',callback_data='adm:payments')],[InlineKeyboardButton(text='🗄 Database Manager',callback_data='adm:db')],[InlineKeyboardButton(text='⬅️ Home',callback_data='adm:home')]]),parse_mode='HTML'); await c.answer()

# ---------- database manager ----------
# Core collections the bot needs to function — NEVER deletable from the panel.
PROTECTED_COLLECTIONS = {'users','products','inventory','orders','wallet_transactions','referrals','cashback','api_keys','api_logs','support_tickets','settings','audit_logs'}

def _db_name_safe(name:str)->bool:
    # only simple collection names; block protected/system/tricky names
    return bool(name) and not name.startswith('system.') and name not in PROTECTED_COLLECTIONS and name.replace('_','').replace('-','').isalnum()

async def _render_db_manager(c:CallbackQuery, note:str=''):
    names=await database.list_collection_names()
    rows=[]
    for n in sorted(names):
        if not _db_name_safe(n): continue
        cnt=await database[n].count_documents({})
        rows.append([InlineKeyboardButton(text=f'🗂 {n} • {cnt} docs',callback_data=f'adm:dbcol:{n}')])
    text=('🗄 <b>Database Manager</b>\n\nCore tables (users, orders, products, inventory, wallet, referrals, cashback, api_keys, settings, etc.) are protected and hidden here.\n\nNon-core collections:'+(f'\n{note}' if note else '') if rows else '🗄 <b>Database Manager</b>\n\nNo non-core collections found. Only protected core tables exist.')
    kb=[[InlineKeyboardButton(text='🔄 Refresh',callback_data='adm:db')],[InlineKeyboardButton(text='⬅️ Settings',callback_data='adm:settings')]]
    await c.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=(rows+kb) if rows else kb),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data=='adm:db')
async def db_manager(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    await _render_db_manager(c)

@router.callback_query(F.data.startswith('adm:dbcol:'))
async def db_col_view(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    name=c.data.split(':',2)[2]
    if not _db_name_safe(name):return await c.answer('Protected collection.',show_alert=True)
    cnt=await database[name].count_documents({})
    await c.message.edit_text(f"🗄 <b>Collection:</b> <code>{escape(name)}</code>\n\n📄 Documents: <b>{cnt}</b>\n\n⚠️ Deleting removes this collection and ALL its documents permanently. This cannot be undone.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🗑 Delete Collection',callback_data=f'adm:dbdel:{name}')],[InlineKeyboardButton(text='⬅️ Back',callback_data='adm:db')]]),parse_mode='HTML'); await c.answer()

@router.callback_query(F.data.startswith('adm:dbdel:'))
async def db_col_delete(c:CallbackQuery):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    name=c.data.split(':',2)[2]
    if not _db_name_safe(name):return await c.answer('Protected collection — cannot delete.',show_alert=True)
    await database[name].drop()
    await _render_db_manager(c,note=f"\n✅ <code>{escape(name)}</code> deleted.")

@router.callback_query(F.data=='adm:broadcast')
async def broadcast_start(c:CallbackQuery,state:FSMContext):
    if not is_admin(c.from_user.id):return await c.answer('Not allowed',show_alert=True)
    await state.set_state(Broadcast.message); await c.message.edit_text('📢 <b>Broadcast</b>\n\nSend text to all registered users. Send /cancel to abort.',parse_mode='HTML'); await c.answer()

@router.message(Broadcast.message)
async def broadcast_send(m:Message,state:FSMContext):
    if not is_admin(m.from_user.id):return
    if (m.text or '').strip().lower()=='/cancel':await state.clear();return await m.answer('❌ Broadcast cancelled.')
    text=m.text or ''
    if not text:return await m.answer('❌ Send text or /cancel.')
    docs=await users.find({}, {'telegram_id':1}).to_list(10000); ok=failed=0
    for d in docs:
        try:await m.bot.send_message(d['telegram_id'],text);ok+=1
        except Exception:failed+=1
    await state.clear(); await m.answer(f'📢 <b>Broadcast complete</b>\n\n✅ Sent: {ok}\n❌ Failed: {failed}',parse_mode='HTML')


@router.callback_query(F.data.startswith('adm:') | F.data.startswith('paycfg:') | F.data.startswith('deliv:'))
async def unknown_admin_callback(c: CallbackQuery):
    # Catch-all for stale admin-panel buttons. Must always answer so the user's
    # button spinner does not hang; non-admins get no details.
    await c.answer('This button is no longer active. Open Admin Home and try again.', show_alert=is_admin(c.from_user.id))
