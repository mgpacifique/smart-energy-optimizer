"""
eia_loader.py
Fetches real hourly electricity demand data from the US Energy Information
Administration (EIA) API for the ERCO (Texas ERCOT) region and normalises
it to Gatsibo District scale for model training.

Why ERCO as a proxy?
  - Hot semi-arid climate → strong AC-driven load peaks (mirrors Rwanda's heat)
  - Clear morning + evening demand peaks matching East African district patterns
  - 3 years of clean hourly data available via EIA Open Data API

EIA API docs : https://www.eia.gov/opendata/
Endpoint     : /v2/electricity/rto/region-data/data/
Auth         : API key via X-Params header or api_key query param

Normalisation:
  ERCO peak demand ≈ 70,000–85,000 MW (entire Texas grid)
  Gatsibo District  ≈ 10–25 MW
  Scale factor      = GATSIBO_PEAK_MW / ERCO_OBSERVED_PEAK_MW
  This preserves the shape (seasonality, diurnal pattern, weekday effect)
  while rescaling magnitudes to district level.
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
EIA_API_KEY      = os.getenv("EIA_API_KEY", "")
EIA_BASE_URL     = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
EIA_REGION       = "ERCO"          # Texas ERCOT
EIA_DATA_TYPE    = "D"             # D = Demand (MWh)
PAGE_SIZE        = 5000            # max rows per EIA request
YEARS_BACK       = 3

# Gatsibo normalisation targets (MW)
GATSIBO_BASE_MW  = 12.0
GATSIBO_PEAK_MW  = 22.0

OUTPUT_PATH      = Path(__file__).parent / "data" / "gatsibo_load.csv"
RAW_CACHE_PATH   = Path(__file__).parent / "data" / "eia_raw_cache.csv"


# ── EIA fetcher ───────────────────────────────────────────────────────────────

def _fetch_page(start: str, end: str, offset: int = 0) -> dict:
    """
    Fetch one page of EIA hourly demand data.

    Parameters
    ----------
    start  : ISO date string  e.g. '2022-01-01'
    end    : ISO date string  e.g. '2024-12-31'
    offset : pagination offset

    Returns raw API response dict.
    """
    if not EIA_API_KEY:
        raise RuntimeError(
            "EIA_API_KEY not set. Add it to backend/.env — "
            "get a free key at https://www.eia.gov/opendata/register.php"
        )

    params = {
        "api_key":               EIA_API_KEY,
        "frequency":             "hourly",
        "data[0]":               "value",
        "facets[respondent][]":  EIA_REGION,
        "facets[type][]":        EIA_DATA_TYPE,
        "start":                 start,
        "end":                   end,
        "sort[0][column]":       "period",
        "sort[0][direction]":    "asc",
        "offset":                offset,
        "length":                PAGE_SIZE,
    }

    resp = httpx.get(EIA_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_eia_raw(
    years_back: int = YEARS_BACK,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Pull full history from EIA, paginating until all rows are retrieved.
    Caches raw data locally to avoid re-fetching on every startup.

    Returns DataFrame with columns: period (datetime), value_mw (float)
    """
    # ── Try cache first ───────────────────────────────────────────────────────
    if use_cache and RAW_CACHE_PATH.exists():
        cache_age_days = (
            datetime.now() - datetime.fromtimestamp(RAW_CACHE_PATH.stat().st_mtime)
        ).days
        if cache_age_days < 1:
            logger.info("[eia] Using cached raw data (%d days old).", cache_age_days)
            return pd.read_csv(RAW_CACHE_PATH, parse_dates=["period"])

    end_date   = datetime.utcnow().strftime("%Y-%m-%d")
    start_date = (datetime.utcnow() - timedelta(days=365 * years_back)).strftime("%Y-%m-%d")

    logger.info(
        "[eia] Fetching ERCO demand data %s → %s (up to ~%d rows)…",
        start_date, end_date, years_back * 8760,
    )

    all_rows = []
    offset   = 0

    while True:
        logger.info("[eia] Page offset=%d …", offset)
        try:
            data = _fetch_page(start_date, end_date, offset)
        except httpx.HTTPStatusError as exc:
            logger.error("[eia] HTTP error %s: %s", exc.response.status_code, exc)
            raise

        rows = data.get("response", {}).get("data", [])
        if not rows:
            break

        all_rows.extend(rows)
        total_raw = data.get("response", {}).get("total", 0)
        try:
            total = int(total_raw)
        except (TypeError, ValueError):
            logger.warning("[eia] Unexpected response.total=%r; using collected row count.", total_raw)
            total = len(all_rows)

        logger.info("[eia] Retrieved %d / %d rows.", len(all_rows), total)

        if len(all_rows) >= total:
            break

        offset += PAGE_SIZE
        time.sleep(0.3)   # be polite to the API

    if not all_rows:
        raise RuntimeError("[eia] No data returned from EIA API.")

    df = pd.DataFrame(all_rows)
    df = df.rename(columns={"period": "period", "value": "value_mw"})
    df["period"]   = pd.to_datetime(df["period"], format="%Y-%m-%dT%H")
    df["value_mw"] = pd.to_numeric(df["value_mw"], errors="coerce")
    df = df.dropna(subset=["value_mw"])
    df = df.sort_values("period").reset_index(drop=True)

    # Cache raw data
    RAW_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(RAW_CACHE_PATH, index=False)
    logger.info("[eia] Raw data cached → %s (%d rows)", RAW_CACHE_PATH, len(df))

    return df[["period", "value_mw"]]


# ── Normaliser ────────────────────────────────────────────────────────────────

def normalise_to_gatsibo(df: pd.DataFrame) -> pd.DataFrame:
    """
    Rescale ERCO MWh values (tens of thousands) down to Gatsibo MW scale
    (10–25 MW) while preserving the load shape entirely.

    Method: min-max rescale to [GATSIBO_BASE_MW, GATSIBO_PEAK_MW]
    then add small Gaussian noise to simulate district-level variance.
    """
    rng = np.random.default_rng(42)

    v_min = df["value_mw"].quantile(0.01)   # use percentiles to clip outliers
    v_max = df["value_mw"].quantile(0.99)

    normalised = (df["value_mw"].clip(v_min, v_max) - v_min) / (v_max - v_min)
    scaled     = GATSIBO_BASE_MW + normalised * (GATSIBO_PEAK_MW - GATSIBO_BASE_MW)

    # Add small Gaussian noise (±3% of value) for district realism
    noise = rng.normal(0, scaled * 0.03)
    scaled = (scaled + noise).clip(GATSIBO_BASE_MW * 0.5, GATSIBO_PEAK_MW * 1.1)

    result = df.copy()
    result["load_mw"] = np.round(scaled, 3)
    return result


# ── Synthetic temperature from EIA timestamps ─────────────────────────────────

def _add_synthetic_temperature(df: pd.DataFrame) -> pd.DataFrame:
    """
    Since EIA doesn't provide temperature, we generate synthetic Rwanda-like
    temperature features correlated with the time of day and month.
    (Real weather is fetched live from Open-Meteo at inference time.)
    """
    rng = np.random.default_rng(99)
    hour  = df["ds"].dt.hour
    month = df["ds"].dt.month

    # Rwanda annual mean ~19°C, hotter in dry season (Jun–Sep)
    base  = np.where(month.isin([6, 7, 8, 9, 12, 1]), 22.0, 19.5)
    swing = 5 * np.sin((hour - 6) * np.pi / 12)   # ±5°C diurnal swing
    noise = rng.normal(0, 1.2, size=len(df))

    df = df.copy()
    df["temp_c"] = np.round(base + swing + noise, 2)
    return df


# ── Main pipeline ─────────────────────────────────────────────────────────────

def load_and_prepare(
    years_back: int = YEARS_BACK,
    use_cache: bool = True,
    output_path: Path = OUTPUT_PATH,
) -> pd.DataFrame:
    """
    Full pipeline: fetch EIA → normalise → add temperature → save CSV.

    Returns DataFrame with columns: ds, load_mw, temp_c
    Compatible with ProphetForecaster and LSTMForecaster training interfaces.
    """
    raw    = fetch_eia_raw(years_back=years_back, use_cache=use_cache)
    scaled = normalise_to_gatsibo(raw)

    # Rename for model compatibility
    scaled = scaled.rename(columns={"period": "ds"})
    scaled = _add_synthetic_temperature(scaled)

    final = scaled[["ds", "load_mw", "temp_c"]].copy()
    final = final.dropna().reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    final.to_csv(output_path, index=False)

    logger.info(
        "[eia] Saved %d rows → %s  |  load range: %.2f–%.2f MW",
        len(final), output_path,
        final["load_mw"].min(), final["load_mw"].max(),
    )
    return final


# ── Fallback: synthetic data if no API key ────────────────────────────────────

def generate_fallback(output_path: Path = OUTPUT_PATH) -> pd.DataFrame:
    """
    Generates synthetic Gatsibo-like data when EIA_API_KEY is not available.
    Preserves the same output schema so models train without modification.
    """
    logger.warning(
        "[eia] EIA_API_KEY not set — generating synthetic fallback data. "
        "Set EIA_API_KEY in backend/.env for real training data."
    )

    rng = np.random.default_rng(42)
    HOURLY_SHAPE = np.array([
        0.40, 0.35, 0.32, 0.30, 0.32, 0.45,
        0.70, 0.90, 0.95, 0.85, 0.75, 0.70,
        0.65, 0.62, 0.60, 0.63, 0.68, 0.78,
        0.95, 1.00, 0.98, 0.90, 0.78, 0.60,
    ])

    timestamps = pd.date_range(
        start=(datetime.utcnow() - timedelta(days=365 * YEARS_BACK)).strftime("%Y-%m-%d"),
        end=datetime.utcnow().strftime("%Y-%m-%d"),
        freq="h",
    )

    load, temp = [], []
    for ts in timestamps:
        shape    = HOURLY_SHAPE[ts.hour]
        seasonal = 1.08 if ts.month in {6, 7, 8, 9, 12, 1} else 1.0
        weekend  = 0.90 if ts.dayofweek >= 5 else 1.0
        lv = GATSIBO_BASE_MW + (GATSIBO_PEAK_MW - GATSIBO_BASE_MW) * shape * seasonal * weekend
        load.append(max(GATSIBO_BASE_MW * 0.5, lv + rng.normal(0, 0.8)))
        temp.append(22.0 if seasonal > 1 else 19.0 + 5 * np.sin((ts.hour - 6) * np.pi / 12) + rng.normal(0, 1.2))

    df = pd.DataFrame({"ds": timestamps, "load_mw": np.round(load, 3), "temp_c": np.round(temp, 2)})
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    logger.info("[eia] Fallback synthetic data saved → %s (%d rows)", output_path, len(df))
    return df


# ── Entry point ───────────────────────────────────────────────────────────────

def generate(output_path: Path = OUTPUT_PATH, use_cache: bool = True) -> pd.DataFrame:
    """
    Unified entry point used by startup.sh, main.py, forecaster.py, lstm_model.py.
    Uses real EIA data if key is present, falls back to synthetic otherwise.
    """
    if EIA_API_KEY:
        return load_and_prepare(output_path=output_path, use_cache=use_cache)
    return generate_fallback(output_path=output_path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    df = generate()
    print(f"\nDataset ready: {len(df):,} rows")
    print(df.describe())
    print(f"\nSample:\n{df.head(5)}")
