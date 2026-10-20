"""Futu OpenAPI via a locally running OpenD gateway.

OpenD must be running and logged in with your Futu ID. Read queries
(funds, positions) do not unlock trading, and this connector never does.
"""
from __future__ import annotations

from .. import config
from ..models import FetchResult, Holding, ReportedTotal, to_float
from .base import Connector

CASH_COLUMNS = {
    "hk_cash": "HKD", "us_cash": "USD", "cn_cash": "CNH", "jp_cash": "JPY",
    "sg_cash": "SGD", "au_cash": "AUD", "ca_cash": "CAD", "my_cash": "MYR",
}
MARKET_CURRENCY = {"HK": "HKD", "US": "USD", "SH": "CNH", "SZ": "CNH", "SG": "SGD", "JP": "JPY",
                   "AU": "AUD", "CA": "CAD", "MY": "MYR"}


class FutuError(RuntimeError):
    pass


def _check(ret, data, what: str):
    from futu import RET_OK
    if ret != RET_OK:
        raise FutuError(f"{what}: {data}")
    return data


def parse_accinfo(row: dict, account: str, source: str = "futu") -> FetchResult:
    """`row` is one record of accinfo_query(currency=HKD)."""
    result = FetchResult()
    found = False
    for col, ccy in CASH_COLUMNS.items():
        amount = to_float(row.get(col))
        if amount is None:
            continue
        found = True
        if amount:
            result.holdings.append(Holding(source, account, ccy, f"Cash {ccy}", "cash", amount, ccy, amount, 1.0))
    if not found:
        cash = to_float(row.get("cash"))
        if cash:
            result.holdings.append(Holding(source, account, "HKD", "Cash", "cash", cash, "HKD", cash, 1.0))
    total = to_float(row.get("total_assets"))
    if total is not None:
        result.reported_totals.append(ReportedTotal(source, account, "HKD", total))
    for col in ("fund_assets", "bond_assets"):
        if to_float(row.get(col)):
            result.warnings.append(
                f"Futu {account}: {col}={row[col]} HKD; check these appear in positions (reconciliation will flag it if not)")
    return result


def parse_positions(rows: list[dict], account: str, source: str = "futu") -> FetchResult:
    result = FetchResult()
    for r in rows:
        qty = to_float(r.get("qty")) or 0
        if not qty:
            continue
        code = str(r.get("code", "?"))
        market = code.split(".", 1)[0]
        ccy = r.get("currency")
        ccy = str(ccy) if ccy and str(ccy) not in ("N/A", "nan") else MARKET_CURRENCY.get(market, "HKD")
        value = to_float(r.get("market_val"))
        if str(r.get("position_side", "")).upper() == "SHORT":
            qty = -abs(qty)
            value = -abs(value) if value is not None else None
        avg = to_float(r.get("average_cost")) or to_float(r.get("cost_price"))
        result.holdings.append(Holding(
            source=source,
            account=account,
            symbol=code,
            name=str(r.get("stock_name", "")),
            asset_class="equity",
            quantity=qty,
            currency=ccy,
            market_value=value,
            price=to_float(r.get("nominal_price")),
            cost_basis=avg * qty if avg is not None else None,
        ))
    return result


class FutuConnector(Connector):
    name = "futu"

    def __init__(self, host: str | None = None, port: int | None = None, security_firm: str | None = None):
        self.host = host or config.env("FUTU_OPEND_HOST", "127.0.0.1")
        self.port = port or int(config.env("FUTU_OPEND_PORT", "11111"))
        self.security_firm = security_firm or config.env("FUTU_SECURITY_FIRM", "FUTUSECURITIES")

    def fetch(self) -> FetchResult:
        from futu import Currency, OpenSecTradeContext, SecurityFirm, TrdEnv, TrdMarket

        result = FetchResult()
        ctx = OpenSecTradeContext(filter_trdmarket=TrdMarket.NONE, host=self.host, port=self.port,
                                  security_firm=getattr(SecurityFirm, self.security_firm))
        try:
            accounts = _check(*ctx.get_acc_list(), "get_acc_list")
            accounts = accounts[(accounts["trd_env"] == TrdEnv.REAL) & (accounts["acc_status"] != "DISABLED")]
            for _, acc in accounts.iterrows():
                acc_id = int(acc["acc_id"])
                label = f"{acc['acc_type']} …{str(acc_id)[-4:]}"
                info = _check(*ctx.accinfo_query(trd_env=TrdEnv.REAL, acc_id=acc_id, currency=Currency.HKD),
                              f"accinfo_query {label}")
                positions = _check(*ctx.position_list_query(trd_env=TrdEnv.REAL, acc_id=acc_id),
                                   f"position_list_query {label}")
                for part in (parse_accinfo(info.iloc[0].to_dict(), label, self.name),
                             parse_positions(positions.to_dict("records"), label, self.name)):
                    result.holdings += part.holdings
                    result.reported_totals += part.reported_totals
                    result.warnings += part.warnings
        finally:
            ctx.close()
        return result
