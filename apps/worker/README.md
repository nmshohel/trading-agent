# Python Trading Worker (`apps/worker`)

Windows-hosted MT5 connector, market data normalizer, deterministic strategy/risk engine, AI explanation client, and backtesting module.

## Setup (Windows, Python 3.11 / 3.12 recommended for `MetaTrader5`)

```powershell
cd apps\worker
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest
```

Never commit `.env` or MT5 passwords. Store broker credentials in Windows Credential Manager.
