"""
forecaster.py
Prophet-based hourly energy load forecasting for Gatsibo District.

Prophet handles:
  - Daily seasonality  (morning / evening peaks)
  - Weekly seasonality (weekday vs weekend)
  - Yearly seasonality (Rwanda dry / rainy seasons)
  - External regressors: temperature, humidity, solar radiation

Usage:
    model = ProphetForecaster()
    model.train()                       # trains on data/gatsibo_load.csv
    result = model.predict(hours=24)    # returns list of hourly predictions
"""

import logging
import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from prophet import Prophet

from weather import fetch_forecast, weather_to_dataframe

logger = logging.getLogger(__name__)

MODEL_PATH  = Path(__file__).parent / "models" / "prophet_model.pkl"
DATA_PATH   = Path(__file__).parent / "data"   / "gatsibo_load.csv"

# Load threshold (MW) — webhook triggers above this
LOAD_THRESHOLD_MW = 20.0


class ProphetForecaster:
    def __init__(self):
        self.model: Optional[Prophet] = None
        self._load_model_if_exists()

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, data_path: Path = DATA_PATH) -> None:
        """
        Train Prophet on historical load data.
        Expects CSV with columns: ds, load_mw, temp_c
        """
        logger.info("[prophet] Loading training data from %s", data_path)
        df = pd.read_csv(data_path, parse_dates=["ds"])

        # Prophet requires columns named 'ds' and 'y'
        train_df = df.rename(columns={"load_mw": "y"})[["ds", "y", "temp_c"]]
        train_df = train_df.dropna()

        logger.info("[prophet] Training on %d rows…", len(train_df))

        self._ensure_cmdstan_ready()

        try:
            self.model = Prophet(
                daily_seasonality=True,
                weekly_seasonality=True,
                yearly_seasonality=True,
                seasonality_mode="multiplicative",
                changepoint_prior_scale=0.05,
                stan_backend="CMDSTANPY",
            )
        except Exception as exc:
            raise RuntimeError(
                "Prophet could not initialize the Stan backend. "
                "Run: python3 -c \"import cmdstanpy; cmdstanpy.install_cmdstan()\""
            ) from exc

        self.model.add_regressor("temp_c")
        self.model.fit(train_df)

        self._save_model()
        logger.info("[prophet] Training complete. Model saved to %s", MODEL_PATH)

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, hours: int = 24) -> list[dict]:
        """
        Predict load for the next `hours` hours.

        Returns
        -------
        List of dicts: {timestamp, predicted_mw, lower_mw, upper_mw, alert}
        """
        if self.model is None:
            raise RuntimeError("Model not trained. Call train() first.")

        # Build future dataframe
        future = self.model.make_future_dataframe(periods=hours, freq="h")

        # Attach temperature forecast from Open-Meteo
        try:
            raw_weather = fetch_forecast(days_ahead=max(1, hours // 24 + 1))
            weather_df  = weather_to_dataframe(raw_weather).reset_index()
            weather_df  = weather_df.rename(columns={"ds": "ds", "temperature_2m": "temp_c"})
            future = future.merge(
                weather_df[["ds", "temp_c"]],
                on="ds",
                how="left",
            )
        except Exception as exc:
            logger.warning("[prophet] Weather fetch failed (%s) — using mean temp.", exc)
            future["temp_c"] = 21.5   # fallback: Rwanda annual mean

        future["temp_c"] = future["temp_c"].fillna(21.5)

        forecast = self.model.predict(future)

        # Extract the forecast window (future rows only)
        result_df = forecast.tail(hours)[["ds", "yhat", "yhat_lower", "yhat_upper"]]

        results = []
        for _, row in result_df.iterrows():
            predicted = round(float(np.clip(row["yhat"], 0, 50)), 3)
            results.append({
                "timestamp":    row["ds"].isoformat(),
                "predicted_mw": predicted,
                "lower_mw":     round(float(np.clip(row["yhat_lower"], 0, 50)), 3),
                "upper_mw":     round(float(np.clip(row["yhat_upper"], 0, 50)), 3),
                "alert":        predicted >= LOAD_THRESHOLD_MW,
                "model":        "prophet",
            })

        return results

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_model(self) -> None:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(self.model, f)

    def _load_model_if_exists(self) -> None:
        if MODEL_PATH.exists():
            logger.info("[prophet] Loading existing model from %s", MODEL_PATH)
            with open(MODEL_PATH, "rb") as f:
                self.model = pickle.load(f)
        else:
            logger.info("[prophet] No saved model found — call train() first.")

    def _ensure_cmdstan_ready(self) -> None:
        """
        Ensure CmdStan is installed for Prophet's CMDSTANPY backend.
        """
        try:
            import cmdstanpy
        except ImportError as exc:
            raise RuntimeError(
                "cmdstanpy is not installed. Add it to requirements and reinstall dependencies."
            ) from exc

        try:
            cmdstanpy.cmdstan_path()
        except ValueError:
            logger.info("[prophet] CmdStan not found; installing (first run only)…")
            try:
                cmdstanpy.install_cmdstan(verbose=False, progress=True)
                cmdstanpy.cmdstan_path()
            except Exception as exc:
                raise RuntimeError(
                    "Failed to install CmdStan automatically. "
                    "Install system build tools first (make, g++), then run: "
                    "python3 -c \"import cmdstanpy; cmdstanpy.install_cmdstan(overwrite=True)\""
                ) from exc


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from eia_loader import generate

    print("Fetching EIA / generating data…")
    generate()

    fc = ProphetForecaster()
    fc.train()

    print("\n24-hour forecast:")
    preds = fc.predict(hours=24)
    for p in preds:
        flag = " *** ALERT ***" if p["alert"] else ""
        print(f"  {p['timestamp']}  {p['predicted_mw']:6.2f} MW{flag}")
