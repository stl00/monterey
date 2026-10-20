"""HSBC HK personal banking via browser automation (not implemented yet).

HSBC HK has no API individuals can use, so the plan is a Playwright script
that logs in to online banking, reads deposit balances and investment
holdings from the pages, and returns them as a FetchResult like the API
connectors. Login needs your credentials plus a security code from the
HSBC HK app, so it will run headed and pause for the code.
"""
from __future__ import annotations

from ..models import FetchResult
from .base import Connector


class HsbcHkConnector(Connector):
    name = "hsbc_hk"

    def fetch(self) -> FetchResult:
        raise NotImplementedError("HSBC HK browser connector is not built yet")
