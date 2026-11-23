"""Phase 6 worker job tests: end-to-end market_scan pipeline & proposal_expiry."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx

from app.ai.client import AiAnalyzer
from app.api_client import WorkerApiClient
from app.jobs.market_scan import run_market_scan
from app.jobs.proposal_expiry import run_proposal_expiry
from app.mt5.account import VerifiedAccount
from app.mt5.client import MT5Client
from app.mt5.market_data import LiveQuote
from app.mt5.symbols import VerifiedSymbolSpecs
from app.strategy.xauusd_v1 import XauusdV1Strategy
from tests.test_phase3_strategy_backtest import _build_setup_bars
from tests.test_risk import FakeMT5Risk


def test_market_scan_submits_proposal_end_to_end() -> None:
    h1, m15 = _build_setup_bars("UP")
    now = datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC)
    bid = m15[-1].close
    quote = LiveQuote(
        symbol="XAUUSDc",
        bid=bid,
        ask=bid + Decimal("0.20"),
        spread=Decimal("0.20"),
        quote_time=now - timedelta(seconds=1),
    )
    account = VerifiedAccount(
        login="12345678",
        server="Exness-MT5Demo",
        account_currency="USC",
        currency_scale=100,
        account_type="CENT",
        margin_mode="HEDGING",
        mode="DEMO",
        balance=Decimal("10000.00"),
        equity=Decimal("10000.00"),
        free_margin=Decimal("9800.00"),
        terminal_connected=True,
        terminal_trade_allowed=True,
        account_trade_allowed=True,
    )
    specs = VerifiedSymbolSpecs(
        symbol="XAUUSDc",
        volume_min=Decimal("0.01"),
        volume_max=Decimal("200.00"),
        volume_step=Decimal("0.01"),
        trade_contract_size=Decimal("100.0"),
        trade_tick_size=Decimal("0.01"),
        trade_tick_value=Decimal("1.0"),
        point=Decimal("0.01"),
        digits=2,
        trade_stops_level=10,
        trade_freeze_level=0,
        filling_mode="IOC",
        filling_mode_flags=2,
        expiration_mode_flags=15,
        trade_mode=4,
    )

    ai_json = json.dumps(
        {
            "recommendation": "PROCEED",
            "confidence": 0.77,
            "market_regime": "BULLISH_CONTINUATION",
            "reasons": ["Trend aligned"],
            "risks": ["Gold volatility"],
            "invalidation_notes": ["Break below swing low"],
        }
    )
    fake_openai = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=ai_json))],
                    usage=SimpleNamespace(total_tokens=200),
                )
            )
        )
    )
    ai = AiAnalyzer(api_key="test", openai_client=fake_openai)

    posted: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/internal/v1/proposals":
            body = json.loads(req.content.decode("utf-8"))
            posted.append(body)
            return httpx.Response(201, json={"proposal": {"id": "prop_1", **body}})
        if req.url.path == "/api/internal/v1/proposals/expire":
            return httpx.Response(200, json={"ok": True, "expiredCount": 2})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(
        base_url="http://127.0.0.1:5000", transport=httpx.MockTransport(handler)
    ) as raw:
        api = WorkerApiClient("http://127.0.0.1:5000", "secret_token_1234", client=raw)
        client = MT5Client(mt5_module=FakeMT5Risk(contract_size=100.0, margin_per_lot=150.0))

        res = run_market_scan(
            client=client,
            api=api,
            strategy=XauusdV1Strategy(),
            ai_analyzer=ai,
            account=account,
            broker_account_id="acc_1",
            symbol_specs=specs,
            quote=quote,
            h1_bars=h1,
            m15_bars=m15,
            now_utc=now,
            risk_percent=Decimal("0.50"),
        )
        assert res is not None
        assert len(posted) == 1
        assert posted[0]["status"] == "PENDING_APPROVAL"
        assert posted[0]["aiStatus"] == "COMPLETED"

        expired = run_proposal_expiry(api)
        assert expired == 2
