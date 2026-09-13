from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from html import escape
from datetime import datetime, timezone
import uuid, random

from app.services.store import ensure_user, list_products, get_product, stock_count, atomic_take_inventory, create_wallet_tx, create_api_key, grant_referral_reward, grant_cashback
from app.services.payment import generate_paytm_qr, verify_paytm_payment, get_payment_settings, usdt_amount_from_inr, stars_amount_from_inr
from app.bot.keyboards.main import main_kb, back_kb
from app.bot.keyboards.shop import product_list_kb, product_detail_kb
from app.bot.keyboards.payment import payment_methods_kb, payment_verify_kb, usdt_paid_kb, wallet_method_kb, topup_verify_kb
from app.database.mongo import users, orders, cashback, referrals, api_keys
from app.config import settings

router = Router()

async def safe_edit_message(message: Message, text: str, reply_markup=None, parse_mode="HTML"):
    """Edit a text message or replace a photo message safely.

    Telegram does not allow edit_text() on photo messages. Product pages use
    photos for logos, so navigation must work for both message types.
    If the original message was deleted or cannot be edited, fall back to
    sending a fresh message so navigation never breaks.
    """
    if getattr(message, "photo", None):
        try:
            await message.delete()
        except Exception:
            pass
        return await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    try:
        return await message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        # Message deleted / identical content / not editable — send a new one.
        try:
            await message.delete()
        except Exception:
            pass
        return await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)

class WalletTopup(StatesGroup):
    method = State()
    amount = State()

class USDTProof(StatesGroup):
    txid = State()

async def now(): return datetime.now(timezone.utc)

def money(inr, currency, cfg):
    inr = float(inr)
    if currency == 'USDT': return f"${usdt_amount_from_inr(inr, float(cfg.get('usdt_inr_rate', 90))):.4f} USDT"
    if currency == 'XTR': return f"⭐ {stars_amount_from_inr(inr, float(cfg.get('star_inr_rate', 1)))} Stars"
    return f"₹{inr:.2f} INR"

async def user_currency(uid):
    u = await users.find_one({'telegram_id': uid}) or {}
    return u.get('currency', 'INR')

async def home_text():
    return "🛍 <b>Welcome to Teleshop</b>\n\nBuy legitimate digital products instantly.\nChoose an option below:"

@router.message(CommandStart())
async def start(message: Message):
    ref = None
    if message.text and len(message.text.split()) > 1:
        # /start ref_123456789 — keep digits only so injected junk cannot reach the DB.
        raw = message.text.split(maxsplit=1)[1].replace('ref_', '')
        digits = ''.join(ch for ch in raw if ch.isdigit())
        if digits:
            ref = digits[:20]
    await ensure_user(message.from_user, ref)
    await message.answer(await home_text(), reply_markup=main_kb(), parse_mode='HTML')

@router.callback_query(F.data == 'home')
async def home(cb: CallbackQuery):
    await safe_edit_message(cb.message, await home_text(), reply_markup=main_kb(), parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'buy')
async def buy(cb: CallbackQuery):
    ps = await list_products(); counts = {str(p['_id']): await stock_count(p['_id']) for p in ps}
    currency = await user_currency(cb.from_user.id); cfg = await get_payment_settings()
    text = '🛍 <b>Store</b>\n\nChoose a product:' if ps else '🛍 <b>Store</b>\n\nNo products are available right now.'
    await safe_edit_message(cb.message, text, reply_markup=product_list_kb(ps, counts, currency, float(cfg.get('usdt_inr_rate',90)), float(cfg.get('star_inr_rate',1))), parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data.startswith('prod:'))
async def product(cb: CallbackQuery):
    pid = cb.data.split(':',1)[1]; p = await get_product(pid)
    if not p: return await cb.answer('Product not found.', show_alert=True)
    n = await stock_count(p['_id']); currency = await user_currency(cb.from_user.id); cfg = await get_payment_settings()
    price = money(p['price'], currency, cfg)
    text = (f"🛍 <b>{escape(p['name'])}</b>\n\n💵 Price: <b>{price}</b>\n"
            f"🛡 Warranty: {int(p.get('warranty_days',0))} days\n📦 Stock: {n}\n📊 Sold: {p.get('sold',0)}\n\n📝 {escape(p.get('description',''))}")
    # Telegram inline buttons cannot contain arbitrary image thumbnails. If a product has a logo/photo,
    # show it above the inline buttons so the image is actually visible to the buyer.
    if p.get('image'):
        try:
            await cb.message.delete()
        except Exception: pass
        await cb.message.answer_photo(p['image'], caption=text, reply_markup=product_detail_kb(pid,n>0,currency), parse_mode='HTML')
    else:
        await safe_edit_message(cb.message, text, reply_markup=product_detail_kb(pid,n>0,currency), parse_mode='HTML')
    await cb.answer()

@router.callback_query(F.data.startswith('buyprod:'))
async def buy_product(cb: CallbackQuery):
    pid = cb.data.split(':',1)[1]; p = await get_product(pid)
    if not p or not p.get('enabled',True): return await cb.answer('Product unavailable.', show_alert=True)
    if await stock_count(p['_id']) <= 0: return await cb.answer('Out of stock.', show_alert=True)
    cfg = await get_payment_settings(); currency = await user_currency(cb.from_user.id)
    if not (cfg.get('stars_enabled',True) or cfg.get('paytm_enabled',True) or cfg.get('usdt_enabled',False)):
        return await cb.answer('No payment method is available right now. Please contact support.', show_alert=True)
    oid = 'ORD-' + uuid.uuid4().hex[:10].upper()
    await orders.insert_one({'order_id':oid,'user_id':cb.from_user.id,'product_id':p['_id'],'product_name':p['name'],'amount':float(p['price']),'status':'payment_method','currency':currency,'created_at':datetime.now(timezone.utc)})
    await safe_edit_message(cb.message, f"💳 <b>Choose Payment Method</b>\n\n🛍 {escape(p['name'])}\n💵 {money(p['price'],currency,cfg)}\n🧾 <code>{oid}</code>\n\nSelect how you want to pay:", reply_markup=payment_methods_kb(oid, bool(cfg.get('stars_enabled',True)), False, bool(cfg.get('paytm_enabled',True)), bool(cfg.get('usdt_enabled',False))), parse_mode='HTML'); await cb.answer()

async def deliver_order(cb_or_message, order, provider):
    """Atomically transition an unpaid order into processing before consuming stock.

    This makes successful-payment retries idempotent: a duplicate Telegram payment
    update cannot consume a second inventory item after the order is already delivered.
    """
    oid = order['order_id']
    current = await orders.find_one({'order_id': oid})
    if not current:
        return None, 'Order not found.'
    if current.get('status') == 'delivered':
        return {'value': current.get('delivery', '')}, None
    if current.get('status') == 'paid_no_stock':
        return None, 'Payment received, but the product is out of stock. Please contact support.'

    transitioned = await orders.update_one(
        {'order_id': oid, 'status': {'$in': ['payment_method', 'awaiting_payment', 'pending_usdt', 'paid']}},
        {'$set': {'status': 'paid_processing', 'payment_provider': provider, 'paid_at': datetime.now(timezone.utc)}}
    )
    if transitioned.modified_count != 1:
        latest = await orders.find_one({'order_id': oid})
        if latest and latest.get('status') == 'delivered':
            return {'value': latest.get('delivery', '')}, None
        if latest and latest.get('status') == 'paid_no_stock':
            return None, 'Payment received, but the product is out of stock. Please contact support.'
        return None, 'Order is already being processed.'

    p = await get_product(str(order['product_id']))
    if not p:
        await orders.update_one({'order_id': oid, 'status': 'paid_processing'}, {'$set': {'status': 'paid_error', 'error': 'Product no longer exists.'}})
        return None, 'Product no longer exists.'

    item = await atomic_take_inventory(p['_id'], oid)
    if not item:
        await orders.update_one(
            {'order_id': oid, 'status': 'paid_processing'},
            {'$set': {'status': 'paid_no_stock', 'payment_provider': provider, 'verified_at': datetime.now(timezone.utc)}}
        )
        return None, 'Payment received, but the product is out of stock. Please contact support.'

    updated = await orders.update_one(
        {'order_id': oid, 'status': 'paid_processing'},
        {'$set': {'status': 'delivered', 'delivery': item['value'], 'payment_provider': provider, 'verified_at': datetime.now(timezone.utc)}}
    )
    if updated.modified_count == 1:
        await users.update_one({'telegram_id': order['user_id']}, {'$inc': {'total_orders': 1}})
        from app.database.mongo import products
        await products.update_one({'_id': p['_id']}, {'$inc': {'sold': 1}})
        # Cashback + referral reward are credited on every successful bot delivery.
        try:
            await grant_cashback(order['user_id'], float(order['amount']))
            await grant_referral_reward(order['user_id'], float(order['amount']))
        except Exception:
            pass  # rewards must never block delivery
    else:
        # The reserved item is intentionally kept associated with this order.
        # The order remains paid_processing and can be reconciled manually rather than double-delivered.
        return None, 'Payment was received but delivery could not be finalized. Please contact support.'
    return item, None

@router.callback_query(F.data.startswith('paymethod:'))
async def choose_payment(cb: CallbackQuery):
    _, method, oid = cb.data.split(':',2)
    order = await orders.find_one({'order_id':oid,'user_id':cb.from_user.id})
    if not order: return await cb.answer('Order not found.', show_alert=True)
    if order.get('status') not in ('payment_method','awaiting_payment'): return await cb.answer('Order is no longer active.', show_alert=True)
    cfg = await get_payment_settings()
    if method == 'wallet':
        return await cb.answer('Wallet checkout is disabled for in-Telegram digital goods. Pay with Telegram Stars.', show_alert=True)
    if method == 'paytm':
        if not cfg.get('paytm_enabled', True): return await cb.answer('Paytm payment is disabled.', show_alert=True)
        await orders.update_one({'order_id':oid},{'$set':{'status':'awaiting_payment','payment_provider':'paytm_qr'}})
        try:
            qr = await generate_paytm_qr(amount=float(order['amount']), order_id=oid)
        except Exception as e:
            await orders.update_one({'order_id':oid,'status':'awaiting_payment'},{'$set':{'status':'payment_method'},'$unset':{'payment_provider':''}})
            return await cb.answer(f'Paytm QR error: {str(e)[:120]}', show_alert=True)
        try:
            await cb.message.answer_photo(qr['qr_url'], caption=f"📱 <b>Paytm UPI QR</b>\n\n🛍 {escape(order['product_name'])}\n💵 ₹{float(order['amount']):.2f}\n🧾 <code>{oid}</code>\n\nPay the QR, then tap Verify.", reply_markup=payment_verify_kb(oid), parse_mode='HTML')
        except Exception:
            await safe_edit_message(cb.message, f"📱 <b>Pay with Paytm / UPI</b>\n\n💵 ₹{float(order['amount']):.2f}\n🧾 <code>{oid}</code>\n\nQR load nahi hua — UPI app me ye ID use karke pay kijiye, phir Verify dabaiye.", reply_markup=payment_verify_kb(oid), parse_mode='HTML')
        return await cb.answer()
    if method == 'usdt':
        if not cfg.get('usdt_enabled', False): return await cb.answer('USDT payment is disabled.', show_alert=True)
        addr = str(cfg.get('usdt_address') or '').strip()
        if not addr: return await cb.answer('USDT address is not configured by the store.', show_alert=True)
        usdt_amt = usdt_amount_from_inr(float(order['amount']), float(cfg.get('usdt_inr_rate',90)))
        network = str(cfg.get('usdt_network','TRC20'))
        await orders.update_one({'order_id':oid},{'$set':{'status':'awaiting_usdt','payment_provider':'usdt_manual','usdt_amount':usdt_amt,'usdt_network':network}})
        await safe_edit_message(cb.message, f"🪙 <b>Pay with USDT</b>\n\n💵 Amount: <b>{usdt_amt:.4f} USDT</b>\n🔗 Network: <b>{network}</b>\n📍 Address:\n<code>{escape(addr)}</code>\n\n🧾 Order: <code>{oid}</code>\n\nExact amount bhejein, phir \"I Paid\" daba kar TxID submit karein — admin verification ke baad delivery hogi.", reply_markup=usdt_paid_kb(oid), parse_mode='HTML')
        return await cb.answer()
    if method != 'stars':
        return await cb.answer('For digital goods sold inside Telegram, payment is handled with Telegram Stars.', show_alert=True)
    if not cfg.get('stars_enabled',True): return await cb.answer('Telegram Stars is disabled.', show_alert=True)
    stars = stars_amount_from_inr(float(order['amount']), float(cfg.get('star_inr_rate',1)))
    await orders.update_one({'order_id':oid},{'$set':{'status':'awaiting_payment','payment_provider':'telegram_stars','stars_amount':stars}})
    try:
        await cb.message.answer_invoice(title=order['product_name'][:32], description=f"Digital product — Order {oid}", payload=oid, provider_token='', currency='XTR', prices=[LabeledPrice(label=order['product_name'][:32], amount=stars)])
    except Exception as e:
        await orders.update_one({'order_id': oid, 'status': 'awaiting_payment'}, {'$set': {'status': 'payment_method'}, '$unset': {'payment_provider': '', 'stars_amount': ''}})
        return await cb.answer(f'Stars invoice error: {str(e)[:120]}', show_alert=True)
    await cb.answer('Stars invoice sent.')

@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    order = await orders.find_one({'order_id': query.invoice_payload, 'user_id': query.from_user.id})
    if not order or order.get('status') != 'awaiting_payment' or order.get('payment_provider') != 'telegram_stars':
        return await query.answer(ok=False, error_message='This invoice is no longer active.')
    if order.get('type') != 'wallet_topup' and await stock_count(order.get('product_id')) <= 0:
        return await query.answer(ok=False, error_message='Sorry, this product is out of stock.')
    await query.answer(ok=True)

@router.message(F.successful_payment)
async def successful_stars(message: Message):
    payment = message.successful_payment
    oid = payment.invoice_payload
    order = await orders.find_one({'order_id': oid, 'user_id': message.from_user.id})
    if not order:
        return await message.answer('Payment received but order was not found. Contact support.')
    await orders.update_one({'order_id': oid}, {'$set': {'telegram_payment_charge_id': payment.telegram_payment_charge_id, 'provider_payment_charge_id': payment.provider_payment_charge_id, 'paid_at': datetime.now(timezone.utc)}})
    if order.get('type') == 'wallet_topup':
        updated = await orders.update_one({'order_id': oid, 'status': 'awaiting_payment'}, {'$set': {'status': 'credited', 'payment_provider': 'telegram_stars'}})
        if updated.modified_count == 1:
            amount = float(order['amount'])
            await users.update_one({'telegram_id': message.from_user.id}, {'$inc': {'balance': amount}})
            await create_wallet_tx(message.from_user.id, amount, 'deposit', 'Telegram Stars wallet top-up', oid)
            return await message.answer(f'⭐ <b>Stars Payment Successful</b>\n\n₹{amount:.2f} has been added to your wallet.\nOrder: <code>{oid}</code>', reply_markup=back_kb('wallet'), parse_mode='HTML')
        return await message.answer('This top-up was already processed.')
    item, err = await deliver_order(message, order, 'telegram_stars')
    if err:
        return await message.answer(f'⚠️ {escape(err)}\nOrder: <code>{oid}</code>', parse_mode='HTML')
    await message.answer(f"⭐ <b>Stars Payment Successful</b>\n\n🧾 <code>{oid}</code>\n🛍 {escape(order['product_name'])}\n\n📦 <b>Your item:</b>\n<code>{escape(str(item['value']))}</code>", reply_markup=back_kb(), parse_mode='HTML')

@router.callback_query(F.data.startswith('payverify:'))
async def verify_paytm(cb: CallbackQuery):
    oid = cb.data.split(':',1)[1]; order = await orders.find_one({'order_id':oid,'user_id':cb.from_user.id})
    if not order: return await cb.answer('Order not found.', show_alert=True)
    if order.get('status') == 'delivered': return await cb.answer('Already delivered.')
    if order.get('type') == 'wallet_topup':
        # Stale button from an old message: top-up orders are credited by topupverify:, not here.
        return await cb.answer('Yeh top-up order hai — upar wale top-up message ke Verify button se verify karein.', show_alert=True)
    try: result = await verify_paytm_payment(order_id=oid)
    except Exception as e: return await cb.answer(f'Paytm verification unavailable: {str(e)[:100]}', show_alert=True)
    if result.get('status') != 'success' or not result.get('verified'): return await cb.answer('❌ Payment not verified yet.', show_alert=True)
    item, err = await deliver_order(cb, order, 'paytm_qr')
    if err: return await cb.answer(err, show_alert=True)
    try: await cb.message.delete()
    except Exception: pass
    await cb.message.answer(f"✅ <b>Payment Verified</b>\n\n🧾 <code>{oid}</code>\n🛍 {escape(order['product_name'])}\n💵 ₹{float(order['amount']):.2f}\n\n📦 <b>Your item:</b>\n<code>{escape(str(item['value']))}</code>", reply_markup=back_kb(), parse_mode='HTML'); await cb.answer('Payment verified!')

@router.callback_query(F.data.startswith('usdtpaid:'))
async def usdt_paid(cb: CallbackQuery, state: FSMContext):
    oid = cb.data.split(':',1)[1]
    order = await orders.find_one({'order_id':oid,'user_id':cb.from_user.id,'status':'awaiting_usdt'})
    if not order: return await cb.answer('Order not found or already submitted.', show_alert=True)
    await state.update_data(usdt_order_id=oid); await state.set_state(USDTProof.txid)
    await safe_edit_message(cb.message, '🧾 <b>USDT Transaction ID</b>\n\nSend the transaction hash / TxID of your payment. This is required for admin verification.\n\nSend /cancel to abort.', parse_mode='HTML'); await cb.answer()

@router.message(USDTProof.txid)
async def usdt_proof(message: Message, state: FSMContext):
    if (message.text or '').strip().lower() == '/cancel': await state.clear(); return await message.answer('❌ Submission cancelled.', reply_markup=back_kb())
    d=await state.get_data(); oid=d.get('usdt_order_id'); proof=(message.text or '').strip()
    if len(proof)<8: return await message.answer('❌ Send a valid transaction hash / TxID.')
    order=await orders.find_one({'order_id':oid,'user_id':message.from_user.id,'status':'awaiting_usdt'})
    if not order: await state.clear(); return await message.answer('❌ Order is no longer active.')
    await orders.update_one({'order_id':oid},{'$set':{'status':'pending_usdt','usdt_txid':proof,'submitted_at':datetime.now(timezone.utc)}})
    await state.clear(); await message.answer(f'⏳ <b>USDT Payment Submitted</b>\n\nOrder: <code>{oid}</code>\nTxID: <code>{escape(proof)}</code>\n\nWaiting for admin verification.', reply_markup=back_kb(), parse_mode='HTML')

@router.callback_query(F.data.startswith('paycancel:'))
async def paycancel(cb: CallbackQuery):
    oid = cb.data.split(':',1)[1]
    await orders.update_one({'order_id':oid,'user_id':cb.from_user.id,'status':{'$in':['payment_method','awaiting_payment','awaiting_usdt','pending_usdt'] }},{'$set':{'status':'cancelled'}})
    await safe_edit_message(cb.message, '❌ Payment cancelled.', reply_markup=back_kb(), parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'wallet')
async def wallet(cb: CallbackQuery):
    u = await users.find_one({'telegram_id':cb.from_user.id}) or {}; cfg = await get_payment_settings(); bal=float(u.get('balance',0)); cur=u.get('currency','INR')
    shown=money(bal,cur,cfg)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='➕ Add Balance',callback_data='wallet:add')],[InlineKeyboardButton(text='📜 Wallet History',callback_data='wallet:history')],[InlineKeyboardButton(text='💱 Change Currency',callback_data='currency')],[InlineKeyboardButton(text='⬅️ Back',callback_data='home')]])
    await safe_edit_message(cb.message, f"💳 <b>Wallet</b>\n\nBalance: <b>₹{bal:.2f} INR</b>\nDisplay: <b>{shown}</b>\n\nWallet balance is maintained in INR.",reply_markup=kb,parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'wallet:add')
async def wallet_add(cb: CallbackQuery, state: FSMContext):
    cfg=await get_payment_settings()
    stars_on=bool(cfg.get('stars_enabled',True)); paytm_on=bool(cfg.get('paytm_enabled',True))
    if not stars_on and not paytm_on:
        return await cb.answer('No wallet top-up method is enabled.', show_alert=True)
    if not paytm_on:  # sirf Stars available — seedha amount pooch lo
        await state.update_data(method='stars'); await state.set_state(WalletTopup.amount)
        await safe_edit_message(cb.message, '➕ <b>Add Wallet Balance</b>\n\nSend the INR amount you want to add (₹1–₹50,000). Telegram Stars will be used for the invoice.', parse_mode='HTML'); return await cb.answer()
    await state.set_state(WalletTopup.method)
    await safe_edit_message(cb.message, '➕ <b>Add Wallet Balance</b>\n\nChoose a top-up method (₹1–₹50,000):', reply_markup=wallet_method_kb(paytm_on), parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data.startswith('wmethod:'))
async def wallet_method(cb: CallbackQuery,state: FSMContext):
    if await state.get_state() != WalletTopup.method.state: return await cb.answer('Open the wallet menu again.', show_alert=True)
    method=cb.data.split(':',1)[1]; cfg=await get_payment_settings()
    if method=='paytm' and not cfg.get('paytm_enabled',True): return await cb.answer('Paytm top-up is disabled.', show_alert=True)
    if method=='stars' and not cfg.get('stars_enabled',True): return await cb.answer('Stars top-up is disabled.', show_alert=True)
    await state.update_data(method=method); await state.set_state(WalletTopup.amount)
    label='Paytm UPI QR' if method=='paytm' else 'Telegram Stars'
    await safe_edit_message(cb.message, f'💰 <b>Wallet Top-up — {label}</b>\n\nSend the INR amount to add (₹1–₹50,000).', parse_mode='HTML'); await cb.answer()

@router.message(WalletTopup.amount)
async def wallet_amount(message: Message,state:FSMContext):
    try:
        amount=round(float((message.text or '').replace(',','').strip()),2)
        if amount<1 or amount>50000: raise ValueError
    except Exception: return await message.answer('❌ Enter an amount between ₹1 and ₹50,000.')
    d=await state.get_data(); method=d.get('method'); oid='TOPUP-'+uuid.uuid4().hex[:10].upper()
    await orders.insert_one({'order_id':oid,'user_id':message.from_user.id,'amount':amount,'product_name':'Wallet Top-up','status':'awaiting_payment','type':'wallet_topup','payment_provider':method,'created_at':datetime.now(timezone.utc)})
    cfg=await get_payment_settings()
    if method=='paytm':
        try: qr=await generate_paytm_qr(amount=amount,order_id=oid)
        except Exception as e:
            await orders.delete_one({'order_id':oid}); await state.clear(); return await message.answer(f'❌ Paytm QR error: {str(e)[:120]}',reply_markup=back_kb('wallet'))
        await state.clear(); return await message.answer_photo(qr['qr_url'],caption=f"📱 <b>Wallet Top-up</b>\n\n₹{amount:.2f}\nOrder: <code>{oid}</code>\n\nPay and verify.",reply_markup=topup_verify_kb(oid),parse_mode='HTML')
    if method=='stars':
        stars=stars_amount_from_inr(amount,float(cfg.get('star_inr_rate',1))); await orders.update_one({'order_id':oid},{'$set':{'stars_amount':stars}}); await state.clear()
        return await message.answer_invoice(title='Wallet Top-up',description=f'Add ₹{amount:.2f} to your Teleshop wallet',payload=oid,provider_token='',currency='XTR',prices=[LabeledPrice(label='Wallet balance',amount=stars)])
    await state.clear(); await orders.delete_one({'order_id':oid}); await message.answer('Unsupported payment method.',reply_markup=back_kb('wallet'))

@router.callback_query(F.data.startswith('topupverify:'))
async def topup_verify(cb:CallbackQuery):
    oid=cb.data.split(':',1)[1]; order=await orders.find_one({'order_id':oid,'user_id':cb.from_user.id,'type':'wallet_topup'})
    if not order: return await cb.answer('Top-up not found.',show_alert=True)
    try:r=await verify_paytm_payment(order_id=oid)
    except Exception as e:return await cb.answer(f'Verification unavailable: {str(e)[:100]}',show_alert=True)
    if r.get('status')!='success' or not r.get('verified'):return await cb.answer('❌ Payment not verified yet.',show_alert=True)
    updated=await orders.update_one({'order_id':oid,'status':'awaiting_payment'},{'$set':{'status':'credited','verified_at':datetime.now(timezone.utc)}})
    if updated.modified_count!=1:return await cb.answer('Already processed.',show_alert=True)
    amount=float(order['amount']); await users.update_one({'telegram_id':cb.from_user.id},{'$inc':{'balance':amount}}); await create_wallet_tx(cb.from_user.id,amount,'deposit','Paytm wallet top-up',oid)
    try: await cb.message.delete()
    except Exception: pass
    await cb.message.answer(f'✅ <b>Wallet Credited</b>\n\n₹{amount:.2f} added.\nOrder: <code>{oid}</code>',reply_markup=back_kb('wallet'),parse_mode='HTML'); await cb.answer('Credited!')


@router.callback_query(F.data.startswith('topupcancel:'))
async def topup_cancel(cb: CallbackQuery):
    oid = cb.data.split(':', 1)[1]
    result = await orders.update_one(
        {'order_id': oid, 'user_id': cb.from_user.id, 'type': 'wallet_topup', 'status': 'awaiting_payment'},
        {'$set': {'status': 'cancelled', 'cancelled_at': datetime.now(timezone.utc)}}
    )
    if result.modified_count != 1:
        return await cb.answer('This top-up is no longer active.', show_alert=True)
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.message.answer(
        '❌ <b>Wallet top-up cancelled.</b>',
        reply_markup=back_kb('wallet'),
        parse_mode='HTML'
    )
    await cb.answer('Top-up cancelled.')

@router.callback_query(F.data == 'wallet:history')
async def wallet_history(cb:CallbackQuery):
    from app.database.mongo import wallet_transactions
    docs=await wallet_transactions.find({'user_id':cb.from_user.id}).sort('created_at',-1).limit(20).to_list(20)
    text='📜 <b>Wallet History</b>\n\n'+('\n'.join(f"• {'+' if float(d.get('amount',0))>=0 else ''}₹{float(d.get('amount',0)):.2f} — {escape(str(d.get('type','transaction')))}" for d in docs) if docs else 'No transactions yet.')
    await safe_edit_message(cb.message, text,reply_markup=back_kb('wallet'),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'currency')
async def currency(cb:CallbackQuery):
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🇮🇳 INR',callback_data='setcurrency:INR'),InlineKeyboardButton(text='🪙 USDT',callback_data='setcurrency:USDT')],[InlineKeyboardButton(text='⭐ Telegram Stars',callback_data='setcurrency:XTR')],[InlineKeyboardButton(text='⬅️ Back',callback_data='home')]])
    await safe_edit_message(cb.message, '💱 <b>Currency / Payment Preference</b>\n\nChoose how prices should be displayed. You can change this anytime.',reply_markup=kb,parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data.startswith('setcurrency:'))
async def setcurrency(cb:CallbackQuery):
    cur=cb.data.split(':',1)[1]
    labels={'INR':'🇮🇳 INR','USDT':'🪙 USDT','XTR':'⭐ Telegram Stars'}
    if cur not in labels: return await cb.answer('Unsupported currency.', show_alert=True)
    await users.update_one({'telegram_id':cb.from_user.id},{'$set':{'currency':cur,'updated_at':datetime.now(timezone.utc)}})
    label=labels[cur]
    await safe_edit_message(cb.message, f'✅ Currency preference changed to <b>{label}</b>.',reply_markup=back_kb('home'),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'profile')
async def profile(cb:CallbackQuery):
    u=await users.find_one({'telegram_id':cb.from_user.id}) or {}; total=await orders.count_documents({'user_id':cb.from_user.id,'status':'delivered'})
    await safe_edit_message(cb.message, f"👤 <b>Profile</b>\n\n🆔 ID: <code>{cb.from_user.id}</code>\n👤 Username: @{escape(cb.from_user.username or '—')}\n💳 Wallet: ₹{float(u.get('balance',0)):.2f}\n💱 Currency: {escape(str(u.get('currency','INR')))}\n🧾 Completed Orders: {total}",reply_markup=back_kb(),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'history')
async def history(cb:CallbackQuery):
    docs=await orders.find({'user_id':cb.from_user.id}).sort('created_at',-1).limit(15).to_list(15)
    text='📋 <b>Purchase History</b>\n\n'+('\n'.join(f"• <code>{d['order_id']}</code> — {escape(str(d['product_name']))} — ₹{float(d.get('amount',0)):.2f} — {escape(str(d.get('status','')))}" for d in docs) if docs else 'No orders yet.')
    await safe_edit_message(cb.message, text,reply_markup=back_kb(),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'referral')
async def referral(cb:CallbackQuery):
    count=await referrals.count_documents({'referrer_id':cb.from_user.id})
    rewarded=await referrals.count_documents({'referrer_id':cb.from_user.id,'rewarded':True})
    me=await cb.bot.get_me(); link=f'https://t.me/{me.username}?start=ref_{cb.from_user.id}'
    await safe_edit_message(cb.message, f"👥 <b>Referral</b>\n\nShare:\n<code>{link}</code>\n\n👥 Referrals: {count}\n🎁 Rewards credited: {rewarded}\n\nReward wallet me automatically credit hota hai jab referred user ka pehla order deliver hota hai (admin ke set kiye reward amount par).",reply_markup=back_kb(),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'cashback')
async def cashback_page(cb:CallbackQuery):
    total=await cashback.aggregate([{'$match':{'user_id':cb.from_user.id}},{'$group':{'_id':None,'x':{'$sum':'$amount'}}}]).to_list(1); amount=float(total[0]['x']) if total else 0
    await safe_edit_message(cb.message, f'🎁 <b>Cashback</b>\n\nTotal earned: ₹{amount:.2f}\n\nCashback is credited automatically when enabled by the store.',reply_markup=back_kb(),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'api')
async def api_page(cb:CallbackQuery):
    active=await api_keys.find_one({'user_id':cb.from_user.id,'active':True}); base=settings.public_base_url.rstrip('/') if settings.public_base_url else ''
    from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton
    rows=[[InlineKeyboardButton(text='🔑 Generate / Rotate API Key',callback_data='genkey')]]
    if base: rows.append([InlineKeyboardButton(text='📘 API Integration Guide',url=base+'/api/v1')])
    rows.append([InlineKeyboardButton(text='⬅️ Back',callback_data='home')])
    await safe_edit_message(cb.message, f"🔌 <b>Reseller API</b>\n\nProtected API for reselling legitimate digital products.\n\nActive key: {'Yes' if active else 'No'}\nBase URL: <code>{escape(base or 'Not configured')}</code>\n\nUse the generated key in <code>X-Reseller-Key</code>.",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'genkey')
async def genkey(cb:CallbackQuery):
    key=await create_api_key(cb.from_user.id); await safe_edit_message(cb.message, f'🔑 <b>New API Key</b>\n\n<code>{key}</code>\n\n⚠️ Copy it now. It will not be shown again.',reply_markup=back_kb('api'),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'support')
async def support(cb:CallbackQuery):
    from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton
    rows=[]
    if settings.support_username: rows.append([InlineKeyboardButton(text='💬 Contact Support',url='https://t.me/'+settings.support_username.lstrip('@'))])
    rows.append([InlineKeyboardButton(text='⬅️ Back',callback_data='home')])
    await safe_edit_message(cb.message, '💬 <b>Support</b>\n\nNeed help with an order or payment? Contact support.',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'channel')
async def channel(cb:CallbackQuery):
    from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton
    rows=[]
    if settings.channel_url: rows.append([InlineKeyboardButton(text='📢 Open Channel',url=settings.channel_url)])
    rows.append([InlineKeyboardButton(text='⬅️ Back',callback_data='home')])
    await safe_edit_message(cb.message, '📢 <b>Official Channel</b>',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'language')
async def language(cb:CallbackQuery):
    from aiogram.types import InlineKeyboardMarkup,InlineKeyboardButton
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🇬🇧 English',callback_data='lang:en'),InlineKeyboardButton(text='🇮🇳 हिन्दी',callback_data='lang:hi')],[InlineKeyboardButton(text='🇸🇦 العربية',callback_data='lang:ar'),InlineKeyboardButton(text='🇨🇳 中文',callback_data='lang:zh')],[InlineKeyboardButton(text='⬅️ Back',callback_data='home')]])
    await safe_edit_message(cb.message, '🌐 <b>Language</b>\n\nChoose your language.',reply_markup=kb,parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data.startswith('lang:'))
async def lang_set(cb:CallbackQuery):
    lang=cb.data.split(':',1)[1]; await users.update_one({'telegram_id':cb.from_user.id},{'$set':{'language':lang}}); await safe_edit_message(cb.message, '✅ Language preference saved.',reply_markup=back_kb(),parse_mode='HTML'); await cb.answer()

@router.callback_query(F.data == 'game')
async def game(cb:CallbackQuery):
    result=random.choice(['🔥 You win!','🤖 Bot wins!','🤝 Draw!'])
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🎮 Play Again',callback_data='game')],[InlineKeyboardButton(text='⬅️ Back',callback_data='home')]])
    try:
        await cb.message.edit_text(f'🎮 <b>Who Wins?</b>\n\n{result}\n\nFree game — no betting or prizes.',reply_markup=kb,parse_mode='HTML')
    except Exception:
        # Same text twice (same random result) raises TelegramBadRequest — resend instead.
        await cb.message.answer(f'🎮 <b>Who Wins?</b>\n\n{result}\n\nFree game — no betting or prizes.',reply_markup=kb,parse_mode='HTML')
    await cb.answer()

@router.callback_query(F.data.startswith('notify:'))
async def notify(cb:CallbackQuery): await cb.answer('You will be notified when stock is available.',show_alert=True)



@router.callback_query(~(F.data.startswith('adm:') | F.data.startswith('paycfg:') | F.data.startswith('deliv:')))
async def unknown_user_callback(cb: CallbackQuery):
    # Admin-panel callbacks (adm:/paycfg:/deliv:) are handled by the admin router;
    # they must NOT be swallowed here.
    await cb.answer('This button is no longer active. Please open the menu again.', show_alert=True)
