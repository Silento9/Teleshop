from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def product_list_kb(items, counts, currency="INR", rate=90.0, star_rate=1.0):
    rows=[]
    for p in items:
        stock = counts.get(str(p['_id']), 0)
        price = float(p.get('price', 0))
        if currency == 'USDT':
            label = f"${price/rate:.4f}"
        elif currency == 'XTR':
            label = f"⭐ {max(1, int(round(price/star_rate)))}"
        else:
            label = f"₹{price:.2f}"
        rows.append([InlineKeyboardButton(text=f"🛍 {p['name']} • {label} • {'📦 '+str(stock) if stock else '❌ Out'}", callback_data=f"prod:{p['_id']}")])
    rows += [[InlineKeyboardButton(text='🔄 Refresh', callback_data='buy')],[InlineKeyboardButton(text='⬅️ Back', callback_data='home')]]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def product_detail_kb(pid, available, currency='INR'):
    rows=[]
    if available: rows.append([InlineKeyboardButton(text='🛒 Buy Now', callback_data=f'buyprod:{pid}')])
    rows += [[InlineKeyboardButton(text='🔔 Notify Me', callback_data=f'notify:{pid}')],[InlineKeyboardButton(text='⬅️ Back to Store', callback_data='buy')]]
    return InlineKeyboardMarkup(inline_keyboard=rows)
