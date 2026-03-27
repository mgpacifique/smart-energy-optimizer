"""
evaluate.py
Evaluates Prophet and LSTM models on a held-out 20% test set and prints
a side-by-side comparison of MAE, RMSE, and MAPE.

Run this after training both models to decide which one to use in production,
or to justify using an ensemble of both.

Usage:
    cd backend
    python evaluate.py
"""

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.WARNING)

DATA_PATH  = Path(__file__).parent / "data" / "gatsibo_load.csv"
TEST_RATIO = 0.20


# ── Metrics ───────────────────────────────────────────────────────────────────

def mae(actual, predicted):
    return float(np.mean(np.abs(actual - predicted)))

def rmse(actual, predicted):
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))

def mape(actual, predicted):
    mask = actual != 0
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100)


# ── Test set ──────────────────────────────────────────────────────────────────

def load_test_set():
    if not DATA_PATH.exists():
        print("ERROR: No training data found. Run startup.sh first.")
        sys.exit(1)
    df    = pd.read_csv(DATA_PATH, parse_dates=["ds"])
    split = int(len(df) * (1 - TEST_RATIO))
    test  = df.iloc[split:].copy().reset_index(drop=True)
    print(f"Test set: {len(test):,} rows  "
          f"({test['ds'].iloc[0].date()} to {test['ds'].iloc[-1].date()})")
    return test


# ── Prophet ───────────────────────────────────────────────────────────────────

def evaluate_prophet(test_df):
    try:
        from forecaster import ProphetForecaster
        fc = ProphetForecaster()
        if fc.model is None:
            print("  Prophet: not trained — skipping.")
            return None

        future = fc.model.make_future_dataframe(
            periods=len(test_df),
            freq="h",
            include_history=False,
        )

        forecast = fc.model.predict(future)
        preds    = np.clip(forecast["yhat"].values, 0, 50)
        actual   = test_df["load_mw"].values

        return {"model": "Prophet", "mae": mae(actual, preds),
                "rmse": rmse(actual, preds), "mape": mape(actual, preds),
                "preds": preds, "actual": actual}
    except Exception as exc:
        print(f"  Prophet evaluation failed: {exc}")
        return None


# ── LSTM ──────────────────────────────────────────────────────────────────────

def evaluate_lstm(test_df):
    try:
        from lstm_model import LSTMForecaster, SEQUENCE_LEN, FEATURES, _add_time_features
        fc = LSTMForecaster()
        if fc.model is None:
            print("  LSTM: not trained — skipping.")
            return None

        df_full  = pd.read_csv(DATA_PATH, parse_dates=["ds"])
        df_full  = _add_time_features(df_full).dropna()
        split    = int(len(df_full) * (1 - TEST_RATIO))
        seed     = fc.scaler.transform(
            df_full.iloc[split - SEQUENCE_LEN : split][FEATURES].values.astype(np.float32)
        )

        test_feat = _add_time_features(test_df).dropna()
        preds, window = [], seed.copy()

        for i in range(len(test_feat)):
            x_in       = window[-SEQUENCE_LEN:].reshape(1, SEQUENCE_LEN, len(FEATURES))
            pred_scaled = float(fc.model.predict(x_in, verbose=0)[0, 0])
            dummy       = np.zeros((1, len(FEATURES)))
            dummy[0, 0] = pred_scaled
            pred_mw     = float(np.clip(fc.scaler.inverse_transform(dummy)[0, 0], 0, 50))
            preds.append(pred_mw)

            row     = test_feat.iloc[i]
            nxt     = np.array([[pred_mw, row["temp_c"], row["hour_sin"],
                                  row["hour_cos"], row["weekday"]]], dtype=np.float32)
            window  = np.vstack([window[1:], fc.scaler.transform(nxt)])

        preds  = np.array(preds)
        actual = test_feat["load_mw"].values
        return {"model": "LSTM", "mae": mae(actual, preds),
                "rmse": rmse(actual, preds), "mape": mape(actual, preds),
                "preds": preds, "actual": actual}
    except Exception as exc:
        print(f"  LSTM evaluation failed: {exc}")
        return None


# ── Ensemble ──────────────────────────────────────────────────────────────────

def evaluate_ensemble(rp, rl):
    wp, wl  = 1 / rp["mae"], 1 / rl["mae"]
    preds   = (rp["preds"] * wp + rl["preds"] * wl) / (wp + wl)
    actual  = rp["actual"]
    return {"model": "Ensemble", "mae": mae(actual, preds),
            "rmse": rmse(actual, preds), "mape": mape(actual, preds),
            "preds": preds, "actual": actual,
            "weights": {"prophet": round(wp/(wp+wl), 3), "lstm": round(wl/(wp+wl), 3)}}


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(results):
    best_mae  = min(r["mae"]  for r in results)
    best_rmse = min(r["rmse"] for r in results)
    best_mape = min(r["mape"] for r in results)

    sep = "-" * 54
    print(f"\n{sep}")
    print(f"  Model Evaluation -- Gatsibo District")
    print(sep)
    print(f"  {'Model':<12}  {'MAE':>8}  {'RMSE':>8}  {'MAPE':>8}")
    print(sep)

    for r in results:
        m = f"{r['mae']:.3f} MW" + (" *" if r["mae"]  == best_mae  else "  ")
        rs= f"{r['rmse']:.3f} MW"+ (" *" if r["rmse"] == best_rmse else "  ")
        p = f"{r['mape']:.2f}%"  + (" *" if r["mape"] == best_mape else "  ")
        print(f"  {r['model']:<12}  {m:>10}  {rs:>10}  {p:>10}")

    print(sep)
    print("  * = best in column")

    winner = min(results, key=lambda r: r["mae"] + r["rmse"])
    print(f"\n  Recommendation: {winner['model']}  (lowest combined MAE + RMSE)")

    ensemble = next((r for r in results if r["model"] == "Ensemble"), None)
    if ensemble and "weights" in ensemble:
        wp = ensemble["weights"]["prophet"] * 100
        wl = ensemble["weights"]["lstm"] * 100
        print(f"  Ensemble weights -- Prophet: {wp:.1f}%   LSTM: {wl:.1f}%")

    print("\n  MAPE interpretation:")
    for r in results:
        tier = "excellent (<5%)" if r["mape"] < 5 else \
               "acceptable (5-10%)" if r["mape"] < 10 else "needs improvement (>10%)"
        print(f"    {r['model']:<12}  {r['mape']:.2f}%  --  {tier}")
    print()


def save_results(results):
    out = Path(__file__).parent / "data" / "eval_results.csv"
    pd.DataFrame([{k: v for k, v in r.items()
                   if k not in ("preds", "actual", "weights")}
                  for r in results]).to_csv(out, index=False)
    print(f"  Results saved to {out}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\nGatsibo Smart Energy Optimizer -- Model Evaluation")
    print("=" * 54)

    test_df = load_test_set()
    results = []

    print("\nEvaluating Prophet...")
    rp = evaluate_prophet(test_df)
    if rp:
        results.append(rp)
        print(f"  MAE={rp['mae']:.3f}  RMSE={rp['rmse']:.3f}  MAPE={rp['mape']:.2f}%")

    print("Evaluating LSTM...")
    rl = evaluate_lstm(test_df)
    if rl:
        results.append(rl)
        print(f"  MAE={rl['mae']:.3f}  RMSE={rl['rmse']:.3f}  MAPE={rl['mape']:.2f}%")

    if rp and rl:
        print("Computing ensemble...")
        results.append(evaluate_ensemble(rp, rl))

    if not results:
        print("\nNo models evaluated. Train at least one model first:\n"
              "  python forecaster.py\n  python lstm_model.py\n")
        sys.exit(1)

    print_report(results)
    save_results(results)


if __name__ == "__main__":
    main()
