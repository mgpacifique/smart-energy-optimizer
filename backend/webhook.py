"""
webhook.py
Threshold checker and webhook dispatcher.

When a forecast exceeds LOAD_THRESHOLD_MW:
  1. Generate a zone-based load-shedding schedule for Gatsibo
  2. POST the payload to the mock smart-grid controller endpoint
  3. Fire alert notifications (SMS + email) via alerts.py

Gatsby zones (illustrative — based on Gatsibo sector layout):
  Zone A: Gatsibo Town Centre
  Zone B: Rugarama Sector
  Zone C: Kiramuruzi Sector
  Zone D: Gitoki Sector
  Zone E: Mukarange Sector
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import httpx

from alerts import dispatch_alert

logger = logging.getLogger(__name__)

LOAD_THRESHOLD_MW    = float(os.getenv("LOAD_THRESHOLD_MW", "20.0"))
CONTROLLER_URL       = os.getenv("CONTROLLER_WEBHOOK_URL", "http://localhost:8000/webhook/controller")
WEBHOOK_SECRET       = os.getenv("WEBHOOK_SECRET", "dev-secret-change-me")

# Gatsibo district zones for rotation-based load shedding
ZONES = [
    {"zone": "Zone A – Gatsibo Town Centre",  "priority": 1},
    {"zone": "Zone B – Rugarama Sector",      "priority": 2},
    {"zone": "Zone C – Kiramuruzi Sector",    "priority": 3},
    {"zone": "Zone D – Gitoki Sector",        "priority": 4},
    {"zone": "Zone E – Mukarange Sector",     "priority": 5},
]


def build_shed_schedule(
    predicted_mw: float,
    peak_timestamp: str,
    threshold_mw: float = LOAD_THRESHOLD_MW,
) -> list[dict]:
    """
    Generate a proportional load-shedding schedule.

    Strategy:
      - Excess MW = predicted - threshold
      - Each zone handles ~20% of total load
      - Zones are shed in priority order until excess is covered
      - Shedding window is 2 hours centred on the predicted peak
    """
    excess_mw   = max(0.0, predicted_mw - threshold_mw)
    load_per_zone = predicted_mw / len(ZONES)

    peak_dt = datetime.fromisoformat(peak_timestamp)
    shed_start = peak_dt - timedelta(hours=1)

    schedule = []
    remaining = excess_mw

    for zone_info in ZONES:
        if remaining <= 0:
            break

        zone_load    = min(load_per_zone, remaining)
        duration_hrs = max(0.5, round(zone_load / load_per_zone * 2, 1))
        end_dt       = shed_start + timedelta(hours=duration_hrs)

        schedule.append({
            "zone":         zone_info["zone"],
            "priority":     zone_info["priority"],
            "start":        shed_start.strftime("%H:%M"),
            "end":          end_dt.strftime("%H:%M"),
            "duration_hrs": duration_hrs,
            "load_mw":      round(zone_load, 2),
        })

        remaining   -= zone_load
        shed_start   = end_dt   # next zone starts where previous ends

    return schedule


def _build_payload(
    predictions: list[dict],
    triggered_at: Optional[str] = None,
) -> dict:
    """
    Assemble the full webhook POST payload.
    """
    peak = max(predictions, key=lambda p: p["predicted_mw"])
    schedule = build_shed_schedule(peak["predicted_mw"], peak["timestamp"])

    return {
        "event":         "peak_load_alert",
        "triggered_at":  triggered_at or datetime.utcnow().isoformat(),
        "district":      "Gatsibo, Eastern Province, Rwanda",
        "threshold_mw":  LOAD_THRESHOLD_MW,
        "peak": {
            "timestamp":    peak["timestamp"],
            "predicted_mw": peak["predicted_mw"],
            "model":        peak.get("model", "unknown"),
        },
        "forecast_window": predictions,
        "shed_schedule":   schedule,
        "secret":          WEBHOOK_SECRET,
    }


def check_and_dispatch(predictions: list[dict]) -> Optional[dict]:
    """
    Evaluate a list of forecast dicts. If any hour exceeds the threshold,
    build a payload and POST to the controller endpoint.

    Returns the payload dict if dispatched, None otherwise.
    """
    alerts = [p for p in predictions if p.get("alert") or p["predicted_mw"] >= LOAD_THRESHOLD_MW]

    if not alerts:
        logger.info(
            "[webhook] No threshold breaches in forecast (max=%.2f MW, threshold=%.1f MW).",
            max(p["predicted_mw"] for p in predictions),
            LOAD_THRESHOLD_MW,
        )
        return None

    peak = max(alerts, key=lambda p: p["predicted_mw"])
    logger.warning(
        "[webhook] ALERT — peak forecast %.2f MW at %s exceeds threshold %.1f MW.",
        peak["predicted_mw"], peak["timestamp"], LOAD_THRESHOLD_MW,
    )

    payload = _build_payload(predictions)

    # ── POST to mock controller ───────────────────────────────────────────────
    try:
        resp = httpx.post(
            CONTROLLER_URL,
            json=payload,
            headers={"X-Webhook-Secret": WEBHOOK_SECRET},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("[webhook] Controller acknowledged: HTTP %d", resp.status_code)
    except httpx.HTTPError as exc:
        logger.error("[webhook] Controller POST failed: %s", exc)

    # ── Fire SMS + email alerts ───────────────────────────────────────────────
    dispatch_alert(
        predicted_mw=peak["predicted_mw"],
        timestamp=peak["timestamp"],
        schedule=payload["shed_schedule"],
    )

    return payload


def receive_webhook(payload: dict) -> dict:
    """
    Handler for incoming webhook POSTs at /webhook/controller.
    Validates secret, stores the event, returns acknowledgement.
    """
    if payload.get("secret") != WEBHOOK_SECRET:
        logger.warning("[webhook] Rejected payload — invalid secret.")
        return {"status": "rejected", "reason": "invalid secret"}

    logger.info(
        "[webhook] Controller received event '%s' — peak %.2f MW",
        payload.get("event"), payload.get("peak", {}).get("predicted_mw", 0),
    )
    return {
        "status":        "acknowledged",
        "event":         payload.get("event"),
        "shed_schedule": payload.get("shed_schedule", []),
    }
