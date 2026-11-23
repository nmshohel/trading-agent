"""Phase 0 configuration unit tests."""

from app.config import WorkerConfig


def test_worker_config_defaults_to_demo_cent() -> None:
    cfg = WorkerConfig(
        _env_file=None,
        worker_auth_token="test_worker_token_at_least_16_chars",
        mt5_account_login=12345678,
        mt5_account_server="Exness-MT5Demo",
    )
    assert cfg.mt5_account_mode == "DEMO"
    assert cfg.mt5_account_type == "CENT"
    assert cfg.mt5_symbol == "XAUUSDc"
    assert cfg.ai_fallback_mode == "DETERMINISTIC_ONLY"
