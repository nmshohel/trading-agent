"""Open position synchronization job (MT5 -> internal API)."""

from __future__ import annotations

from typing import Any

from app.api_client import WorkerApiClient
from app.config import WorkerConfig
from app.mt5.client import MT5Client
from app.mt5.execution import ORDER_TYPE_BUY
from app.mt5.market_data import mt5_epoch_to_utc


def run_position_sync(
    *,
    client: MT5Client,
    api: WorkerApiClient,
    config: WorkerConfig,
    broker_account_id: str,
    server_offset_seconds: int,
) -> int:
    """Read live MT5 positions for configured symbol and sync to API."""
    mt5_positions = client.positions_get(symbol=config.mt5_symbol) or []
    payload_items: list[dict[str, Any]] = []

    for pos in mt5_positions:
        magic = int(getattr(pos, "magic", 0))
        opened_epoch = int(getattr(pos, "time", 0))
        opened_utc = mt5_epoch_to_utc(opened_epoch, server_offset_seconds)
        pos_type = int(getattr(pos, "type", ORDER_TYPE_BUY))
        sl = getattr(pos, "sl", 0.0)
        tp = getattr(pos, "tp", 0.0)

        payload_items.append(
            {
                "brokerPositionId": str(pos.ticket),
                "symbol": str(getattr(pos, "symbol", config.mt5_symbol)),
                "side": "BUY" if pos_type == ORDER_TYPE_BUY else "SELL",
                "volume": str(getattr(pos, "volume", 0)),
                "openPrice": str(getattr(pos, "price_open", 0)),
                "currentPrice": str(getattr(pos, "price_current", 0)),
                "stopLoss": str(sl) if sl and float(sl) > 0 else None,
                "takeProfit": str(tp) if tp and float(tp) > 0 else None,
                "profit": str(getattr(pos, "profit", 0)),
                "commission": "0",
                "swap": str(getattr(pos, "swap", 0)),
                "magicNumber": magic,
                "isBotManaged": magic == config.mt5_magic_number,
                "status": "OPEN",
                "openedAt": opened_utc.isoformat().replace("+00:00", "Z"),
            }
        )

    res = api.sync_positions(
        {"brokerAccountId": broker_account_id, "positions": payload_items}
    )
    return int(res.get("syncedCount", len(payload_items)))
