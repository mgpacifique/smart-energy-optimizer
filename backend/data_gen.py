"""
data_gen.py
Generates synthetic hourly electricity load data for Gatsibo District, Rwanda.
Used for training Prophet and LSTM models before real RURA/REG data is available.

Load profile is modelled on typical East African district patterns:
  - Morning peak  : 06:00 – 09:00
  - Evening peak  : 18:00 – 22:00
  - Midday trough : 12:00 – 15:00
  - Seasonal drift: slightly higher demand in dry season (Jun–Sep)
  - Weekend effect: ~10% lower overall demand
"""

import numpy as np
import pandas as pd
from pathlib import Path

# ── Constants ────────────────────────────────────────────────────────────────
RANDOM_SEED   = 42
BASE_LOAD_MW  = 12.0       # Gatsibo baseline (approximate district scale)
PEAK_LOAD_MW  = 22.0       # Maximum realistic peak
NOISE_STD     = 0.8        # Gaussian noise std (MW)
START_DATE    = "2022-01-01"
END_DATE      = "2024-12-31"
OUTPUT_PATH   = Path(__file__).parent / "data" / "gatsibo_load.csv"

# Hourly load shape (index 0–23), normalised to [0, 1]
HOURLY_SHAPE = np.array([
    0.40, 0.35, 0.32, 0.30, 0.32, 0.45,   # 00–05  night / early morning
    0.70, 0.90, 0.95, 0.85, 0.75, 0.70,   # 06–11  morning peak
    0.65, 0.62, 0.60, 0.63, 0.68, 0.78,   # 12–17  midday trough → rising
    0.95, 1.00, 0.98, 0.90, 0.78, 0.60,   # 18–23  evening peak
])


def _seasonal_factor(month: int) -> float:
    """
    Rwanda has two dry seasons (Jun–Sep, Dec–Jan).
    Higher temperatures → more cooling load → slightly higher demand.
    """
    dry_months = {6, 7, 8, 9, 12, 1}
    return 1.08 if month in dry_months else 1.0


def _weekend_factor(dayofweek: int) -> float:
    """Saturday (5) and Sunday (6) carry ~10% lower industrial/commercial load."""
    return 0.90 if dayofweek >= 5 else 1.0


def generate(
    start: str = START_DATE,
    end: str = END_DATE,
    seed: int = RANDOM_SEED,
    output_path: Path = OUTPUT_PATH,
) -> pd.DataFrame:
    """
    Build a synthetic hourly load DataFrame and save it to CSV.

    Returns
    -------
    pd.DataFrame with columns: ds (datetime), load_mw (float), temp_c (float)
    """
    rng = np.random.default_rng(seed)

    timestamps = pd.date_range(start=start, end=end, freq="h")
    n = len(timestamps)

    load = np.zeros(n)
    temp = np.zeros(n)

    for i, ts in enumerate(timestamps):
        shape    = HOURLY_SHAPE[ts.hour]
        seasonal = _seasonal_factor(ts.month)
        weekend  = _weekend_factor(ts.dayofweek)

        # Base load
        load[i] = (
            BASE_LOAD_MW
            + (PEAK_LOAD_MW - BASE_LOAD_MW) * shape * seasonal * weekend
            + rng.normal(0, NOISE_STD)
        )
        load[i] = max(BASE_LOAD_MW * 0.5, load[i])   # floor at 50% base

        # Synthetic temperature (Rwanda ~15–28°C, varies by hour + season)
        temp_base = 22.0 if _seasonal_factor(ts.month) > 1 else 19.0
        temp[i] = (
            temp_base
            + 5 * np.sin((ts.hour - 6) * np.pi / 12)    # diurnal swing
            + rng.normal(0, 1.2)
        )

    df = pd.DataFrame({
        "ds":      timestamps,
        "load_mw": np.round(load, 3),
        "temp_c":  np.round(temp, 2),
    })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    print(f"[data_gen] Saved {len(df):,} rows → {output_path}")
    return df


if __name__ == "__main__":
    df = generate()
    print(df.describe())
    print(f"\nFirst 5 rows:\n{df.head()}")
    print(f"\nPeak load: {df['load_mw'].max():.2f} MW at {df.loc[df['load_mw'].idxmax(), 'ds']}")
