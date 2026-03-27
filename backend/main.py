"""
main.py
FastAPI application — Smart Energy Consumption Optimizer
Gatsibo District, Rwanda

Endpoints:
  GET  /                          → health check
  GET  /api/forecast              → 24-hour load forecast (model=prophet|lstm)
  GET  /api/forecast/history      → last N historical predictions
  POST /api/train                 → retrain the selected model
  POST /webhook/controller        → mock smart-grid controller receiver
  GET  /api/alerts                → recent alert log
  GET  /api/weather               → current weather conditions
"""

import logging
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scheduler import start_scheduler, stop_scheduler
from webhook import check_and_dispatch, receive_webhook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# In-memory alert log (replace with DB in production)
alert_log: list[dict] = []


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Smart Energy Optimizer API…")
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("Shutting down.")


app = FastAPI(
    title="Gatsibo Smart Energy Optimizer",
    description="Predicts peak electricity load and triggers load-shedding schedules for Gatsibo District, Rwanda.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontends
frontend_base = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(frontend_base):
    # Simple (default) frontend
    simple_path = os.path.join(frontend_base, "simple")
    if os.path.isdir(simple_path):
        app.mount("/app", StaticFiles(directory=simple_path, html=True), name="simple_frontend")
    # Advanced (detailed) frontend
    advanced_path = os.path.join(frontend_base, "advanced")
    if os.path.isdir(advanced_path):
        app.mount("/advanced", StaticFiles(directory=advanced_path, html=True), name="advanced_frontend")


# ── Pydantic models ───────────────────────────────────────────────────────────

class TrainRequest(BaseModel):
    model: Literal["prophet", "lstm"] = "prophet"

class WebhookPayload(BaseModel):
    event:            str
    triggered_at:     str
    district:         str
    threshold_mw:     float
    peak:             dict
    forecast_window:  list[dict]
    shed_schedule:    list[dict]
    secret:           str


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def health():
    return {
        "status":  "ok",
        "service": "Gatsibo Smart Energy Optimizer",
        "time":    datetime.utcnow().isoformat(),
        "dashboards": {
            "simple": "http://localhost:8000/app (for teachers)",
            "advanced": "http://localhost:8000/advanced (technical view)",
        },
    }


@app.get("/api/forecast", tags=["Forecast"])
def get_forecast(
    hours: int = Query(default=24, ge=1, le=168, description="Hours ahead to forecast"),
    model: Literal["prophet", "lstm"] = Query(default="prophet"),
):
    """
    Return an hourly load forecast.
    Each item contains: timestamp, predicted_mw, lower_mw, upper_mw, alert.
    """
    try:
        if model == "prophet":
            from forecaster import ProphetForecaster
            fc = ProphetForecaster()
        else:
            from lstm_model import LSTMForecaster
            fc = LSTMForecaster()

        if fc.model is None:
            raise HTTPException(
                status_code=503,
                detail=f"Model '{model}' has not been trained yet. POST /api/train first.",
            )

        predictions = fc.predict(hours=hours)

        # Run webhook check (non-blocking — errors are logged, not raised)
        try:
            result = check_and_dispatch(predictions)
            if result:
                alert_log.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "peak_mw":   result["peak"]["predicted_mw"],
                    "schedule":  result["shed_schedule"],
                    "model":     model,
                })
        except Exception as wh_exc:
            logger.error("[api] Webhook check error: %s", wh_exc)

        return {
            "district":     "Gatsibo, Eastern Province, Rwanda",
            "model":        model,
            "hours":        hours,
            "threshold_mw": float(os.getenv("LOAD_THRESHOLD_MW", "20.0")),
            "generated_at": datetime.utcnow().isoformat(),
            "forecast":     predictions,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[api] Forecast error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/train", tags=["Model"])
def train_model(req: TrainRequest):
    """Trigger model retraining. Runs synchronously (expect 30–120s for LSTM)."""
    try:
        from eia_loader import generate
        generate()   # refresh / re-fetch EIA data

        if req.model == "prophet":
            from forecaster import ProphetForecaster
            fc = ProphetForecaster()
            fc.train()
        else:
            from lstm_model import LSTMForecaster
            fc = LSTMForecaster()
            fc.train()

        return {"status": "trained", "model": req.model, "at": datetime.utcnow().isoformat()}

    except Exception as exc:
        logger.error("[api] Training error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/evaluation/summary", tags=["Model"])
def model_evaluation_summary():
    """
    Return saved model-evaluation metrics with a recommendation and plain-language explanation.
    Data source: backend/data/eval_results.csv (generated by evaluate.py)
    """
    import pandas as pd

    eval_path = Path(__file__).parent / "data" / "eval_results.csv"
    if not eval_path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                "No evaluation results found. Run 'python evaluate.py' in backend first "
                "to generate backend/data/eval_results.csv."
            ),
        )

    try:
        df = pd.read_csv(eval_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read eval_results.csv: {exc}")

    required_cols = {"model", "mae", "rmse", "mape"}
    if not required_cols.issubset(df.columns):
        raise HTTPException(
            status_code=500,
            detail="Evaluation file is missing required columns: model, mae, rmse, mape.",
        )

    for c in ("mae", "rmse", "mape"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["mae", "rmse", "mape"])

    if df.empty:
        raise HTTPException(status_code=500, detail="Evaluation file has no valid metric rows.")

    # Combined score favours lower forecast error and lower percentage error.
    df["score"] = df["mae"] + df["rmse"] + (df["mape"] / 10.0)
    best = df.loc[df["score"].idxmin()]

    def tier(mape_val: float) -> str:
        if mape_val < 5:
            return "excellent"
        if mape_val < 10:
            return "very good"
        if mape_val < 20:
            return "acceptable"
        return "needs improvement"

    models = []
    for _, row in df.sort_values("score").iterrows():
        models.append(
            {
                "model": str(row["model"]),
                "mae": round(float(row["mae"]), 3),
                "rmse": round(float(row["rmse"]), 3),
                "mape": round(float(row["mape"]), 2),
                "score": round(float(row["score"]), 3),
                "quality": tier(float(row["mape"])),
            }
        )

    winner_name = str(best["model"])
    winner_mape = float(best["mape"])
    explanation = (
        f"{winner_name} is recommended because it has the lowest combined error score. "
        f"Its MAPE is {winner_mape:.2f}%, which is {tier(winner_mape)} for this use case. "
        "Lower MAE/RMSE means the model stays closer to real demand in MW, while lower MAPE "
        "means better percentage accuracy."
    )

    return {
        "generated_at": datetime.fromtimestamp(eval_path.stat().st_mtime).isoformat(),
        "source_file": str(eval_path),
        "recommended_model": winner_name,
        "explanation": explanation,
        "models": models,
        "metric_guide": {
            "mae": "Average absolute error in MW (lower is better).",
            "rmse": "Like MAE, but penalizes bigger misses more strongly (lower is better).",
            "mape": "Average percentage error against actual demand (lower is better).",
        },
    }


@app.post("/api/evaluation/run", tags=["Model"])
def run_model_evaluation():
    """
    Execute evaluate.py and return latest summary metrics.
    """
    backend_dir = Path(__file__).parent
    cmd = [sys.executable, "evaluate.py"]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(backend_dir),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=504,
            detail="Evaluation timed out after 10 minutes. Try again or reduce workload.",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to run evaluation: {exc}")

    stdout_tail = "\n".join((proc.stdout or "").strip().splitlines()[-20:])
    stderr_tail = "\n".join((proc.stderr or "").strip().splitlines()[-20:])

    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "Evaluation script failed.",
                "exit_code": proc.returncode,
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
            },
        )

    summary = model_evaluation_summary()
    return {
        "status": "ok",
        "message": "Evaluation completed and results refreshed.",
        "stdout_tail": stdout_tail,
        "summary": summary,
    }


@app.post("/api/eia/sync", tags=["Data"])
def sync_eia_data(force_refresh: bool = False):
    """
    Pull the latest ERCO demand data from EIA, normalise to Gatsibo scale,
    and retrain the Prophet model on the updated dataset.

    Set force_refresh=true to bypass the local raw-data cache.
    """
    try:
        from eia_loader import generate
        from forecaster import ProphetForecaster

        logger.info("[api] EIA sync triggered (force_refresh=%s).", force_refresh)
        df = generate(use_cache=not force_refresh)

        fc = ProphetForecaster()
        fc.train()

        return {
            "status":   "synced",
            "rows":     len(df),
            "range":    {"from": str(df["ds"].min()), "to": str(df["ds"].max())},
            "load_mw":  {"min": round(float(df["load_mw"].min()), 2),
                         "max": round(float(df["load_mw"].max()), 2)},
            "at":       datetime.utcnow().isoformat(),
            "source":   "EIA ERCO (Texas ERCOT) — normalised to Gatsibo scale",
        }

    except Exception as exc:
        logger.error("[api] EIA sync error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/eia/status", tags=["Data"])
def eia_data_status():
    """Returns metadata about the current training dataset."""
    from pathlib import Path
    import pandas as pd

    data_path = Path(__file__).parent / "data" / "gatsibo_load.csv"
    raw_path  = Path(__file__).parent / "data" / "eia_raw_cache.csv"

    if not data_path.exists():
        return {"status": "no_data", "message": "Run POST /api/eia/sync to fetch data."}

    df = pd.read_csv(data_path, parse_dates=["ds"])
    return {
        "status":      "ready",
        "rows":        len(df),
        "from":        str(df["ds"].min()),
        "to":          str(df["ds"].max()),
        "load_min_mw": round(float(df["load_mw"].min()), 2),
        "load_max_mw": round(float(df["load_mw"].max()), 2),
        "source":      "EIA ERCO" if raw_path.exists() else "synthetic fallback",
        "cache_exists": raw_path.exists(),
    }


@app.post("/webhook/controller", tags=["Webhook"])
def controller_endpoint(payload: WebhookPayload):
    """
    Mock smart-grid controller endpoint.
    Receives webhook POSTs from the threshold checker.
    """
    result = receive_webhook(payload.model_dump())
    if result["status"] == "rejected":
        raise HTTPException(status_code=403, detail="Invalid webhook secret.")
    return result


@app.get("/api/alerts", tags=["Alerts"])
def get_alerts(limit: int = Query(default=20, ge=1, le=100)):
    """Return the most recent alert events."""
    return {
        "count":  len(alert_log),
        "alerts": alert_log[-limit:][::-1],   # newest first
    }


@app.get("/api/weather", tags=["Weather"])
def get_weather():
    """Return current weather conditions for Gatsibo from Open-Meteo."""
    try:
        from weather import fetch_forecast, weather_to_dataframe
        raw = fetch_forecast(days_ahead=1)
        df  = weather_to_dataframe(raw).reset_index()
        now = df.iloc[0]
        return {
            "location":    "Gatsibo, Rwanda",
            "timestamp":   str(now["ds"]),
            "temperature": round(float(now.get("temperature_2m", 0)), 1),
            "humidity":    round(float(now.get("relative_humidity_2m", 0)), 1),
            "wind_speed":  round(float(now.get("wind_speed_10m", 0)), 1),
            "solar_rad":   round(float(now.get("shortwave_radiation", 0)), 1),
            "source":      "Open-Meteo (open-meteo.com)",
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Weather service unavailable: {exc}")
