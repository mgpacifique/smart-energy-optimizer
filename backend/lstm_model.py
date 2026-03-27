"""
lstm_model.py
LSTM-based hourly energy load forecasting for Gatsibo District.

Architecture:
  Input  → LSTM(64) → Dropout(0.2) → LSTM(32) → Dense(1)
  Window : 24 hours of history → predict next hour
  Features: load_mw, temp_c, hour_sin, hour_cos, weekday

Usage:
    model = LSTMForecaster()
    model.train()
    result = model.predict(hours=24)
"""

import logging
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODEL_PATH  = Path(__file__).parent / "models" / "lstm_model.keras"
SCALER_PATH = Path(__file__).parent / "models" / "lstm_scaler.pkl"
DATA_PATH   = Path(__file__).parent / "data"   / "gatsibo_load.csv"

SEQUENCE_LEN     = 24       # hours of history used per prediction
FEATURES         = ["load_mw", "temp_c", "hour_sin", "hour_cos", "weekday"]
LOAD_THRESHOLD_MW = 20.0
EPOCHS           = 30
BATCH_SIZE       = 64


def _add_time_features(df: pd.DataFrame) -> pd.DataFrame:
    """Encode cyclical time features so the model understands periodicity."""
    df = df.copy()
    df["hour_sin"] = np.sin(2 * np.pi * df["ds"].dt.hour / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["ds"].dt.hour / 24)
    df["weekday"]  = df["ds"].dt.dayofweek / 6.0   # normalised 0–1
    return df


def _make_sequences(data: np.ndarray, seq_len: int):
    """Slide a window across `data` to produce (X, y) pairs."""
    X, y = [], []
    for i in range(len(data) - seq_len):
        X.append(data[i : i + seq_len])
        y.append(data[i + seq_len, 0])   # column 0 = load_mw
    return np.array(X), np.array(y)


class LSTMForecaster:
    def __init__(self):
        self.model  = None
        self.scaler = None
        self._load_if_exists()

    # ── Training ──────────────────────────────────────────────────────────────

    def train(self, data_path: Path = DATA_PATH) -> None:
        """
        Train the LSTM on historical load + weather data.
        """
        from sklearn.preprocessing import MinMaxScaler
        from tensorflow import keras
        from tensorflow.keras import layers

        logger.info("[lstm] Loading training data…")
        df = pd.read_csv(data_path, parse_dates=["ds"])
        df = _add_time_features(df).dropna()

        feature_data = df[FEATURES].values.astype(np.float32)

        # Scale all features to [0, 1]
        self.scaler = MinMaxScaler()
        scaled = self.scaler.fit_transform(feature_data)

        X, y = _make_sequences(scaled, SEQUENCE_LEN)

        # Train / validation split (last 10% as validation)
        split = int(len(X) * 0.9)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        logger.info("[lstm] Building model — X_train shape: %s", X_train.shape)

        inputs = keras.Input(shape=(SEQUENCE_LEN, len(FEATURES)))
        x = layers.LSTM(64, return_sequences=True)(inputs)
        x = layers.Dropout(0.2)(x)
        x = layers.LSTM(32)(x)
        x = layers.Dense(1)(x)
        self.model = keras.Model(inputs, x)

        self.model.compile(optimizer="adam", loss="mse", metrics=["mae"])

        callbacks = [
            keras.callbacks.EarlyStopping(patience=5, restore_best_weights=True),
            keras.callbacks.ReduceLROnPlateau(factor=0.5, patience=3, verbose=1),
        ]

        self.model.fit(
            X_train, y_train,
            validation_data=(X_val, y_val),
            epochs=EPOCHS,
            batch_size=BATCH_SIZE,
            callbacks=callbacks,
            verbose=1,
        )

        self._save()
        logger.info("[lstm] Training complete.")

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict(self, hours: int = 24) -> list[dict]:
        """
        Predict the next `hours` hours using the last SEQUENCE_LEN rows as seed.

        Uses autoregressive inference: each predicted value feeds the next step.
        """
        if self.model is None or self.scaler is None:
            raise RuntimeError("LSTM not trained. Call train() first.")

        from weather import fetch_forecast, weather_to_dataframe

        # Load last SEQUENCE_LEN rows of historical data as seed
        df = pd.read_csv(DATA_PATH, parse_dates=["ds"])
        df = _add_time_features(df).dropna()
        seed = self.scaler.transform(df[FEATURES].values[-SEQUENCE_LEN:].astype(np.float32))

        # Fetch weather forecast for future time features
        try:
            raw   = fetch_forecast(days_ahead=max(1, hours // 24 + 2))
            wdf   = weather_to_dataframe(raw).reset_index()
            temps = wdf["temperature_2m"].values[:hours]
        except Exception:
            logger.warning("[lstm] Weather unavailable — using fallback temperature.")
            temps = np.full(hours, 21.5)

        future_times = pd.date_range(
            start=df["ds"].iloc[-1] + pd.Timedelta(hours=1),
            periods=hours,
            freq="h",
        )

        window  = seed.copy()
        results = []

        for i in range(hours):
            ts = future_times[i]

            # Predict next load (scaled)
            x_input = window[-SEQUENCE_LEN:].reshape(1, SEQUENCE_LEN, len(FEATURES))
            pred_scaled = float(self.model.predict(x_input, verbose=0)[0, 0])

            # Inverse-scale load column only
            dummy = np.zeros((1, len(FEATURES)))
            dummy[0, 0] = pred_scaled
            pred_mw = float(self.scaler.inverse_transform(dummy)[0, 0])
            pred_mw = float(np.clip(pred_mw, 0, 50))

            # Build next row in feature space
            temp  = temps[i] if i < len(temps) else 21.5
            h_sin = np.sin(2 * np.pi * ts.hour / 24)
            h_cos = np.cos(2 * np.pi * ts.hour / 24)
            wday  = ts.dayofweek / 6.0

            next_row_raw = np.array([[pred_mw, temp, h_sin, h_cos, wday]], dtype=np.float32)
            next_row_scaled = self.scaler.transform(next_row_raw)
            window = np.vstack([window[1:], next_row_scaled])

            results.append({
                "timestamp":    ts.isoformat(),
                "predicted_mw": round(pred_mw, 3),
                "lower_mw":     round(max(0, pred_mw * 0.92), 3),
                "upper_mw":     round(pred_mw * 1.08, 3),
                "alert":        pred_mw >= LOAD_THRESHOLD_MW,
                "model":        "lstm",
            })

        return results

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save(self) -> None:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.model.save(MODEL_PATH)
        joblib.dump(self.scaler, SCALER_PATH)
        logger.info("[lstm] Model saved to %s", MODEL_PATH)

    def _load_if_exists(self) -> None:
        if MODEL_PATH.exists() and SCALER_PATH.exists():
            from tensorflow import keras
            self.model  = keras.models.load_model(MODEL_PATH)
            self.scaler = joblib.load(SCALER_PATH)
            logger.info("[lstm] Loaded existing model from %s", MODEL_PATH)


# ── Quick test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from eia_loader import generate

    print("Fetching EIA / generating data…")
    generate()

    lf = LSTMForecaster()
    lf.train()

    print("\n24-hour forecast (LSTM):")
    preds = lf.predict(hours=24)
    for p in preds:
        flag = "  *** ALERT ***" if p["alert"] else ""
        print(f"  {p['timestamp']}  {p['predicted_mw']:6.2f} MW{flag}")
