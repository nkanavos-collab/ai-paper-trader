"""
SEC EDGAR 8-K filings — free, no API key required.
Uses the EDGAR submissions API and company tickers mapping.
Rate limit: 10 requests/second per SEC policy.
"""
import json
import logging
import time
from datetime import datetime, timezone, timedelta
import requests

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "AI-Paper-Trader research@example.com",  # SEC requires a UA
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov",
}
_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

_ticker_cik_cache: dict[str, int] = {}
_ticker_map_ts: float = 0.0
_submissions_cache: dict[str, tuple[dict, float]] = {}

CACHE_SECONDS = 3600 * 6


def _load_ticker_map() -> None:
    global _ticker_map_ts
    if time.time() - _ticker_map_ts < 3600 * 12:
        return
    try:
        resp = requests.get(
            _TICKER_MAP_URL,
            headers={"User-Agent": "AI-Paper-Trader research@example.com"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            for entry in data.values():
                ticker = str(entry.get("ticker", "")).upper()
                cik = int(entry.get("cik_str", 0))
                if ticker and cik:
                    _ticker_cik_cache[ticker] = cik
            _ticker_map_ts = time.time()
            log.info("EDGAR ticker map loaded: %d tickers", len(_ticker_cik_cache))
    except Exception as exc:
        log.warning("EDGAR ticker map load failed: %s", exc)


def _get_cik(symbol: str) -> int | None:
    _load_ticker_map()
    return _ticker_cik_cache.get(symbol.upper())


def get_recent_8k(symbol: str, days: int = 60) -> dict:
    symbol = symbol.upper()
    cached, ts = _submissions_cache.get(symbol, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False, "filings": []}

    cik = _get_cik(symbol)
    if not cik:
        result["error"] = f"CIK not found for {symbol}"
        _submissions_cache[symbol] = (result, time.time())
        return result

    try:
        url = _SUBMISSIONS_URL.format(cik=cik)
        resp = requests.get(
            url,
            headers=_HEADERS,
            timeout=12,
        )
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            _submissions_cache[symbol] = (result, time.time())
            return result

        data = resp.json()
        filings_data = data.get("filings", {}).get("recent", {})

        forms       = filings_data.get("form", [])
        dates       = filings_data.get("filingDate", [])
        descriptions= filings_data.get("primaryDocument", [])
        accessions  = filings_data.get("accessionNumber", [])
        items_list  = filings_data.get("items", [])

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
        filings = []

        for i, form in enumerate(forms):
            if form not in ("8-K", "8-K/A"):
                continue
            try:
                date_str = dates[i] if i < len(dates) else ""
                if not date_str:
                    continue
                filing_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                if filing_date < cutoff:
                    continue

                accession = (accessions[i] if i < len(accessions) else "").replace("-", "")
                items = str(items_list[i] if i < len(items_list) else "")

                # Build EDGAR viewer URL
                url_frag = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"

                filings.append({
                    "form":        form,
                    "date":        date_str,
                    "items":       items,
                    "description": descriptions[i] if i < len(descriptions) else "",
                    "url":         url_frag,
                })
            except Exception:
                continue

        if filings:
            result["available"] = True
            result["filings"]   = filings[:10]
            result["count"]     = len(filings)
            log.info("%s EDGAR 8-K: %d filings in last %d days", symbol, len(filings), days)
        else:
            result["error"] = f"No 8-K filings in last {days} days"

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s EDGAR failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _submissions_cache[symbol] = (result, time.time())
    return result
