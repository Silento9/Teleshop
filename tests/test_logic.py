"""Real logic tests (no MongoDB needed — collections are faked).
Covers: API-key regeneration deletes old keys, referral reward once-only,
cashback percent math, config gating (OFF = no credit)."""
import asyncio, hashlib

from app.services import store


class FakeCol:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def find_one(self, q, *a, **k):
        for r in self.rows:
            if all(r.get(kk) == vv for kk, vv in q.items()):
                return dict(r)
        return None

    async def update_one(self, q, upd, *a, **k):
        for r in self.rows:
            if all(r.get(kk) == vv for kk, vv in q.items() if kk != '_id'):
                if '$inc' in upd:
                    for kk, vv in upd['$inc'].items():
                        r[kk] = r.get(kk, 0) + vv
                if '$set' in upd:
                    r.update(upd['$set'])
                return type('R', (), {'modified_count': 1})()
        return type('R', (), {'modified_count': 0})()

    async def delete_many(self, q):
        uid = q.get('user_id')
        before = len(self.rows)
        self.rows = [r for r in self.rows if r['user_id'] != uid]
        return type('R', (), {'deleted_count': before - len(self.rows)})()

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        return type('R', (), {'inserted_id': 'x'})()

    async def count_documents(self, q):
        uid = q.get('user_id')
        act = q.get('active')
        return len([r for r in self.rows
                    if (uid is None or r['user_id'] == uid)
                    and (act is None or r.get('active') == act)])


def make_store():
    users_f = FakeCol([{'telegram_id': 111, 'balance': 0.0}, {'telegram_id': 222, 'balance': 0.0}])
    ref_f = FakeCol([{'_id': 'r1', 'referrer_id': 111, 'referred_id': 222, 'rewarded': False}])
    cash_f = FakeCol()
    wtx_f = FakeCol()
    cfg_f = FakeCol([{'_id': 'payment', 'referral_reward_inr': 50.0, 'cashback_percent': 5.0}])
    store.users = users_f; store.referrals = ref_f; store.cashback = cash_f
    store.wallet_transactions = wtx_f; store.api_keys = FakeCol(); store.settings_col = cfg_f
    return users_f, ref_f, cash_f, wtx_f, cfg_f


def test_api_key_regeneration_deletes_old_key():
    users_f, *_ = make_store()
    async def run():
        k1 = await store.create_api_key(111)
        k2 = await store.create_api_key(111)
        assert k1 != k2
        rows = [r for r in store.api_keys.rows if r['user_id'] == 111]
        assert len(rows) == 1, f'expected 1 row, got {len(rows)}'
        old_h = hashlib.sha256(k1.encode()).hexdigest()
        assert await store.api_keys.find_one({'key_hash': old_h, 'active': True}) is None
        new_h = hashlib.sha256(k2.encode()).hexdigest()
        assert await store.api_keys.find_one({'key_hash': new_h, 'active': True}) is not None
    asyncio.run(run())


def test_cashback_percent_and_wallet_credit():
    users_f, _, cash_f, wtx_f, _ = make_store()
    async def run():
        await store.grant_cashback(222, 900)  # 5% of 900 = 45
        assert any(r['user_id'] == 222 and r['amount'] == 45.0 for r in cash_f.rows)
        assert any(t['type'] == 'cashback' for t in wtx_f.rows)
        u = [r for r in users_f.rows if r['telegram_id'] == 222][0]
        assert u['balance'] == 45.0
    asyncio.run(run())


def test_referral_reward_paid_exactly_once():
    users_f, ref_f, _, _, _ = make_store()
    async def run():
        await store.grant_referral_reward(222, 900)
        await store.grant_referral_reward(222, 900)  # retry must not double-pay
        assert ref_f.rows[0]['rewarded'] is True
        u = [r for r in users_f.rows if r['telegram_id'] == 111][0]
        assert u['balance'] == 50.0
    asyncio.run(run())


def test_rewards_disabled_when_config_zero():
    users_f, ref_f, cash_f, _, cfg_f = make_store()
    async def run():
        cfg_f.rows[0]['referral_reward_inr'] = 0
        cfg_f.rows[0]['cashback_percent'] = 0
        await store.grant_referral_reward(222, 900)
        await store.grant_cashback(222, 900)
        assert ref_f.rows[0]['rewarded'] is False
        assert cash_f.rows == []
    asyncio.run(run())


def test_no_referral_record_is_safe():
    make_store()
    async def run():
        await store.grant_referral_reward(777, 100)  # must not raise
    asyncio.run(run())
