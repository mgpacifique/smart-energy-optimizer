"""
alerts.py
Sends load-alert notifications via:
  1. Africa's Talking SMS  (primary)
  2. Resend email          (backup / always fires for email recipients)

Environment variables required (see .env.example):
  AT_API_KEY          Africa's Talking API key
  AT_USERNAME         Africa's Talking username (use 'sandbox' for testing)
  AT_SENDER_ID        Shortcode / sender name (optional)
  AT_PHONE_NUMBERS    Comma-separated E.164 numbers e.g. +250788123456,+250789654321

  RESEND_API_KEY      Resend API key
  RESEND_FROM         Sender address e.g. alerts@yourdomain.com
  RESEND_TO           Comma-separated recipient emails
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# ── Africa's Talking SMS ──────────────────────────────────────────────────────

def send_sms_alert(predicted_mw: float, timestamp: str, schedule: list[dict]) -> bool:
    """
    Send an SMS alert via Africa's Talking.

    Parameters
    ----------
    predicted_mw : float       — peak load that triggered the alert
    timestamp    : str         — ISO timestamp of predicted peak
    schedule     : list[dict]  — load-shedding schedule zones

    Returns True if at least one SMS was accepted.
    """
    api_key   = os.getenv("AT_API_KEY")
    username  = os.getenv("AT_USERNAME", "sandbox")
    sender_id = os.getenv("AT_SENDER_ID")
    phones_raw = os.getenv("AT_PHONE_NUMBERS", "")

    if not api_key or not phones_raw:
        logger.warning("[alerts] Africa's Talking not configured — SMS skipped.")
        return False

    phones = [p.strip() for p in phones_raw.split(",") if p.strip()]

    # Build concise SMS body (160-char target)
    zones_text = ", ".join(z["zone"] for z in schedule[:3])
    if len(schedule) > 3:
        zones_text += f" +{len(schedule)-3} more"

    body = (
        f"[GATSIBO ENERGY ALERT]\n"
        f"Predicted peak: {predicted_mw:.1f} MW at {timestamp[:16]}\n"
        f"Load shedding: {zones_text}\n"
        f"Reduce usage where possible."
    )

    try:
        import africastalking
        africastalking.initialize(username=username, api_key=api_key)
        sms = africastalking.SMS

        kwargs = {"message": body, "recipients": phones}
        if sender_id:
            kwargs["sender_id"] = sender_id

        response = sms.send(**kwargs)
        logger.info("[alerts] SMS response: %s", response)
        return True

    except ImportError:
        logger.error("[alerts] africastalking package not installed.")
        return False
    except Exception as exc:
        logger.error("[alerts] SMS send failed: %s", exc)
        return False


# ── Resend Email ──────────────────────────────────────────────────────────────

def send_email_alert(predicted_mw: float, timestamp: str, schedule: list[dict]) -> bool:
    """
    Send an HTML email alert via Resend.

    Returns True if the API accepted the message.
    """
    api_key    = os.getenv("RESEND_API_KEY")
    from_addr  = os.getenv("RESEND_FROM", "alerts@gatsibo-energy.com")
    to_raw     = os.getenv("RESEND_TO", "")

    if not api_key or not to_raw:
        logger.warning("[alerts] Resend not configured — email skipped.")
        return False

    to_addrs = [e.strip() for e in to_raw.split(",") if e.strip()]

    # Build schedule table rows
    rows_html = "".join(
        f"<tr>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee'>{z['zone']}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee'>{z.get('start','')}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee'>{z.get('end','')}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #eee'>{z.get('duration_hrs',0):.1f}h</td>"
        f"</tr>"
        for z in schedule
    )

    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto">
      <div style="background:#DC2626;color:#fff;padding:16px 24px;border-radius:8px 8px 0 0">
        <h2 style="margin:0">&#9889; Gatsibo Grid — Peak Load Alert</h2>
      </div>
      <div style="background:#fff;padding:24px;border:1px solid #e5e7eb;border-top:none">
        <p style="font-size:18px;margin:0 0 8px">
          Predicted peak: <strong>{predicted_mw:.2f} MW</strong>
        </p>
        <p style="color:#6b7280;margin:0 0 20px">Timestamp: {timestamp[:19]}</p>

        <h3 style="margin:0 0 12px;font-size:15px">Recommended load-shedding schedule</h3>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
          <thead>
            <tr style="background:#f9fafb">
              <th style="padding:8px 12px;text-align:left">Zone</th>
              <th style="padding:8px 12px;text-align:left">Start</th>
              <th style="padding:8px 12px;text-align:left">End</th>
              <th style="padding:8px 12px;text-align:left">Duration</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>

        <p style="margin:20px 0 0;font-size:13px;color:#9ca3af">
          This alert was generated automatically by the Gatsibo Smart Energy Optimizer.
          Threshold: 20 MW. Model: Prophet + LSTM ensemble.
        </p>
      </div>
    </div>
    """

    try:
        import resend
        resend.api_key = api_key

        params = resend.Emails.SendParams(
            from_=from_addr,
            to=to_addrs,
            subject=f"[ALERT] Gatsibo grid peak {predicted_mw:.1f} MW forecast — {timestamp[:10]}",
            html=html,
        )
        resp = resend.Emails.send(params)
        logger.info("[alerts] Resend email sent. id=%s", resp.get("id"))
        return True

    except ImportError:
        logger.error("[alerts] resend package not installed.")
        return False
    except Exception as exc:
        logger.error("[alerts] Email send failed: %s", exc)
        return False


# ── Combined dispatcher ───────────────────────────────────────────────────────

def dispatch_alert(
    predicted_mw: float,
    timestamp: str,
    schedule: list[dict],
    channels: Optional[list[str]] = None,
) -> dict:
    """
    Fire all configured alert channels.

    Parameters
    ----------
    channels : list of 'sms' and/or 'email'. Defaults to both.

    Returns a dict with channel → success mapping.
    """
    if channels is None:
        channels = ["sms", "email"]

    results = {}
    if "sms" in channels:
        results["sms"] = send_sms_alert(predicted_mw, timestamp, schedule)
    if "email" in channels:
        results["email"] = send_email_alert(predicted_mw, timestamp, schedule)

    logger.info("[alerts] Dispatch results: %s", results)
    return results
