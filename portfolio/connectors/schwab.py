"""Charles Schwab Trader API (Individual).

Auth is OAuth 2.0 authorization code: access tokens last 30 minutes and are
refreshed automatically; the refresh token lasts 7 days, after which
`python -m portfolio schwab-login` must be run again in a browser.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .. import config
from ..models import FetchResult, Holding, ReportedTotal, mask_account, to_float
from .base import Connector

AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
API_BASE = "https://api.schwabapi.com/trader/v1"

REFRESH_TOKEN_TTL = 7 * 24 * 3600
EXPIRY_MARGIN = 60

ASSET_CLASSES = {
    "EQUITY": "equity",
    "COLLECTIVE_INVESTMENT": "etf",
    "MUTUAL_FUND": "fund",
    "FIXED_INCOME": "bond",
    "OPTION": "option",
    "CASH_EQUIVALENT": "cash",
}


class SchwabAuthError(RuntimeError):
    pass


class TokenStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text())

    def save(self, token: dict) -> None:
        self.path.write_text(json.dumps(token, indent=2))
        self.path.chmod(0o600)


class SchwabClient:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str,
                 store: TokenStore, http: requests.Session | None = None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.store = store
        self.http = http or requests.Session()

    @classmethod
    def from_env(cls) -> "SchwabClient":
        return cls(
            client_id=config.require("SCHWAB_CLIENT_ID"),
            client_secret=config.require("SCHWAB_CLIENT_SECRET"),
            redirect_uri=config.env("SCHWAB_REDIRECT_URI", "https://127.0.0.1"),
            store=TokenStore(config.data_dir() / "schwab_token.json"),
        )

    # --- OAuth -------------------------------------------------------------

    def authorize_url(self) -> str:
        return f"{AUTH_URL}?{urlencode({'client_id': self.client_id, 'redirect_uri': self.redirect_uri})}"

    def exchange_redirect(self, redirected_url: str) -> dict:
        """Exchange the URL the browser was redirected to for tokens."""
        code = parse_qs(urlparse(redirected_url).query).get("code", [None])[0]
        if not code:
            raise SchwabAuthError("No ?code= in the pasted URL")
        token = self._token_request({"grant_type": "authorization_code", "code": code,
                                     "redirect_uri": self.redirect_uri})
        token["refresh_expires_at"] = time.time() + REFRESH_TOKEN_TTL
        self.store.save(token)
        return token

    def _token_request(self, data: dict) -> dict:
        resp = self.http.post(TOKEN_URL, data=data, auth=(self.client_id, self.client_secret), timeout=30)
        if resp.status_code != 200:
            raise SchwabAuthError(f"Token request failed ({resp.status_code}): {resp.text}")
        token = resp.json()
        token["expires_at"] = time.time() + int(token.get("expires_in", 1800))
        return token

    def access_token(self) -> str:
        token = self.store.load()
        if not token:
            raise SchwabAuthError("Not logged in: run `python -m portfolio schwab-login`")
        now = time.time()
        if now >= token.get("refresh_expires_at", 0) - EXPIRY_MARGIN:
            raise SchwabAuthError("Refresh token expired (7-day limit): run `python -m portfolio schwab-login`")
        if now >= token.get("expires_at", 0) - EXPIRY_MARGIN:
            new = self._token_request({"grant_type": "refresh_token", "refresh_token": token["refresh_token"]})
            # A new refresh token value starts a new 7-day window; the same value keeps the old one.
            if new.get("refresh_token") and new["refresh_token"] != token["refresh_token"]:
                new["refresh_expires_at"] = now + REFRESH_TOKEN_TTL
            else:
                new["refresh_token"] = token["refresh_token"]
                new["refresh_expires_at"] = token["refresh_expires_at"]
            self.store.save(new)
            token = new
        return token["access_token"]

    def refresh_days_left(self) -> float | None:
        token = self.store.load()
        if not token:
            return None
        return (token.get("refresh_expires_at", 0) - time.time()) / 86400

    # --- API ---------------------------------------------------------------

    def get_accounts(self) -> list[dict]:
        resp = self.http.get(f"{API_BASE}/accounts", params={"fields": "positions"},
                             headers={"Authorization": f"Bearer {self.access_token()}"}, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Schwab /accounts failed ({resp.status_code}): {resp.text}")
        return resp.json()


def parse_accounts(accounts: list[dict], source: str = "schwab") -> FetchResult:
    result = FetchResult()
    for entry in accounts:
        acct = entry.get("securitiesAccount", {})
        account = mask_account(acct.get("accountNumber", "?"))
        for p in acct.get("positions", []) or []:
            inst = p.get("instrument", {})
            asset_type = inst.get("assetType", "")
            qty = (to_float(p.get("longQuantity")) or 0) - (to_float(p.get("shortQuantity")) or 0)
            avg = to_float(p.get("averagePrice"))
            value = to_float(p.get("marketValue"))
            result.holdings.append(Holding(
                source=source,
                account=account,
                symbol=inst.get("symbol") or inst.get("cusip", "?"),
                name=inst.get("description", ""),
                asset_class=ASSET_CLASSES.get(asset_type, "other"),
                quantity=qty,
                currency="USD",
                market_value=value,
                price=value / qty if value is not None and qty else None,
                # Option average prices are not clearly per-share vs per-contract, so skip cost there.
                cost_basis=avg * qty if avg is not None and asset_type != "OPTION" else None,
            ))
        balances = acct.get("currentBalances", {}) or {}
        cash = to_float(balances.get("cashBalance"))
        if cash:
            result.holdings.append(Holding(source, account, "USD", "Cash", "cash", cash, "USD", cash, 1.0))
        liq = to_float(balances.get("liquidationValue"))
        if liq is not None:
            result.reported_totals.append(ReportedTotal(source, account, "USD", liq))
    return result


class SchwabConnector(Connector):
    name = "schwab"

    def __init__(self, client: SchwabClient | None = None):
        self.client = client or SchwabClient.from_env()

    def fetch(self) -> FetchResult:
        result = parse_accounts(self.client.get_accounts(), self.name)
        days = self.client.refresh_days_left()
        if days is not None and days < 1.5:
            result.warnings.append(f"Schwab login expires in {days * 24:.0f}h: run `python -m portfolio schwab-login`")
        return result
