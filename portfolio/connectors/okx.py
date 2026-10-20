"""OKX V5 REST API with a read-only API key.

Covers the trading account (with OKX's own USD valuation), the funding
account and Simple Earn (savings). Derivative positions are listed for
information but not counted, since their PnL is already in trading equity.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from .. import config
from ..models import FetchResult, Holding, ReportedTotal, to_float
from .base import Connector

STABLECOINS = {"USDT", "USDC", "USD", "FDUSD", "DAI"}
DUST = 1e-9


class OKXError(RuntimeError):
    pass


def timestamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def sign(secret: str, ts: str, method: str, request_path: str, body: str = "") -> str:
    """OKX signature: base64(HMAC-SHA256(secret, ts + METHOD + path?query + body))."""
    prehash = f"{ts}{method.upper()}{request_path}{body}"
    return base64.b64encode(hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()).decode()


class OKXClient:
    def __init__(self, api_key: str, secret: str, passphrase: str,
                 base_url: str = "https://www.okx.com", http: requests.Session | None = None):
        self.api_key = api_key
        self.secret = secret
        self.passphrase = passphrase
        self.base_url = base_url.rstrip("/")
        self.http = http or requests.Session()

    @classmethod
    def from_env(cls) -> "OKXClient":
        return cls(
            api_key=config.require("OKX_API_KEY"),
            secret=config.require("OKX_API_SECRET"),
            passphrase=config.require("OKX_API_PASSPHRASE"),
            base_url=config.env("OKX_BASE_URL", "https://www.okx.com"),
        )

    def get(self, path: str, params: dict | None = None, private: bool = True) -> list:
        request_path = path + (f"?{urlencode(params)}" if params else "")
        headers = {"Content-Type": "application/json"}
        if private:
            ts = timestamp()
            headers.update({
                "OK-ACCESS-KEY": self.api_key,
                "OK-ACCESS-SIGN": sign(self.secret, ts, "GET", request_path),
                "OK-ACCESS-TIMESTAMP": ts,
                "OK-ACCESS-PASSPHRASE": self.passphrase,
            })
        resp = self.http.get(self.base_url + request_path, headers=headers, timeout=30)
        try:
            payload = resp.json()
        except json.JSONDecodeError:
            raise OKXError(f"{path}: HTTP {resp.status_code} {resp.text[:200]}")
        if str(payload.get("code")) != "0":
            raise OKXError(f"{path}: {payload.get('code')} {payload.get('msg')}")
        return payload.get("data", [])


def spot_prices_usd(tickers: list[dict]) -> dict[str, float]:
    """Map coin -> USD price from SPOT tickers, treating USDT as USD."""
    prices = {c: 1.0 for c in STABLECOINS}
    for t in tickers:
        base, _, quote = t.get("instId", "").partition("-")
        last = to_float(t.get("last"))
        if quote == "USDT" and last and base not in prices:
            prices[base] = last
    return prices


def _asset_class(ccy: str) -> str:
    return "cash" if ccy in STABLECOINS else "crypto"


def parse_trading(data: list[dict], source: str = "okx") -> FetchResult:
    result = FetchResult()
    if not data:
        return result
    acct = data[0]
    for d in acct.get("details", []):
        eq = to_float(d.get("eq")) or 0
        if abs(eq) < DUST:
            continue
        usd = to_float(d.get("eqUsd"))
        result.holdings.append(Holding(source, "trading", d["ccy"], d["ccy"], _asset_class(d["ccy"]),
                                       eq, "USD", usd, usd / eq if usd is not None else None))
    total = to_float(acct.get("totalEq"))
    if total is not None:
        result.reported_totals.append(ReportedTotal(source, "trading", "USD", total))
    return result


def parse_simple_balances(data: list[dict], account: str, qty_field: str,
                          prices: dict[str, float], source: str = "okx") -> FetchResult:
    result = FetchResult()
    for d in data:
        ccy = d["ccy"]
        qty = to_float(d.get(qty_field)) or 0
        if abs(qty) < DUST:
            continue
        price = prices.get(ccy)
        if price is None:
            result.warnings.append(f"OKX {account}: no USDT price for {ccy}, value left blank")
        result.holdings.append(Holding(source, account, ccy, ccy, _asset_class(ccy), qty, "USD",
                                       qty * price if price is not None else None, price))
    return result


def parse_positions(data: list[dict], source: str = "okx") -> FetchResult:
    result = FetchResult()
    for p in data:
        pos = to_float(p.get("pos")) or 0
        if abs(pos) < DUST:
            continue
        if p.get("posSide") == "short":
            pos = -abs(pos)
        result.holdings.append(Holding(
            source, "trading", p.get("instId", "?"), f"{p.get('instType', '')} {p.get('mgnMode', '')}".strip(),
            "derivative", pos, "USD", to_float(p.get("notionalUsd")), to_float(p.get("markPx")),
            counted=False,
        ))
    return result


def merge(*results: FetchResult) -> FetchResult:
    out = FetchResult()
    for r in results:
        out.holdings += r.holdings
        out.reported_totals += r.reported_totals
        out.warnings += r.warnings
    return out


class OKXConnector(Connector):
    name = "okx"

    def __init__(self, client: OKXClient | None = None):
        self.client = client or OKXClient.from_env()

    def fetch(self) -> FetchResult:
        c = self.client
        prices = spot_prices_usd(c.get("/api/v5/market/tickers", {"instType": "SPOT"}, private=False))
        return merge(
            parse_trading(c.get("/api/v5/account/balance"), self.name),
            parse_simple_balances(c.get("/api/v5/asset/balances"), "funding", "bal", prices, self.name),
            parse_simple_balances(c.get("/api/v5/finance/savings/balance"), "earn", "amt", prices, self.name),
            parse_positions(c.get("/api/v5/account/positions"), self.name),
        )
