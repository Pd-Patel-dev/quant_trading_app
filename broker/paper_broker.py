"""Common paper broker interface."""

from __future__ import annotations

from typing import Protocol


class PaperBroker(Protocol):
    def get_account_summary(self) -> dict[str, object]:
        ...

    def get_open_orders(self) -> list[object]:
        ...

    def get_order_by_client_order_id(self, client_order_id: str) -> object | None:
        ...

    def submit_order(self, request: object) -> object:
        ...
