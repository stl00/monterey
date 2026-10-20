from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import FetchResult


class Connector(ABC):
    """A data source. Implementations must be read-only."""

    name: str = ""

    @abstractmethod
    def fetch(self) -> FetchResult:
        """Return current holdings and cash for every account in this source."""
