"""
Government contract data from USASpending.gov — free, no API key required.
Defense/tech/AI/infrastructure stocks are often re-rated after contract awards.
"""
import logging
import time
from datetime import datetime, timezone, timedelta
import requests

log = logging.getLogger(__name__)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_SECONDS = 3600 * 12  # 12 hours

_BASE = "https://api.usaspending.gov/api/v2"
_HEADERS = {"Content-Type": "application/json", "User-Agent": "AI-Paper-Trader research@example.com"}


def get_gov_contracts(company_name: str, symbol: str) -> dict:
    symbol = symbol.upper()
    cache_key = f"gov|{symbol}"
    cached, ts = _cache.get(cache_key, ({}, 0.0))
    if cached and (time.time() - ts) < CACHE_SECONDS:
        return cached

    result: dict = {"symbol": symbol, "available": False, "contracts": []}

    if not company_name:
        result["error"] = "No company name"
        _cache[cache_key] = (result, time.time())
        return result

    # Use the first meaningful word of company name for search
    search_term = company_name.split(",")[0].split(" Inc")[0].split(" Corp")[0].strip()
    if len(search_term) < 3:
        result["error"] = "Company name too short for search"
        _cache[cache_key] = (result, time.time())
        return result

    try:
        # Recipient search
        resp = requests.get(
            f"{_BASE}/recipient/search/",
            params={"search_text": search_term, "limit": 3},
            headers=_HEADERS,
            timeout=10,
        )
        if resp.status_code != 200:
            result["error"] = f"Recipient search HTTP {resp.status_code}"
            _cache[cache_key] = (result, time.time())
            return result

        recipients = resp.json().get("results", [])
        if not recipients:
            result["error"] = f"No recipient found for '{search_term}'"
            _cache[cache_key] = (result, time.time())
            return result

        recipient = recipients[0]
        recipient_id = recipient.get("id", "")
        recipient_name = recipient.get("name", company_name)

        # Awards search
        start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        end_date   = datetime.now().strftime("%Y-%m-%d")

        awards_resp = requests.post(
            f"{_BASE}/search/spending_by_award/",
            json={
                "filters": {
                    "recipient_id":       recipient_id,
                    "award_type_codes":   ["A", "B", "C", "D", "IDV_A", "IDV_B",
                                           "IDV_B_A", "IDV_B_B", "IDV_B_C"],
                    "time_period":        [{"start_date": start_date, "end_date": end_date}],
                },
                "fields":  ["Award ID", "Recipient Name", "Award Amount",
                             "Award Date", "Awarding Agency", "Description"],
                "sort":    "Award Amount",
                "order":   "desc",
                "limit":   10,
            },
            headers=_HEADERS,
            timeout=12,
        )
        if awards_resp.status_code != 200:
            result["error"] = f"Awards search HTTP {awards_resp.status_code}"
            _cache[cache_key] = (result, time.time())
            return result

        awards = awards_resp.json().get("results", [])
        contracts = []
        total_value = 0.0
        for a in awards:
            val = float(a.get("Award Amount") or 0)
            contracts.append({
                "id":       str(a.get("Award ID", ""))[:20],
                "date":     str(a.get("Award Date", ""))[:10],
                "amount":   val,
                "agency":   str(a.get("Awarding Agency", ""))[:40],
                "desc":     str(a.get("Description", ""))[:80],
            })
            total_value += val

        if contracts:
            result.update({
                "available":      True,
                "recipient_name": recipient_name[:50],
                "contracts":      contracts,
                "contract_count": len(contracts),
                "total_value":    total_value,
                "period_days":    365,
            })
            log.info("%s gov contracts: %d awards, total $%.1fM",
                     symbol, len(contracts), total_value / 1e6)
        else:
            result["error"] = "No contracts in the past 12 months"

    except Exception as exc:
        result["error"] = str(exc)
        log.warning("%s gov_contracts failed: %s", symbol, exc)

    result["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _cache[cache_key] = (result, time.time())
    return result
