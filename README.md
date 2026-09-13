# Teleshop — Advanced Railway Telegram Digital Store

Railway-ready Telegram digital-goods store using **aiogram 3 + FastAPI + MongoDB**.

## Included

- Direct product store — no category screen
- Product logo/photo shown above the inline buttons
- Product add/edit/delete/enable/disable
- Multi-line inventory / stock management
- Atomic stock allocation to prevent double-selling
- INR base pricing
- User-selectable display/payment preference: **INR, USDT, Telegram Stars**
- Telegram Stars (`XTR`) native invoice payment for in-Telegram digital-goods checkout
- Paytm configuration retained for an authorized external checkout/web flow (not exposed as an in-Telegram digital-goods payment button)
- USDT configuration retained for an authorized external checkout/web flow (not exposed as an in-Telegram digital-goods payment button)
- Wallet in INR with Telegram Stars top-up
- Wallet history, purchase history, profile
- Referral, cashback statistics, free game, language preference
- Graphical inline admin panel
- Admin payment configuration
- USDT pending-order approval queue
- Reseller API with API-key authentication and integration guide
- FastAPI Swagger at `/docs`

## Important payment notes

### Paytm
Configure from **Admin → Payments** if you also operate an authorized external website checkout. The bot does not expose Paytm as an in-Telegram digital-goods payment method.

### Telegram Stars
Telegram Stars uses Telegram's native `XTR` invoice system. No payment-provider token is required for Stars. The admin controls the INR↔Stars conversion rate in **Admin → Payments**.

### USDT
USDT rate/address/network settings are available for an authorized external checkout. The Telegram digital-goods checkout does not expose USDT as an in-Telegram payment method.

## Currency behavior

Products are stored with an INR base price. Users can switch from **💱 Currency**:
- INR — display currency
- USDT — display-only conversion using the admin-configured `1 USDT = ₹X` rate
- Telegram Stars — checkout currency using the admin-configured `1 Star = ₹X` rate

Wallet balance itself remains stored in INR to keep accounting consistent.

## Product logos

Telegram Bot API inline keyboard buttons do **not** support arbitrary image thumbnails. Therefore the bot stores the Telegram `file_id` and shows the product logo/photo immediately above the inline buttons on the product page. The buttons themselves use emoji + product name.

## Setup

1. Create the bot with BotFather.
2. Create MongoDB / MongoDB Atlas database.
3. Copy `.env.example` to `.env`.
4. Set `BOT_TOKEN`, `MONGODB_URI`, `DATABASE_NAME`, `ADMIN_IDS`.
5. Set `PUBLIC_BASE_URL` to the Railway domain.
6. Install dependencies with `pip install -r requirements.txt`.
7. Run with Uvicorn: `uvicorn app.main:app --host 0.0.0.0 --port 8080`.

## Railway

Railway automatically supplies `PORT`; the included Dockerfile uses it.

Required variables:
- `BOT_TOKEN`
- `MONGODB_URI`
- `DATABASE_NAME`
- `ADMIN_IDS`
- `CHANNEL_URL`
- `SUPPORT_USERNAME`
- `PUBLIC_BASE_URL`

## Admin

Send `/admin` from an ID listed in `ADMIN_IDS`.

Use the inline admin panel for products, stock, orders, payments, users, statistics, cashback, referrals, API and broadcast.

Stock can also be added with:

`/stock PRODUCT_ID`

Then send one legitimate digital item per line.

## API

The API requires:

`X-Reseller-Key: sb_...`

Endpoints:
- `GET /api/v1` — integration guide
- `GET /api/v1/products`
- `GET /api/v1/products/{product_id}`
- `POST /api/v1/orders?product_id=...`
- `GET /api/v1/orders/{order_id}`
- `/docs` — Swagger/OpenAPI

API orders use the reseller's INR wallet balance.

## Safety

Use only legitimate digital goods that you are authorized to sell. Do not use the project for stolen/compromised accounts, credentials, OTPs, session strings, payment-card data, phishing, or unauthorized access.

## Telegram digital-goods payment compliance

Telegram's current documentation says digital goods/services sold inside Telegram apps must use Telegram Stars (`XTR`). The bot therefore keeps Paytm/USDT out of the in-Telegram digital-goods purchase buttons. Paytm/USDT can be used only through a separately operated, authorized external checkout. citeturn0search0turn0search7
