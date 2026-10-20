"""Run connectors, convert to the base currency, reconcile and summarise."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .connectors.base import Connector
from .fx import FxRates
from .models import FetchResult, Holding

RECONCILE_TOLERANCE = 0.01  # 1%


@dataclass
class Snapshot:
    taken_at: str
    base_ccy: str
    rows: list[tuple[Holding, float | None]] = field(default_factory=list)  # (holding, value in base)
    status: dict[str, tuple[bool, str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return sum(v for h, v in self.rows if h.counted and v is not None)

    def totals_by(self, key) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for h, v in self.rows:
            if h.counted and v is not None:
                out[key(h)] += v
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def collect(connectors: list[Connector], fx: FxRates) -> Snapshot:
    snap = Snapshot(datetime.now(timezone.utc).isoformat(timespec="seconds"), fx.base)
    for c in connectors:
        try:
            result = c.fetch()
        except Exception as e:  # one failing source must not block the others
            snap.status[c.name] = (False, f"{type(e).__name__}: {e}")
            continue
        snap.status[c.name] = (True, f"{len(result.holdings)} holdings")
        add_result(snap, result, fx)
    return snap


def add_result(snap: Snapshot, result: FetchResult, fx: FxRates) -> None:
    snap.warnings += result.warnings
    for h in result.holdings:
        value = None
        if h.market_value is not None:
            try:
                value = fx.to_base(h.market_value, h.currency)
            except KeyError as e:
                snap.warnings.append(f"{h.source} {h.symbol}: {e}")
        snap.rows.append((h, value))
    # Compare each account's counted holdings with the total the source reports.
    for t in result.reported_totals:
        held = sum(v for h, v in snap.rows
                   if h.source == t.source and h.account == t.account and h.counted and v is not None)
        try:
            reported = fx.to_base(t.amount, t.currency)
        except KeyError:
            continue
        if abs(held - reported) > RECONCILE_TOLERANCE * max(abs(reported), 1):
            snap.warnings.append(
                f"{t.source} {t.account}: holdings sum to {held:,.0f} {fx.base} but source reports "
                f"{reported:,.0f} {fx.base}; something may be missing or double counted")


def render(snap: Snapshot, top: int = 15) -> str:
    b = snap.base_ccy
    lines = [f"Portfolio total: {snap.total:,.0f} {b}   ({snap.taken_at})", ""]
    for title, key in (("By source", lambda h: h.source), ("By asset class", lambda h: h.asset_class),
                       ("By currency", lambda h: h.currency)):
        lines.append(title)
        for k, v in snap.totals_by(key).items():
            share = v / snap.total * 100 if snap.total else 0
            lines.append(f"  {k:<16}{v:>16,.0f} {b}  {share:5.1f}%")
        lines.append("")
    by_symbol: dict[str, float] = defaultdict(float)
    for h, v in snap.rows:
        if h.counted and v is not None and h.asset_class != "cash":
            by_symbol[h.symbol] += v
    lines.append(f"Top {top} holdings")
    for sym, v in sorted(by_symbol.items(), key=lambda kv: -kv[1])[:top]:
        lines.append(f"  {sym:<16}{v:>16,.0f} {b}")
    lines.append("")
    lines.append("Sources")
    for name, (ok, msg) in snap.status.items():
        lines.append(f"  {'OK  ' if ok else 'FAIL'} {name}: {msg}")
    if snap.warnings:
        lines.append("")
        lines.append("Warnings")
        lines += [f"  - {w}" for w in snap.warnings]
    return "\n".join(lines)
