"""Currency conversion with live rates for BillPilot.

PayPal has no usable FX endpoint for this app (its Currency Exchange v2 API is
a limited-release, approval-gated product for Orders-checkout pricing), so
rates come from the free, no-key endpoint https://open.er-api.com (updated
daily). Results are cached in memory for 24 hours.

Usage:
    from services.fx import convert
    convert(800, "EUR", "USD")   # {"amount": 800, "from": "EUR", "to": "USD",
                                 #  "rate": 1.115..., "converted": 892.12, ...}
"""
import re
import time

import requests

BASE_URL = "https://open.er-api.com/v6/latest"
TIMEOUT = 20
CACHE_SECONDS = 24 * 3600

_cache = {}  # base -> (fetched_at, rates dict)


class FXError(Exception):
    pass


def _clean_code(value, field):
    code = (value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        raise FXError(f"{field} must be a 3-letter currency code, got {value!r}.")
    return code


def get_rates(base="USD"):
    """Return {currency: rate} against `base`. Cached 24h. Raises FXError."""
    base = _clean_code(base, "base")
    now = time.time()
    hit = _cache.get(base)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        r = requests.get(f"{BASE_URL}/{base}", timeout=TIMEOUT)
    except requests.RequestException as e:
        raise FXError(f"Could not reach the exchange-rate service: {e}") from e
    if not r.ok:
        raise FXError(f"Rate request failed ({r.status_code}): {r.text[:200]}")
    try:
        data = r.json()
        rates = data["rates"]
    except (ValueError, KeyError, TypeError) as e:
        raise FXError(f"Unexpected rate response shape: {r.text[:200]}") from e
    if data.get("result") != "success" or not isinstance(rates, dict):
        raise FXError(f"Rate service returned an error: {r.text[:200]}")
    _cache[base] = (now, rates)
    return rates


def convert(amount, from_currency, to_currency):
    """Convert `amount` between currencies at the live rate.

    Returns {"amount", "from", "to", "rate", "converted"} with converted
    rounded to 2 decimals. Same-currency returns the amount unchanged.
    Raises FXError on bad input or rate failure.
    """
    try:
        amount = float(amount)
    except (TypeError, ValueError) as e:
        raise FXError(f"Amount is not a number: {amount!r}") from e
    if amount < 0:
        raise FXError("Amount cannot be negative.")
    from_currency = _clean_code(from_currency, "from_currency")
    to_currency = _clean_code(to_currency, "to_currency")
    if from_currency == to_currency:
        return {
            "amount": amount, "from": from_currency, "to": to_currency,
            "rate": 1.0, "converted": round(amount, 2),
        }
    rates = get_rates(from_currency)
    if to_currency not in rates:
        raise FXError(f"No live rate for {from_currency} to {to_currency}.")
    rate = rates[to_currency]
    return {
        "amount": amount, "from": from_currency, "to": to_currency,
        "rate": rate, "converted": round(amount * rate, 2),
    }
