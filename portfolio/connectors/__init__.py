from __future__ import annotations

from .base import Connector


def build(name: str) -> Connector:
    """Create a connector by name. Imports are lazy so missing optional deps only hit that source."""
    if name == "schwab":
        from .schwab import SchwabConnector
        return SchwabConnector()
    if name == "okx":
        from .okx import OKXConnector
        return OKXConnector()
    if name == "futu":
        from .futu import FutuConnector
        return FutuConnector()
    if name == "hsbc_hk":
        from .hsbc_hk import HsbcHkConnector
        return HsbcHkConnector()
    raise ValueError(f"Unknown source {name!r}")
