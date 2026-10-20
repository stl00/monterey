import base64
import hashlib
import hmac
import json
import time

import pytest

from portfolio.aggregate import Snapshot, add_result, collect, render
from portfolio.connectors import futu, okx, schwab
from portfolio.connectors.base import Connector
from portfolio.fx import FxRates
from portfolio.models import FetchResult

FX = FxRates("HKD", {"USD": 0.128, "CNY": 0.92})  # 1 HKD = 0.128 USD


# --- OKX -------------------------------------------------------------------

def test_okx_sign_matches_spec():
    ts, path = "2026-10-20T16:00:00.000Z", "/api/v5/account/balance?ccy=BTC"
    expected = base64.b64encode(
        hmac.new(b"secret", f"{ts}GET{path}".encode(), hashlib.sha256).digest()).decode()
    assert okx.sign("secret", ts, "get", path) == expected


def test_okx_timestamp_format():
    ts = okx.timestamp()
    assert len(ts) == 24 and ts.endswith("Z") and ts[19] == "."


def test_okx_parse_all_accounts():
    trading = [{"totalEq": "15000", "details": [
        {"ccy": "BTC", "eq": "0.2", "eqUsd": "14000"},
        {"ccy": "USDT", "eq": "1000", "eqUsd": "1000"},
        {"ccy": "DOGE", "eq": "0", "eqUsd": "0"},
    ]}]
    prices = okx.spot_prices_usd([{"instId": "ETH-USDT", "last": "2500"}, {"instId": "ETH-BTC", "last": "0.03"}])
    funding = okx.parse_simple_balances([{"ccy": "ETH", "bal": "2"}, {"ccy": "XYZ", "bal": "5"}], "funding", "bal", prices)
    earn = okx.parse_simple_balances([{"ccy": "USDT", "amt": "500"}], "earn", "amt", prices)
    pos = okx.parse_positions([{"instId": "BTC-USDT-SWAP", "pos": "1", "posSide": "short", "notionalUsd": "700",
                                "markPx": "70000", "instType": "SWAP", "mgnMode": "cross"}])
    r = okx.merge(okx.parse_trading(trading), funding, earn, pos)

    syms = {(h.account, h.symbol): h for h in r.holdings}
    assert ("trading", "DOGE") not in syms
    assert syms[("trading", "USDT")].asset_class == "cash"
    assert syms[("funding", "ETH")].market_value == 5000
    assert syms[("funding", "XYZ")].market_value is None
    assert syms[("trading", "BTC-USDT-SWAP")].counted is False
    assert syms[("trading", "BTC-USDT-SWAP")].quantity == -1
    assert any("XYZ" in w for w in r.warnings)

    snap = Snapshot("t", "HKD")
    add_result(snap, r, FX)
    # trading reconciles exactly, so the only warning is the missing XYZ price
    assert not any("reports" in w for w in snap.warnings)
    assert snap.total == pytest.approx((14000 + 1000 + 5000 + 500) / 0.128)


class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append((url, headers, params))
        for prefix, payload in self.routes.items():
            if prefix in url:
                return FakeResp(payload)
        raise AssertionError(url)

    def post(self, url, data=None, auth=None, timeout=None):
        self.calls.append((url, data, auth))
        return FakeResp(self.routes["token"])


def test_okx_client_headers_and_errors():
    http = FakeHttp({"/account/balance": {"code": "0", "data": []},
                     "/asset/balances": {"code": "50111", "msg": "Invalid OK-ACCESS-KEY"}})
    c = okx.OKXClient("k", "s", "p", http=http)
    assert c.get("/api/v5/account/balance") == []
    headers = http.calls[0][1]
    assert headers["OK-ACCESS-KEY"] == "k" and headers["OK-ACCESS-PASSPHRASE"] == "p"
    with pytest.raises(okx.OKXError, match="50111"):
        c.get("/api/v5/asset/balances")


# --- Schwab ----------------------------------------------------------------

SCHWAB_ACCOUNTS = [{"securitiesAccount": {
    "type": "MARGIN", "accountNumber": "12345678",
    "positions": [
        {"longQuantity": 10, "shortQuantity": 0, "averagePrice": 150, "marketValue": 2000,
         "instrument": {"assetType": "EQUITY", "symbol": "AAPL", "description": "APPLE INC"}},
        {"longQuantity": 5, "shortQuantity": 0, "averagePrice": 400, "marketValue": 2500,
         "instrument": {"assetType": "COLLECTIVE_INVESTMENT", "symbol": "VOO"}},
    ],
    "currentBalances": {"cashBalance": 500, "liquidationValue": 5000},
}}]


def test_schwab_parse():
    r = schwab.parse_accounts(SCHWAB_ACCOUNTS)
    by = {h.symbol: h for h in r.holdings}
    assert by["AAPL"].account == "…5678"
    assert by["AAPL"].cost_basis == 1500 and by["AAPL"].price == 200
    assert by["VOO"].asset_class == "etf"
    assert by["USD"].asset_class == "cash" and by["USD"].market_value == 500
    snap = Snapshot("t", "HKD")
    add_result(snap, r, FX)
    assert snap.warnings == []


def test_schwab_reconcile_flags_gap():
    accounts = json.loads(json.dumps(SCHWAB_ACCOUNTS))
    accounts[0]["securitiesAccount"]["currentBalances"]["liquidationValue"] = 9000
    snap = Snapshot("t", "HKD")
    add_result(snap, schwab.parse_accounts(accounts), FX)
    assert any("reports" in w for w in snap.warnings)


def test_schwab_token_refresh(tmp_path):
    store = schwab.TokenStore(tmp_path / "t.json")
    store.save({"access_token": "old", "refresh_token": "R", "expires_at": time.time() - 10,
                "refresh_expires_at": time.time() + 3 * 86400})
    http = FakeHttp({"token": {"access_token": "new", "refresh_token": "R", "expires_in": 1800}})
    c = schwab.SchwabClient("id", "sec", "https://127.0.0.1", store, http=http)
    assert c.access_token() == "new"
    assert http.calls[0][1]["grant_type"] == "refresh_token"
    assert 2.9 < c.refresh_days_left() < 3.1  # same refresh token keeps its original window


def test_schwab_expired_login(tmp_path):
    store = schwab.TokenStore(tmp_path / "t.json")
    store.save({"access_token": "a", "refresh_token": "R", "expires_at": 0, "refresh_expires_at": time.time() - 1})
    c = schwab.SchwabClient("id", "sec", "https://127.0.0.1", store, http=FakeHttp({}))
    with pytest.raises(schwab.SchwabAuthError, match="schwab-login"):
        c.access_token()


def test_schwab_exchange_redirect(tmp_path):
    store = schwab.TokenStore(tmp_path / "t.json")
    http = FakeHttp({"token": {"access_token": "a", "refresh_token": "R", "expires_in": 1800}})
    c = schwab.SchwabClient("id", "sec", "https://127.0.0.1", store, http=http)
    c.exchange_redirect("https://127.0.0.1/?code=C0.abc%40&session=x")
    assert http.calls[0][1]["code"] == "C0.abc@"
    assert store.load()["refresh_expires_at"] > time.time() + 6.9 * 86400


# --- Futu ------------------------------------------------------------------

def test_futu_parse():
    info = {"total_assets": 100000, "cash": 999, "hk_cash": 20000, "us_cash": 1000, "cn_cash": "N/A",
            "fund_assets": 0, "bond_assets": 0}
    rows = [
        {"code": "HK.00700", "stock_name": "TENCENT", "qty": 100, "market_val": 50000, "nominal_price": 500,
         "average_cost": 300, "currency": "HKD", "position_side": "LONG"},
        {"code": "US.TSLA", "stock_name": "Tesla", "qty": 10, "market_val": 2500, "nominal_price": 250,
         "average_cost": 200, "currency": "N/A", "position_side": "SHORT"},
        {"code": "HK.09988", "qty": 0, "market_val": 0},
    ]
    a = futu.parse_accinfo(info, "CASH …1234")
    p = futu.parse_positions(rows, "CASH …1234")
    cash = {h.symbol: h.market_value for h in a.holdings}
    assert cash == {"HKD": 20000, "USD": 1000}
    by = {h.symbol: h for h in p.holdings}
    assert set(by) == {"HK.00700", "US.TSLA"}
    assert by["US.TSLA"].currency == "USD" and by["US.TSLA"].quantity == -10 and by["US.TSLA"].market_value == -2500
    assert by["HK.00700"].cost_basis == 30000


# --- Aggregation -----------------------------------------------------------

class Boom(Connector):
    name = "boom"

    def fetch(self):
        raise RuntimeError("down")


class Static(Connector):
    name = "static"

    def fetch(self):
        return schwab.parse_accounts(SCHWAB_ACCOUNTS, "static")


def test_collect_isolates_failures_and_renders():
    snap = collect([Boom(), Static()], FX)
    assert snap.status["boom"][0] is False and snap.status["static"][0] is True
    assert snap.total == pytest.approx(5000 / 0.128)
    text = render(snap)
    assert "FAIL boom" in text and "AAPL" in text


def test_fx_aliases():
    assert FX.to_base(92, "CNH") == pytest.approx(100)
    assert FX.to_base(12.8, "usdt") == pytest.approx(100)
    with pytest.raises(KeyError):
        FX.to_base(1, "XXX")
