"""
weather.py
Fetches hourly weather data for Gatsibo District from the Open-Meteo API.
Results are cached in Redis (TTL: 1 hour) to avoid redundant API calls.

Open-Meteo docs : https://open-meteo.com/en/docs
No API key required.

Gatsibo coordinates: lat=-1.5773, lon=30.4249
"""

import json
import logging
from datetime import date, timedelta
from typing import Optional

import httpx
import redis

logger = logging.getLogger(__name__)

# ── Gatsibo District, Rwanda ──────────────────────────────────────────────────
LATITUDE  =  -1.5773
LONGITUDE =  30.4249
TIMEZONE  = "Africa/Kigali"

# ── Open-Meteo endpoints ──────────────────────────────────────────────────────
FORECAST_URL  = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL   = "https://archive-api.open-meteo.com/v1/archive"

# Variables we pull — used as model features
HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "precipitation",
    "shortwave_radiation",
    "wind_speed_10m",
]

# ── Redis config (falls back gracefully if Redis is unavailable) ──────────────
REDIS_URL        = "redis://localhost:6379/0"
CACHE_TTL_SECS   = 3600   # 1 hour


def _get_redis() -> Optional[redis.Redis]:
    """Return a Redis client, or None if the server is unreachable."""
    try:
        client = redis.from_url(REDIS_URL, socket_connect_timeout=2)
        client.ping()
        return client
    except Exception:
        logger.warning("[weather] Redis unavailable — caching disabled.")
        return None


def _cache_key(start: str, end: str, source: str) -> str:
    return f"weather:{source}:{start}:{end}"


def fetch_forecast(days_ahead: int = 7) -> dict:
    """
    Fetch hourly weather forecast for the next `days_ahead` days.

    Returns
    -------
    dict with key 'hourly' containing lists for each variable + 'time'.
    """
    start = date.today().isoformat()
    end   = (date.today() + timedelta(days=days_ahead - 1)).isoformat()
    key   = _cache_key(start, end, "forecast")

    r = _get_redis()
    if r:
        cached = r.get(key)
        if cached:
            logger.info("[weather] Cache HIT for forecast %s → %s", start, end)
            return json.loads(cached)

    params = {
        "latitude":       LATITUDE,
        "longitude":      LONGITUDE,
        "hourly":         ",".join(HOURLY_VARS),
        "timezone":       TIMEZONE,
        "forecast_days":  days_ahead,
    }

    logger.info("[weather] Fetching forecast from Open-Meteo (%s → %s)", start, end)
    try:
        response = httpx.get(FORECAST_URL, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        logger.error("[weather] Open-Meteo request failed: %s", exc)
        raise RuntimeError(f"Weather API unavailable: {exc}") from exc

    if r:
        r.setex(key, CACHE_TTL_SECS, json.dumps(data))
        logger.info("[weather] Cached forecast under key '%s'", key)

    return data


def fetch_historical(start: str, end: str) -> dict:
    """
    Fetch historical hourly weather data for a date range.

    Parameters
    ----------
    start : ISO date string, e.g. '2023-01-01'
    end   : ISO date string, e.g. '2023-12-31'

    Returns
    -------
    dict with key 'hourly' containing lists for each variable + 'time'.
    """
    key = _cache_key(start, end, "archive")

    r = _get_redis()
    if r:
        cached = r.get(key)
        if cached:
            logger.info("[weather] Cache HIT for archive %s → %s", start, end)
            return json.loads(cached)

    params = {
        "latitude":   LATITUDE,
        "longitude":  LONGITUDE,
        "start_date": start,
        "end_date":   end,
        "hourly":     ",".join(HOURLY_VARS),
        "timezone":   TIMEZONE,
    }

    logger.info("[weather] Fetching archive from Open-Meteo (%s → %s)", start, end)
    try:
        response = httpx.get(ARCHIVE_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        logger.error("[weather] Archive request failed: %s", exc)
        raise RuntimeError(f"Weather archive unavailable: {exc}") from exc

    if r:
        # Archive data doesn't change — cache for 24 hours
        r.setex(key, 86400, json.dumps(data))

    return data


def weather_to_dataframe(raw: dict):
    """
    Convert Open-Meteo response dict to a pandas DataFrame.

    Returns a DataFrame indexed by datetime with one column per variable.
    """
    import pandas as pd

    hourly = raw.get("hourly", {})
    if not hourly or "time" not in hourly:
        raise ValueError("Unexpected Open-Meteo response format.")

    df = pd.DataFrame(hourly)
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")
    df.index.name = "ds"
    return df
