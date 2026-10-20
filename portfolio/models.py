"""Source-independent data model shared by every connector."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Holding:
    """One line of a portfolio: a security position or a cash balance.

    `market_value` is denominated in `currency`; the aggregator converts it to
    the base currency. Cash is a holding with asset_class "cash" and the
    currency code as its symbol.
    """

    source: str            # connector name, e.g. "schwab"
    account: str           # display id, masked where it is a real account number
    symbol: str
    name: str
    asset_class: str       # equity | etf | fund | bond | option | crypto | cash | derivative | other
    quantity: float
    currency: str
    market_value: float | None
    price: float | None = None
    cost_basis: float | None = None   # total cost in `currency`, when the source reports it
    counted: bool = True   # False for rows already reflected elsewhere (e.g. OKX derivative exposure)


@dataclass
class ReportedTotal:
    """An account total as reported by the source, used to reconcile holdings."""

    source: str
    account: str
    currency: str
    amount: float


@dataclass
class FetchResult:
    holdings: list[Holding] = field(default_factory=list)
    reported_totals: list[ReportedTotal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def to_float(value) -> float | None:
    """Parse numbers that sources send as strings, '', 'N/A' or NaN."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN check


def mask_account(number: str) -> str:
    number = str(number)
    return "…" + number[-4:] if len(number) > 4 else number
