from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Buy", callback_data="buy"), InlineKeyboardButton(text="🎁 Cashback", callback_data="cashback")],
        [InlineKeyboardButton(text="💳 Wallet", callback_data="wallet"), InlineKeyboardButton(text="💱 Currency", callback_data="currency")],
        [InlineKeyboardButton(text="👤 Profile", callback_data="profile"), InlineKeyboardButton(text="📋 Purchase History", callback_data="history")],
        [InlineKeyboardButton(text="💬 Support", callback_data="support"), InlineKeyboardButton(text="👥 Referral", callback_data="referral")],
        [InlineKeyboardButton(text="🔌 API", callback_data="api"), InlineKeyboardButton(text="📢 Channel", callback_data="channel")],
        [InlineKeyboardButton(text="🎮 Free Game", callback_data="game"), InlineKeyboardButton(text="🌐 Language", callback_data="language")],
    ])

def back_kb(target="home"):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data=target)]])
