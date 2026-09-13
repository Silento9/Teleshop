from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def payment_methods_kb(oid, stars=True, wallet=False, paytm=False, usdt=False):
    rows=[]
    # Render every gateway that is enabled in payment settings (admin panel toggles).
    if stars: rows.append([InlineKeyboardButton(text='⭐ Pay with Telegram Stars', callback_data=f'paymethod:stars:{oid}')])
    if paytm: rows.append([InlineKeyboardButton(text='📱 Pay with Paytm UPI QR', callback_data=f'paymethod:paytm:{oid}')])
    if usdt: rows.append([InlineKeyboardButton(text='🪙 Pay with USDT (TRC20)', callback_data=f'paymethod:usdt:{oid}')])
    if wallet: rows.append([InlineKeyboardButton(text='💳 Pay from Wallet', callback_data=f'paymethod:wallet:{oid}')])
    rows.append([InlineKeyboardButton(text='❌ Cancel', callback_data=f'paycancel:{oid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def payment_verify_kb(oid):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔍 Verify Paytm', callback_data=f'payverify:{oid}')],[InlineKeyboardButton(text='❌ Cancel', callback_data=f'paycancel:{oid}')]])

def usdt_paid_kb(oid):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ I Paid — Submit for Review', callback_data=f'usdtpaid:{oid}')],[InlineKeyboardButton(text='❌ Cancel', callback_data=f'paycancel:{oid}')]])

def wallet_method_kb(paytm=False):
    rows=[[InlineKeyboardButton(text='⭐ Add via Telegram Stars', callback_data='wmethod:stars')]]
    if paytm: rows.append([InlineKeyboardButton(text='📱 Add via Paytm UPI QR', callback_data='wmethod:paytm')])
    rows.append([InlineKeyboardButton(text='❌ Cancel', callback_data='wallet')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def topup_verify_kb(oid):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='🔍 Verify Paytm Payment', callback_data=f'topupverify:{oid}')],[InlineKeyboardButton(text='❌ Cancel', callback_data=f'topupcancel:{oid}')]])
