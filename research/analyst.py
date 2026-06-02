"""
Multi-source AI Investment Research Operator.

Pipeline:
  1. Collect market data (yfinance)
  2. Collect news + sentiment + macro in parallel
  3. Build data packet
  4. Call Claude with the full packet (reasoning layer only)
  5. Fall back to rule-based scoring if no API key
  6. Persist result to SQLite
"""

import json
import concurrent.futures
from datetime import datetime, timezone

from database import db
from research.sources.market import get_market_data
from research.sources.news import get_news
from research.sources.sentiment import get_sentiment
from research.sources.macro import get_macro_data
from research.sources.fundamentals import get_quarterly_trends
from research.sources.insiders import get_insider_activity
from research.sources.earnings_history import get_earnings_history
from research.sources.revisions import get_analyst_revisions
from research.sources.correlation import get_portfolio_correlation
from research.sources.options_iv import get_options_iv
from research.sources.edgar import get_recent_8k
from research.sources.squeeze import get_squeeze_score
from research.sources.unusual_options import get_unusual_options
from research.sources.alpha_news import get_extra_news
from research.sources.stocktwits import get_stocktwits
from research.sources.institutions import get_institutional_data
from research.sources.gov_contracts import get_gov_contracts
from research.sources.sector_rotation import get_sector_rotation
from research.conviction import compute_conviction
from research.prompts import SYSTEM, build_prompt
from config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL


# ── Data collection ───────────────────────────────────────────────────────────

def _collect(symbol: str) -> dict:
    """Collect all sources. Market first → news+macro parallel → sentiment."""
    symbol = symbol.upper()

    market = get_market_data(symbol)
    company = market.get("name", symbol)

    news_data:    dict = {}
    macro_data:   dict = {}
    fund_data:    dict = {}
    ins_data:     dict = {}
    earn_data:    dict = {}
    rev_data:     dict = {}
    options_data: dict = {}
    edgar_data:   dict = {}
    uopts_data:   dict = {}
    extra_news:   dict = {}
    stkt_data:    dict = {}
    inst_data:    dict = {}
    gov_data:     dict = {}
    sector_data:  dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=14) as pool:
        news_fut    = pool.submit(get_news, symbol, company)
        macro_fut   = pool.submit(get_macro_data)
        fund_fut    = pool.submit(get_quarterly_trends, symbol)
        ins_fut     = pool.submit(get_insider_activity, symbol)
        earn_fut    = pool.submit(get_earnings_history, symbol)
        rev_fut     = pool.submit(get_analyst_revisions, symbol)
        options_fut = pool.submit(get_options_iv, symbol)
        edgar_fut   = pool.submit(get_recent_8k, symbol)
        uopts_fut   = pool.submit(get_unusual_options, symbol)
        enews_fut   = pool.submit(get_extra_news, symbol, company)
        stkt_fut    = pool.submit(get_stocktwits, symbol)
        inst_fut    = pool.submit(get_institutional_data, symbol)
        gov_fut     = pool.submit(get_gov_contracts, company, symbol)
        sector_fut  = pool.submit(get_sector_rotation)
        for fut, name, default in [
            (news_fut,    "news",    {"error": "timeout", "articles": [], "article_count": 0, "avg_sentiment": 0.0}),
            (macro_fut,   "macro",   {"error": "timeout"}),
            (fund_fut,    "fund",    {"error": "timeout", "available": False}),
            (ins_fut,     "ins",     {"error": "timeout", "available": False}),
            (earn_fut,    "earn",    {"error": "timeout", "available": False}),
            (rev_fut,     "rev",     {"error": "timeout", "available": False}),
            (options_fut, "options", {"error": "timeout", "available": False}),
            (edgar_fut,   "edgar",   {"error": "timeout", "available": False}),
            (uopts_fut,   "uopts",   {"error": "timeout", "available": False}),
            (enews_fut,   "enews",   {"error": "timeout", "available": False}),
            (stkt_fut,    "stkt",    {"error": "timeout", "available": False}),
            (inst_fut,    "inst2",   {"error": "timeout", "available": False}),
            (gov_fut,     "gov",     {"error": "timeout", "available": False}),
            (sector_fut,  "sector",  {"error": "timeout", "available": False}),
        ]:
            try:
                val = fut.result(timeout=25)
            except Exception as exc:
                val = {**default, "error": str(exc)}
            if   name == "news":    news_data    = val
            elif name == "macro":   macro_data   = val
            elif name == "fund":    fund_data    = val
            elif name == "ins":     ins_data     = val
            elif name == "earn":    earn_data    = val
            elif name == "rev":     rev_data     = val
            elif name == "options": options_data = val
            elif name == "edgar":   edgar_data   = val
            elif name == "uopts":   uopts_data   = val
            elif name == "enews":   extra_news   = val
            elif name == "stkt":    stkt_data    = val
            elif name == "inst2":   inst_data    = val
            elif name == "gov":     gov_data     = val
            elif name == "sector":  sector_data  = val

    try:
        sent_data = get_sentiment(symbol, company, news_data.get("avg_sentiment", 0.0))
    except Exception as exc:
        sent_data = {"error": str(exc), "overall_score": 0.0}

    # Merge extra news articles into the main news dict
    if extra_news.get("available") and extra_news.get("articles"):
        existing = news_data.get("articles", [])
        seen = {a["title"][:50].lower() for a in existing}
        for a in extra_news["articles"]:
            key = a["title"][:50].lower()
            if key and key not in seen:
                existing.append(a)
                seen.add(key)
        news_data["articles"]      = existing
        news_data["article_count"] = len(existing)
        news_data["sources_hit"]   = news_data.get("sources_hit", []) + extra_news.get("sources", [])

    # Squeeze score (derived from market data — no extra API call)
    squeeze = get_squeeze_score(market)

    return {
        "symbol":           symbol,
        "market":           market,
        "news":             news_data,
        "sentiment":        sent_data,
        "macro":            macro_data,
        "fundamentals":     fund_data,
        "insiders":         ins_data,
        "earnings_history": earn_data,
        "revisions":        rev_data,
        "options_iv":       options_data,
        "edgar":            edgar_data,
        "unusual_options":  uopts_data,
        "squeeze":          squeeze,
        "stocktwits":       stkt_data,
        "institutions":     inst_data,
        "gov_contracts":    gov_data,
        "sector_rotation":  sector_data,
        "collected_at":     datetime.now(timezone.utc).isoformat(),
    }


def _sources_list(data: dict) -> list[str]:
    sources: list[str] = []
    m = data.get("market", {})
    if not m.get("error"):
        sources.append(f"yfinance market data ({m.get('symbol','')})")

    news = data.get("news", {})
    if news.get("article_count", 0) > 0:
        sources.extend(news.get("sources_hit", ["News"]))

    sent = data.get("sentiment", {})
    reddit = sent.get("reddit", {})
    if isinstance(reddit, dict) and reddit.get("available"):
        sources.append(
            f"Reddit r/{'/r/'.join(reddit.get('subreddits',[]))} "
            f"({reddit.get('post_count',0)} posts)"
        )
    if sent.get("vader_available"):
        sources.append("VADER sentiment analysis")

    macro = data.get("macro", {})
    sources.extend(macro.get("sources", []))

    fund = data.get("fundamentals", {})
    if fund.get("available"):
        sources.append("yfinance quarterly financials")

    ins = data.get("insiders", {})
    if ins.get("available"):
        sources.append(
            f"insider transactions ({ins.get('buy_count', 0)}B/{ins.get('sell_count', 0)}S)"
        )

    m = data.get("market", {})
    if m.get("sector_etf"):
        sources.append(f"sector ETF {m['sector_etf']}")

    earn = data.get("earnings_history", {})
    if earn.get("available"):
        sources.append(f"earnings history ({earn.get('beats',0)}B/{earn.get('misses',0)}M)")

    edgar = data.get("edgar", {})
    if edgar.get("available"):
        sources.append(f"SEC EDGAR ({edgar.get('count',0)} 8-K filings)")

    uopts = data.get("unusual_options", {})
    if uopts.get("available") and uopts.get("is_unusual"):
        sources.append(f"unusual options ({uopts.get('signal','')})")

    rev = data.get("revisions", {})
    if rev.get("available"):
        sources.append(f"analyst revisions (↑{rev.get('upgrades_30d',0)}/↓{rev.get('downgrades_30d',0)} 30d)")

    opts = data.get("options_iv", {})
    if opts.get("available"):
        sources.append(f"options IV ({opts.get('atm_iv_pct','?')}% ATM)")

    corr = data.get("correlation", {})
    if corr.get("available"):
        sources.append("portfolio correlation")

    return sources


def _data_quality(data: dict) -> str:
    score = 0
    if not data.get("market", {}).get("error"):
        score += 1
    if data.get("news", {}).get("article_count", 0) > 3:
        score += 1
    reddit = data.get("sentiment", {}).get("reddit", {})
    if isinstance(reddit, dict) and reddit.get("available"):
        score += 1
    if not data.get("macro", {}).get("error") and data.get("macro", {}).get("vix"):
        score += 1
    if data.get("fundamentals", {}).get("available"):
        score += 1
    return "high" if score >= 4 else ("medium" if score >= 2 else "low")


# ── Rule-based fallback ───────────────────────────────────────────────────────

def _rule_based(symbol: str, data: dict, cash: float) -> dict:
    m       = data.get("market", {})
    news    = data.get("news", {})
    sent    = data.get("sentiment", {})
    macro   = data.get("macro", {})
    reddit  = sent.get("reddit", {})

    price        = m.get("price_usd", 0) or 0
    pe           = m.get("pe_ratio")
    beta         = m.get("beta") or 1.0
    rsi          = m.get("rsi_14") or 50.0
    rev_growth   = m.get("revenue_growth") or 0.0
    vs_sma200    = m.get("price_vs_sma200_pct") or 0.0
    news_score   = news.get("avg_sentiment", 0.0)
    reddit_score = reddit.get("avg_sentiment", 0.0) if isinstance(reddit, dict) and reddit.get("available") else None
    vix          = macro.get("vix")
    target_up    = m.get("target_upside_pct") or 0.0

    score = 5.0
    bull: list[str] = []
    bear: list[str] = []
    risks: list[str] = []

    # Valuation
    if pe and pe < 15:
        score += 1.5; bull.append(f"Attractive trailing P/E of {pe:.1f}x")
    elif pe and 15 <= pe < 25:
        score += 0.5
    elif pe and pe > 35:
        score -= 1.5; bear.append(f"Elevated P/E of {pe:.1f}x vs historical average")

    # Momentum / trend
    if vs_sma200 > 10:
        score += 0.5; bull.append(f"Price {vs_sma200:+.1f}% above 200-day moving average")
    elif vs_sma200 < -10:
        score -= 1.0; bear.append(f"Price {vs_sma200:+.1f}% below 200-day moving average (downtrend)")

    # RSI
    if rsi < 35:
        score += 1.0; bull.append(f"Oversold RSI of {rsi:.0f} (potential mean reversion)")
    elif rsi > 70:
        score -= 0.5; bear.append(f"Overbought RSI of {rsi:.0f}")

    # Growth
    if rev_growth > 0.2:
        score += 1.5; bull.append(f"Strong revenue growth of {rev_growth*100:.0f}%")
    elif rev_growth > 0.05:
        score += 0.5
    elif rev_growth < -0.05:
        score -= 1.0; bear.append(f"Declining revenue ({rev_growth*100:.0f}% growth)")

    # Analyst target
    if target_up > 15:
        score += 0.5; bull.append(f"Analyst mean target implies {target_up:.0f}% upside")
    elif target_up < -10:
        score -= 0.5; bear.append(f"Analyst mean target implies {target_up:.0f}% downside")

    # Sentiment
    if news_score >= 0.2:
        score += 0.5; bull.append(f"Positive news sentiment ({news_score:+.2f})")
    elif news_score <= -0.2:
        score -= 0.5; bear.append(f"Negative news sentiment ({news_score:+.2f})")
    if reddit_score is not None:
        if reddit_score >= 0.2:
            score += 0.5; bull.append(f"Bullish Reddit sentiment ({reddit_score:+.2f})")
        elif reddit_score <= -0.2:
            score -= 0.5; bear.append(f"Bearish social media sentiment ({reddit_score:+.2f})")

    # Macro
    if vix:
        if vix < 16:
            score += 0.5; bull.append(f"Low market fear (VIX {vix:.0f})")
        elif vix > 28:
            score -= 1.0; bear.append(f"Elevated market fear (VIX {vix:.0f})")

    # Risks
    if beta and beta > 1.5:
        risks.append(f"High beta ({beta:.1f}x) — amplified downside in sell-offs")
    if pe and pe > 30:
        risks.append(f"Premium valuation (P/E {pe:.1f}x) vulnerable to multiple compression")
    if macro.get("yield_curve_signal", "").startswith("inverted"):
        risks.append("Inverted yield curve historically precedes recessions")
    if vix and vix > 25:
        risks.append(f"Elevated market volatility (VIX {vix:.0f}) increases portfolio risk")
    risks.append("Rule-based analysis — set ANTHROPIC_API_KEY for AI reasoning")

    score = max(1.0, min(10.0, score))
    rec = "BUY" if score >= 7 else ("SELL" if score < 4 else "HOLD")

    position_size = round(min(cash * 0.25, max(5.0, cash * 0.15)), 2)

    return {
        "company_name":           m.get("name", symbol),
        "recommendation":         rec,
        "confidence":             round(score),
        "bull_thesis":            "Rule-based signals: " + ("; ".join(bull) if bull else "No strong bullish signals."),
        "bear_thesis":            "Risk signals: " + ("; ".join(bear) if bear else "No strong bearish signals."),
        "risks":                  risks,
        "target_price_usd":       m.get("target_mean_price"),
        "time_horizon":           "medium-term (weeks)",
        "key_metrics_assessment": (
            f"P/E: {f'{pe:.1f}' if pe else 'N/A'} | "
            f"RSI: {rsi:.0f} | Beta: {beta:.1f} | "
            f"Rev Growth: {f'{rev_growth*100:.0f}%' if rev_growth is not None else 'N/A'}"
        ),
        "macro_impact":           (
            f"Market regime: {macro.get('market_regime','N/A')}. "
            f"VIX: {f'{vix:.1f}' if vix else 'N/A'}."
        ),
        "sentiment_assessment":   (
            f"News: {news_score:+.2f}. "
            f"Reddit: {f'{reddit_score:+.2f}' if reddit_score is not None else 'N/A'}."
        ),
        "suitable_for_small_account": beta < 1.5 and (price or 0) < cash,
        "suggested_position_size_eur": position_size,
        "sources_used":           _sources_list(data),
        "data_quality":           _data_quality(data),
        "reasoning_engine":       "rule-based (no API key)",
    }


# ── Devil's advocate ─────────────────────────────────────────────────────────

def _devil_advocate(symbol: str, initial_result: dict) -> dict | None:
    """Second Claude call: argue against the initial recommendation."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        rec   = initial_result.get("recommendation", "")
        conf  = initial_result.get("confidence", 5)
        bull  = initial_result.get("bull_thesis", "")
        bear  = initial_result.get("bear_thesis", "")

        prompt = (
            f"A colleague made this call on {symbol}:\n"
            f"Recommendation: {rec}  Confidence: {conf}/10\n"
            f"Bull thesis: {bull}\n"
            f"Bear thesis: {bear}\n\n"
            "Your job: find every reason this recommendation is WRONG. "
            "Be brutal and specific. Do NOT validate the thesis.\n\n"
            "Output valid JSON only:\n"
            '{"critique":"2-3 sentences: strongest case AGAINST this recommendation",'
            '"red_flags":["specific concern with numbers","concern 2","concern 3"],'
            '"confidence_adjustment":integer from -3 to 0}'
        )
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=512,
            system="You are a contrarian equity analyst. Find flaws in investment recommendations.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as exc:
        print(f"[analyst] Devil's advocate failed: {exc}")
        return None


# ── Claude reasoning ─────────────────────────────────────────────────────────

def _call_claude(symbol: str, data: dict, cash: float, holdings: str) -> dict | None:
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        prompt = build_prompt(symbol, data, cash, holdings)
        resp = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=2048,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        result["reasoning_engine"] = f"Claude ({ANTHROPIC_MODEL})"
        return result
    except Exception as exc:
        print(f"[analyst] Claude failed: {exc}")
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def analyse(symbol: str, force_refresh: bool = False) -> dict:
    symbol = symbol.upper()

    # Cache hit
    if not force_refresh:
        row = db.get_latest_research(symbol)
        if row:
            try:
                inner = json.loads(row.get("analysis") or "{}")
                merged = {**row, **inner}
                # Ensure raw_data is a dict — prefer parsed analysis JSON, fall back to DB column
                if not isinstance(merged.get("raw_data"), dict):
                    rd = row.get("raw_data") or "{}"
                    try:
                        merged["raw_data"] = json.loads(rd) if isinstance(rd, str) else (rd or {})
                    except Exception:
                        merged["raw_data"] = {}
                return merged
            except Exception:
                return row

    # Collect all data
    data = _collect(symbol)

    cash = db.get_balance()
    positions = db.get_all_positions()
    holdings = ", ".join(f"{p['symbol']}×{p['quantity']:.2f}" for p in positions) or "none"

    # Portfolio correlation (needs positions, so done after _collect)
    pos_syms = [p["symbol"] for p in positions if p["symbol"].upper() != symbol]
    if pos_syms:
        try:
            data["correlation"] = get_portfolio_correlation(symbol, pos_syms)
        except Exception as exc:
            data["correlation"] = {"available": False, "error": str(exc)}
    else:
        data["correlation"] = {"available": False, "reason": "No current positions"}

    # Reason with Claude (or fall back)
    result = _call_claude(symbol, data, cash, holdings) or _rule_based(symbol, data, cash)

    # Devil's advocate (second Claude call — only when API key present)
    if ANTHROPIC_API_KEY:
        devil = _devil_advocate(symbol, result)
        if devil:
            result["devil_advocate_critique"]   = devil.get("critique", "")
            result["devil_advocate_red_flags"]  = devil.get("red_flags", [])
            adj = max(-3, min(0, int(devil.get("confidence_adjustment", 0))))
            if adj:
                result["confidence"] = max(1, min(10, int(result.get("confidence", 5)) + adj))

    # Kelly criterion position sizing (replaces arbitrary sizing when enough history exists)
    from database.predictions import get_kelly_inputs
    kelly = get_kelly_inputs(symbol) or get_kelly_inputs()
    if kelly and kelly.get("sample_size", 0) >= 10:
        hk         = kelly["half_kelly_fraction"]
        conf_mult  = 0.5 + int(result.get("confidence", 5)) / 10
        fraction   = min(hk * conf_mult, 0.25)
        kelly_size = max(5.0, round(cash * fraction, 2))
        result["suggested_position_size_eur"] = kelly_size
        result["kelly_inputs"] = kelly

    # Always compute these programmatically — Claude can't know actual sources
    result["sources_used"] = _sources_list(data)
    result["data_quality"] = _data_quality(data)
    result.setdefault("risks", [])

    # Conviction score — independent data-driven reliability signal
    conviction = compute_conviction(data, result)
    result["conviction"] = conviction

    # Do-not-trade guard: override position size to 0 when hard stops fire
    if conviction.get("do_not_trade"):
        result["suggested_position_size_eur"] = 0.0
        flags = conviction.get("do_not_trade_flags", [])
        result["risks"] = [f"⛔ DO NOT TRADE: {f}" for f in flags] + result.get("risks", [])

    # Attach metadata + raw collected data (so template can display it on fresh results too)
    result["raw_data_summary"] = {
        "market_error":    data["market"].get("error"),
        "news_articles":   data["news"].get("article_count", 0),
        "reddit_posts":    data["sentiment"].get("reddit", {}).get("post_count", 0)
                           if isinstance(data["sentiment"].get("reddit"), dict) else 0,
        "macro_sources":   len(data["macro"].get("sources", [])),
        "collection_time": data["collected_at"],
    }
    result["raw_data"] = {
        "news":             data["news"],
        "sentiment":        data["sentiment"],
        "macro":            data["macro"],
        "market":           {k: v for k, v in data["market"].items() if v is not None and k != "error"},
        "fundamentals":     data.get("fundamentals", {}),
        "insiders":         data.get("insiders", {}),
        "earnings_history": data.get("earnings_history", {}),
        "revisions":        data.get("revisions", {}),
        "options_iv":       data.get("options_iv", {}),
        "correlation":      data.get("correlation", {}),
        "edgar":            data.get("edgar", {}),
        "unusual_options":  data.get("unusual_options", {}),
        "squeeze":          data.get("squeeze", {}),
        "stocktwits":       data.get("stocktwits", {}),
        "institutions":     data.get("institutions", {}),
        "gov_contracts":    data.get("gov_contracts", {}),
        "sector_rotation":  data.get("sector_rotation", {}),
    }

    # Persist — store raw_data separately from Claude's analysis
    db.save_research(
        symbol=symbol,
        company_name=result.get("company_name", symbol),
        analysis=json.dumps(result),
        recommendation=result.get("recommendation", "HOLD"),
        confidence=int(result.get("confidence") or 5),
        target_price_usd=result.get("target_price_usd"),
        bull_thesis=result.get("bull_thesis", ""),
        bear_thesis=result.get("bear_thesis", ""),
        risks=json.dumps(result.get("risks", [])),
        sources_used=json.dumps(result.get("sources_used", [])),
        raw_data=json.dumps({
            "news":             data["news"],
            "sentiment":        data["sentiment"],
            "macro":            data["macro"],
            "market":           {k: v for k, v in data["market"].items()
                                 if k not in ("error",) and v is not None},
            "fundamentals":     data.get("fundamentals", {}),
            "insiders":         data.get("insiders", {}),
            "earnings_history": data.get("earnings_history", {}),
            "revisions":        data.get("revisions", {}),
            "options_iv":       data.get("options_iv", {}),
            "correlation":      data.get("correlation", {}),
            "edgar":            data.get("edgar", {}),
            "unusual_options":  data.get("unusual_options", {}),
            "squeeze":          data.get("squeeze", {}),
        }),
    )

    # Auto-create a prediction entry for tracking (skip if recent duplicate)
    _create_prediction(symbol, result, data)

    return result


def _create_prediction(symbol: str, result: dict, data: dict) -> None:
    """Create a prediction entry unless one was made for this symbol in the last 4 hours."""
    try:
        from database.predictions import create_prediction, get_recent_prediction

        if get_recent_prediction(symbol, hours=4):
            return

        market = data.get("market", {})
        if not market.get("price_usd"):
            return

        # Extract fired signal names from conviction for attribution tracking
        conviction = result.get("conviction", {})
        bull_names  = [n for n, _, _ in conviction.get("bull_signals", [])]
        bear_names  = [n for n, _, _ in conviction.get("bear_signals", [])]
        all_signals = bull_names + bear_names

        create_prediction(
            symbol=symbol,
            company_name=result.get("company_name", symbol),
            recommendation=result.get("recommendation", "HOLD"),
            confidence=int(result.get("confidence") or 5),
            bull_thesis=result.get("bull_thesis", "")[:500],
            bear_thesis=result.get("bear_thesis", "")[:500],
            price_usd=market["price_usd"],
            price_eur=market["price_eur"],
            eur_usd_rate=market.get("eur_usd_rate", 1.08),
            reasoning_engine=result.get("reasoning_engine", ""),
            sources_count=len(result.get("sources_used", [])),
            conviction_score=conviction.get("conviction_score"),
            signals_present=all_signals,
        )
    except Exception as exc:
        print(f"[analyst] Prediction creation failed: {exc}")
