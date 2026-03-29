"""
main.py
FastAPI application — Smart Energy Consumption Optimizer
Texas ERCOT Grid, United States

Endpoints:
  GET  /                          → health check
  GET  /api/forecast              → 24-hour load forecast (model=prophet|lstm)
  GET  /api/forecast/history      → last N historical predictions
  POST /api/train                 → retrain the selected model
  POST /webhook/controller        → mock smart-grid controller receiver
  GET  /api/alerts                → recent alert log
  GET  /api/weather               → current weather conditions
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
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

# ── In-memory forecast cache (key: "model:hours", value: {data, ts}) ──────────
_forecast_cache: dict = {}
FORECAST_CACHE_TTL = 300  # seconds (5 minutes)


# ── App lifecycle ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Smart Energy Optimizer API…")
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("Shutting down.")


app = FastAPI(
    title="Texas ERCOT Smart Energy Optimizer",
    description="Predicts peak electricity load and triggers load-shedding schedules for the Texas ERCOT grid, United States.",
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
        app.mount("/simple", StaticFiles(directory=simple_path, html=True), name="simple_frontend")
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

@app.get("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/simple/", status_code=307)


@app.get("/app", include_in_schema=False)
def app_redirect_root():
    return RedirectResponse(url="/simple/", status_code=307)


@app.get("/app/advanced", include_in_schema=False)
def app_advanced_redirect_root():
    return RedirectResponse(url="/advanced/", status_code=307)


@app.get("/app/advanced/{path:path}", include_in_schema=False)
def app_advanced_redirect_path(path: str):
    target = f"/advanced/{path}" if path else "/advanced/"
    return RedirectResponse(url=target, status_code=307)


@app.get("/app/{path:path}", include_in_schema=False)
def app_redirect_path(path: str):
    target = f"/simple/{path}" if path else "/simple/"
    return RedirectResponse(url=target, status_code=307)


@app.get("/technical", include_in_schema=False)
def technical_redirect_root():
    return RedirectResponse(url="/advanced/", status_code=307)


@app.get("/technical/{path:path}", include_in_schema=False)
def technical_redirect_path(path: str):
    target = f"/advanced/{path}" if path else "/advanced/"
    return RedirectResponse(url=target, status_code=307)


@app.get("/health", tags=["Health"])
def health():
    return {
        "status":  "ok",
        "service": "Texas ERCOT Smart Energy Optimizer",
        "time":    datetime.utcnow().isoformat(),
        "dashboards": {
            "simple": "http://localhost:8000/app",
            "advanced": "http://localhost:8000/advanced (technical view)",
        },
    }


@app.get("/api/forecast", tags=["Forecast"])
async def get_forecast(
    hours: int = Query(default=24, ge=1, le=168, description="Hours ahead to forecast"),
    model: Literal["prophet", "lstm"] = Query(default="prophet"),
):
    """
    Return an hourly load forecast.
    Each item contains: timestamp, predicted_mw, lower_mw, upper_mw, alert.
    Responses are cached for 5 minutes per (model, hours) combination.
    """
    cache_key = f"{model}:{hours}"
    now = time.monotonic()

    # ── Serve from cache if still fresh ───────────────────────────────────────
    cached = _forecast_cache.get(cache_key)
    if cached and (now - cached["ts"]) < FORECAST_CACHE_TTL:
        logger.info("[api] Forecast cache HIT for %s", cache_key)
        return cached["data"]

    # ── Run prediction in a thread so we don't block the event loop ───────────
    def _run_prediction():
        if model == "prophet":
            from forecaster import ProphetForecaster
            fc = ProphetForecaster()
        else:
            from lstm_model import LSTMForecaster
            fc = LSTMForecaster()

        if fc.model is None:
            # Raise a plain exception — HTTPException cannot propagate from a thread
            raise RuntimeError(
                f"Model '{model}' has not been trained yet. POST /api/train first."
            )
        return fc.predict(hours=hours)

    try:
        predictions = await asyncio.to_thread(_run_prediction)
    except ModuleNotFoundError as exc:
        if exc.name == "tensorflow":
            logger.warning("[api] LSTM requested but TensorFlow is not installed.")
            raise HTTPException(
                status_code=503,
                detail="LSTM forecasting requires TensorFlow. Install backend dependencies or use model=prophet.",
            )
        raise
    except RuntimeError as exc:
        # Raised by _run_prediction when the model isn't trained yet
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error("[api] Forecast error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    # ── Webhook check — fire-and-forget, never blocks the response ─────────────
    async def _dispatch():
        try:
            result = await asyncio.to_thread(check_and_dispatch, predictions)
            if result:
                alert_log.append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "peak_mw":   result["peak"]["predicted_mw"],
                    "schedule":  result["shed_schedule"],
                    "model":     model,
                })
        except Exception as wh_exc:
            logger.error("[api] Webhook check error: %s", wh_exc)

    asyncio.create_task(_dispatch())

    payload = {
        "district":     "Texas ERCOT, United States",
        "model":        model,
        "hours":        hours,
        "threshold_mw": float(os.getenv("LOAD_THRESHOLD_MW", "20.0")),
        "generated_at": datetime.utcnow().isoformat(),
        "forecast":     predictions,
    }

    # ── Store in cache ─────────────────────────────────────────────────────────
    _forecast_cache[cache_key] = {"data": payload, "ts": now}
    logger.info("[api] Forecast cache MISS — computed and cached for %s", cache_key)

    return payload


@app.post("/api/train", tags=["Model"])
async def train_model(req: TrainRequest):
    """
    Trigger model retraining in a background thread so the event loop is not blocked.
    Training can take 30–120 s for LSTM.
    """
    def _do_train():
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

    try:
        await asyncio.to_thread(_do_train)
        return {"status": "trained", "model": req.model, "at": datetime.utcnow().isoformat()}
    except Exception as exc:
        logger.error("[api] Training error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/evaluation/summary", tags=["Model"])
def model_evaluation_summary():
    """
    Return saved model-evaluation metrics with a recommendation and plain-language explanation.
    Data source: backend/data/eval_results.csv (generated by evaluate.py)
    Texas ERCOT load data — sourced from US EIA ERCO series.
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


# Global flag to avoid running two evaluations simultaneously
_eval_running: bool = False


@app.post("/api/evaluation/run", tags=["Model"])
async def run_model_evaluation():
    """
    Execute evaluate.py in a background asyncio task.
    Does not block — returns immediately.
    Check progress with GET /api/evaluation/status
    """
    global _eval_running

    if _eval_running:
        return {
            "status": "already_running",
            "message": "An evaluation is already in progress.",
        }

    async def _bg_eval():
        global _eval_running
        _eval_running = True
        try:
            def _run():
                import runpy
                backend_dir = Path(__file__).parent
                # evaluate.py writes backend/data/eval_results.csv
                runpy.run_path(str(backend_dir / "evaluate.py"), run_name="__main__")

            await asyncio.to_thread(_run)
            logger.info("[eval] Background evaluation completed.")
        except Exception as exc:
            logger.error("[eval] Background evaluation failed: %s", exc, exc_info=True)
        finally:
            _eval_running = False

    asyncio.create_task(_bg_eval())
    return {
        "status": "started",
        "message": "Evaluation started in the background. Poll GET /api/evaluation/status for results.",
    }


@app.get("/api/evaluation/status", tags=["Model"])
def get_evaluation_status():
    """
    Check whether the background evaluation is running and whether results are available.
    """
    eval_path = Path(__file__).parent / "data" / "eval_results.csv"

    status_info: dict = {
        "is_running":       _eval_running,
        "eval_file_exists": eval_path.exists(),
    }

    if eval_path.exists():
        mtime = eval_path.stat().st_mtime
        age_seconds = time.time() - mtime
        status_info["eval_file_age_seconds"] = round(age_seconds, 1)
        status_info["just_completed"] = age_seconds < 300

    if _eval_running:
        return {"status": "running", "message": "Evaluation is in progress.", **status_info}

    if status_info.get("just_completed"):
        try:
            summary = model_evaluation_summary()
            return {
                "status": "completed",
                "message": "Evaluation just completed.",
                "summary": summary,
                **status_info,
            }
        except Exception:
            return {
                "status": "completed_with_errors",
                "message": "Evaluation completed but results could not be read.",
                **status_info,
            }

    return {"status": "idle", "message": "No evaluation currently running.", **status_info}


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
    """Return current weather conditions for Dallas, TX from Open-Meteo."""
    try:
        from weather import fetch_forecast, weather_to_dataframe
        # Short timeout — weather must never hang the dashboard
        raw = fetch_forecast(days_ahead=1)
        df  = weather_to_dataframe(raw).reset_index()
        now = df.iloc[0]
        return {
            "location":    "Dallas, TX — ERCOT Grid",
            "timestamp":   str(now["ds"]),
            "temperature": round(float(now.get("temperature_2m", 0)), 1),
            "humidity":    round(float(now.get("relative_humidity_2m", 0)), 1),
            "wind_speed":  round(float(now.get("wind_speed_10m", 0)), 1),
            "solar_rad":   round(float(now.get("shortwave_radiation", 0)), 1),
            "source":      "Open-Meteo (open-meteo.com)",
        }
    except Exception as exc:
        logger.warning("[api] Weather fetch failed (non-fatal): %s", exc)
        return {
            "location":    "Dallas, TX — ERCOT Grid",
            "timestamp":   datetime.utcnow().isoformat(),
            "temperature": None,
            "humidity":    None,
            "wind_speed":  None,
            "solar_rad":   None,
            "source":      "Open-Meteo (unavailable)",
            "error":       str(exc),
        }
