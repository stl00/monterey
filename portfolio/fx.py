"""Currency conversion with daily rates from open.er-api.com (free, no key)."""
from __future__ import annotations

import requests

# Currencies the rate source does not list, mapped to the closest one it does.
ALIASES = {"CNH": "CNY", "USDT": "USD", "USDC": "USD"}


class FxRates:
    def __init__(self, base: str, rates: dict[str, float]):
        self.base = base.upper()
        self.rates = {k.upper(): v for k, v in rates.items()}  # units of currency per 1 base
        self.rates[self.base] = 1.0

    @classmethod
    def fetch(cls, base: str, http: requests.Session | None = None) -> "FxRates":
        http = http or requests.Session()
        resp = http.get(f"https://open.er-api.com/v6/latest/{base.upper()}", timeout=30)
        payload = resp.json()
        if payload.get("result") != "success":
            raise RuntimeError(f"FX rate fetch failed: {payload}")
        return cls(base, payload["rates"])

    def to_base(self, amount: float, currency: str) -> float:
        ccy = currency.upper()
        ccy = ccy if ccy in self.rates else ALIASES.get(ccy, ccy)
        if ccy not in self.rates:
            raise KeyError(f"No FX rate for {currency}")
        return amount / self.rates[ccy]
