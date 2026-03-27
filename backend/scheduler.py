"""
scheduler.py
Background scheduler that runs an hourly forecast and fires
the webhook check automatically.

Uses APScheduler with AsyncIOScheduler so it runs inside the
FastAPI event loop without blocking.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _run_forecast_job():
    """Hourly job: predict → check threshold → dispatch if needed."""
    logger.info("[scheduler] Running scheduled forecast job…")
    try:
        from forecaster import ProphetForecaster
        from webhook import check_and_dispatch

        fc    = ProphetForecaster()
        preds = fc.predict(hours=24)
        result = check_and_dispatch(preds)

        if result:
            logger.info("[scheduler] Alert dispatched for peak %.2f MW.", result["peak"]["predicted_mw"])
        else:
            logger.info("[scheduler] No alert — all values within threshold.")

    except Exception as exc:
        logger.error("[scheduler] Forecast job failed: %s", exc, exc_info=True)


def start_scheduler():
    """Register jobs and start the scheduler."""
    scheduler.add_job(
        _run_forecast_job,
        trigger=IntervalTrigger(hours=1),
        id="hourly_forecast",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info("[scheduler] Scheduler started — hourly forecast job registered.")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] Scheduler stopped.")
