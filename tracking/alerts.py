"""
Scanner alert system — email delivery + DB persistence.

Flow:
  1. scanner finds a stock above ALERT_MIN_SCORE
  2. call maybe_send_alert(symbol, ...)
  3. we dedupe (no repeat alert for same symbol within 24h)
  4. save to scanner_alerts table
  5. send email if SMTP is configured
"""

import logging
import smtplib
import ssl
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import (
    ALERT_EMAIL_TO, ALERT_EMAIL_FROM,
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    ALERT_MIN_SCORE,
)
from database.predictions import save_scanner_alert, get_recent_alerts

log = logging.getLogger(__name__)

_EMAIL_CONFIGURED = all([ALERT_EMAIL_TO, ALERT_EMAIL_FROM, SMTP_USER, SMTP_PASSWORD])


# ── Public API ────────────────────────────────────────────────────────────────

def maybe_send_alert(
    symbol: str,
    company_name: str,
    score: float,
    convergence_count: int,
    signals: list[str],
    price_usd: float | None,
) -> bool:
    """
    Save alert to DB and optionally email. Returns True if alert was fired.
    Deduplicates: no repeat alert for the same symbol within 24 hours.
    """
    if score < ALERT_MIN_SCORE:
        return False

    if _was_alerted_recently(symbol):
        log.debug("[ALERT] Skipping %s — alerted within last 24h", symbol)
        return False

    emailed = False
    if _EMAIL_CONFIGURED:
        try:
            _send_email_alert(symbol, company_name, score, convergence_count, signals, price_usd)
            emailed = True
            log.info("[ALERT] Email sent for %s (score=%.1f)", symbol, score)
        except Exception as exc:
            log.warning("[ALERT] Email failed for %s: %s", symbol, exc)

    save_scanner_alert(
        symbol=symbol,
        company_name=company_name,
        score=score,
        convergence_count=convergence_count,
        signals=signals,
        price_usd=price_usd,
        email_sent=emailed,
    )
    log.info("[ALERT] Saved alert: %s score=%.1f email=%s", symbol, score, emailed)
    return True


def get_alert_status() -> dict:
    """Summary for the analytics page."""
    recent = get_recent_alerts(limit=50)
    emailed = sum(1 for a in recent if a.get("email_sent"))
    return {
        "email_configured": _EMAIL_CONFIGURED,
        "smtp_host": SMTP_HOST if _EMAIL_CONFIGURED else "",
        "alert_min_score": ALERT_MIN_SCORE,
        "recent_count": len(recent),
        "emailed_count": emailed,
        "recent_alerts": recent[:10],
    }


# ── Dedupe ────────────────────────────────────────────────────────────────────

def _was_alerted_recently(symbol: str, hours: int = 24) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    recent = get_recent_alerts(limit=200)
    for a in recent:
        if a.get("symbol", "").upper() == symbol.upper():
            if a.get("created_at", "") > cutoff:
                return True
    return False


# ── Email delivery ────────────────────────────────────────────────────────────

def _send_email_alert(
    symbol: str,
    company_name: str,
    score: float,
    convergence_count: int,
    signals: list[str],
    price_usd: float | None,
) -> None:
    subject = f"[AI Trader] Scanner Alert: {symbol} — Score {score:.0f}/100"

    signals_html = "".join(
        f"<li>{s.replace('_', ' ').title()}</li>" for s in signals
    )
    price_str = f"${price_usd:.2f}" if price_usd else "N/A"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html_body = f"""
<html><body style="font-family:sans-serif;color:#1a1a2e;background:#f8f9fa;padding:24px">
<div style="max-width:540px;margin:auto;background:#fff;border-radius:10px;
            padding:28px;box-shadow:0 2px 8px rgba(0,0,0,.12)">
  <h2 style="color:#1a6b3c;margin-top:0">
    📈 Scanner Opportunity: {symbol}
  </h2>
  <p style="font-size:1.05em"><strong>{company_name or symbol}</strong></p>
  <table style="width:100%;border-collapse:collapse;margin-bottom:16px">
    <tr>
      <td style="padding:6px 8px;background:#f0faf4;border-radius:4px">
        <strong>Score</strong>
      </td>
      <td style="padding:6px 8px;font-size:1.2em;color:#1a6b3c;font-weight:700">
        {score:.0f} / 100
      </td>
    </tr>
    <tr>
      <td style="padding:6px 8px">Converging signals</td>
      <td style="padding:6px 8px">{convergence_count}</td>
    </tr>
    <tr>
      <td style="padding:6px 8px;background:#f0faf4">Price (USD)</td>
      <td style="padding:6px 8px;background:#f0faf4">{price_str}</td>
    </tr>
  </table>
  <h4 style="margin-bottom:6px">Signals fired:</h4>
  <ul style="margin:0 0 16px 0">{signals_html}</ul>
  <p style="color:#888;font-size:.85em">
    Generated {now_str} by AI Paper Trader (paper trading only — no real money)
  </p>
</div>
</body></html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = ALERT_EMAIL_FROM
    msg["To"]      = ALERT_EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
        srv.ehlo()
        srv.starttls(context=ctx)
        srv.login(SMTP_USER, SMTP_PASSWORD)
        srv.sendmail(ALERT_EMAIL_FROM, ALERT_EMAIL_TO, msg.as_string())
