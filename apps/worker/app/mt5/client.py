"""Thread-safe wrapper around the official MetaTrader5 Python IPC bridge.

The MetaTrader5 C-extension is NOT thread-safe. Every mt5.* call across polling,
sync, and execution must pass through this centralized MT5Client guarded by an
in-process threading.RLock().
"""

from __future__ import annotations

import threading
from typing import Any


class MT5ConnectionError(RuntimeError):
    """Raised when the local MT5 terminal cannot be initialized or reached."""


class MT5Client:
    """Centralized, RLock-serialized interface to the MetaTrader5 package."""

    def __init__(
        self,
        terminal_path: str | None = None,
        mt5_module: Any | None = None,
    ) -> None:
        self._terminal_path = terminal_path
        self._lock = threading.RLock()
        self._connected = False
        if mt5_module is not None:
            self._mt5 = mt5_module
        else:
            import MetaTrader5 as mt5  # type: ignore[import-untyped]

            self._mt5 = mt5

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def initialize(self) -> bool:
        with self._lock:
            kwargs: dict[str, Any] = {}
            if self._terminal_path:
                kwargs["path"] = self._terminal_path
            ok = bool(self._mt5.initialize(**kwargs))
            self._connected = ok
            if not ok:
                err = self._safe_last_error()
                raise MT5ConnectionError(f"MT5 initialize failed: {err}")
            return True

    def shutdown(self) -> None:
        with self._lock:
            try:
                self._mt5.shutdown()
            finally:
                self._connected = False

    def ensure_connected(self) -> bool:
        """Verify terminal connectivity and attempt one clean reconnect if needed."""
        with self._lock:
            if self._connected:
                info = self._mt5.terminal_info()
                if info is not None and bool(getattr(info, "connected", False)):
                    return True
                self._connected = False
            self.shutdown()
            return self.initialize()

    def terminal_info(self) -> Any:
        with self._lock:
            return self._mt5.terminal_info()

    def account_info(self) -> Any:
        with self._lock:
            return self._mt5.account_info()

    def symbol_info(self, symbol: str) -> Any:
        with self._lock:
            return self._mt5.symbol_info(symbol)

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        with self._lock:
            return bool(self._mt5.symbol_select(symbol, enable))

    def symbol_info_tick(self, symbol: str) -> Any:
        with self._lock:
            return self._mt5.symbol_info_tick(symbol)

    def symbols_get(self, group: str | None = None) -> Any:
        with self._lock:
            if group is None:
                return self._mt5.symbols_get()
            return self._mt5.symbols_get(group=group)

    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> Any:
        with self._lock:
            return self._mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)

    def order_calc_profit(
        self,
        action: int,
        symbol: str,
        volume: float,
        price_open: float,
        price_close: float,
    ) -> float | None:
        with self._lock:
            val = self._mt5.order_calc_profit(
                action, symbol, volume, price_open, price_close
            )
            return None if val is None else float(val)

    def order_calc_margin(
        self, action: int, symbol: str, volume: float, price: float
    ) -> float | None:
        with self._lock:
            val = self._mt5.order_calc_margin(action, symbol, volume, price)
            return None if val is None else float(val)

    def order_check(self, request: dict[str, Any]) -> Any:
        with self._lock:
            return self._mt5.order_check(request)

    def order_send(self, request: dict[str, Any]) -> Any:
        with self._lock:
            return self._mt5.order_send(request)

    def positions_get(
        self, *, symbol: str | None = None, ticket: int | None = None
    ) -> Any:
        with self._lock:
            if ticket is not None:
                return self._mt5.positions_get(ticket=ticket)
            if symbol is not None:
                return self._mt5.positions_get(symbol=symbol)
            return self._mt5.positions_get()

    def orders_get(
        self, *, symbol: str | None = None, ticket: int | None = None
    ) -> Any:
        with self._lock:
            if ticket is not None:
                return self._mt5.orders_get(ticket=ticket)
            if symbol is not None:
                return self._mt5.orders_get(symbol=symbol)
            return self._mt5.orders_get()

    def history_deals_get(
        self, date_from: Any = None, date_to: Any = None, *, position: int | None = None
    ) -> Any:
        with self._lock:
            if position is not None:
                return self._mt5.history_deals_get(position=position)
            return self._mt5.history_deals_get(date_from, date_to)

    def last_error(self) -> Any:
        with self._lock:
            return self._safe_last_error()

    def _safe_last_error(self) -> Any:
        fn = getattr(self._mt5, "last_error", None)
        return fn() if callable(fn) else ("unknown", "")
