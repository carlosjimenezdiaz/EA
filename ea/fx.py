"""Foreign exchange rate fetching with local cache (6-hour TTL)."""

import json
import requests
from datetime import datetime, timedelta
from pathlib import Path

CACHE_FILE = Path.home() / ".ea" / "fx_cache.json"
CACHE_TTL_HOURS = 6
API_URL = "https://open.er-api.com/v6/latest/{base}"


def _load_cache(base: str) -> dict | None:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text())
        if data.get("base") == base.upper():
            cached_at = datetime.fromisoformat(data["cached_at"])
            if datetime.now() - cached_at < timedelta(hours=CACHE_TTL_HOURS):
                return data["rates"]
    except Exception:
        pass
    return None


def _save_cache(base: str, rates: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps({
        "base": base.upper(),
        "cached_at": datetime.now().isoformat(),
        "rates": rates,
    }))


def get_rates(base: str = "USD") -> dict[str, float]:
    """Return exchange rates for *base* currency. Caches for 6 hours."""
    base = base.upper()
    cached = _load_cache(base)
    if cached:
        return cached
    try:
        resp = requests.get(API_URL.format(base=base), timeout=10)
        resp.raise_for_status()
        rates = resp.json()["rates"]
        _save_cache(base, rates)
        return rates
    except Exception as exc:
        # Fall back to stale cache rather than crashing
        if CACHE_FILE.exists():
            try:
                data = json.loads(CACHE_FILE.read_text())
                if data.get("base") == base:
                    return data["rates"]
            except Exception:
                pass
        raise RuntimeError(f"Cannot fetch FX rates: {exc}") from exc


def convert(amount: float, from_ccy: str, to_ccy: str) -> tuple[float, float]:
    """Convert *amount* from *from_ccy* to *to_ccy*.

    Returns ``(converted_amount, rate)`` where *rate* is from_ccy → to_ccy.
    """
    from_ccy, to_ccy = from_ccy.upper(), to_ccy.upper()
    if from_ccy == to_ccy:
        return amount, 1.0
    rates = get_rates(from_ccy)
    if to_ccy not in rates:
        raise ValueError(f"Unknown currency: {to_ccy}")
    rate = rates[to_ccy]
    return round(amount * rate, 4), rate
