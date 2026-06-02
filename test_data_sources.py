#!/usr/bin/env python3
"""
Manual data source smoke test.
Usage:  python test_data_sources.py [TICKER]
        python test_data_sources.py AAPL
        python test_data_sources.py NVDA

Prints a summary of every collector: status, records, errors, and key values.
Exit code 0 = all sources OK or partial; 1 = any source completely failed.
"""

import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s [%(name)s] %(message)s",
)

SEP  = "=" * 64
LINE = "-" * 40


def ok(s):  return f"\033[32m✓  {s}\033[0m"
def warn(s): return f"\033[33m~  {s}\033[0m"
def err(s):  return f"\033[31m✗  {s}\033[0m"


def _section(n, title):
    print(f"\n{LINE}")
    print(f"{n}. {title}")
    print(LINE)


def test_market(symbol: str) -> dict:
    _section(1, "MARKET DATA")
    from research.sources.market import get_market_data
    data   = get_market_data(symbol)
    meta   = data.get("_meta", {})
    status = meta.get("status", "failed") if not data.get("error") else "failed"

    if data.get("error"):
        print(err(f"FAILED — {data['error']}"))
    else:
        print(ok(f"Status: {status.upper()}  |  {meta.get('history_bars',0)} price bars  |  "
                 f"{meta.get('duration_ms',0)} ms"))
        print(f"   Price:      ${data.get('price_usd', 'N/A')}")
        print(f"   Prev close: ${data.get('prev_close_usd', 'N/A')}")
        print(f"   Change:     {data.get('change_pct', 'N/A')}%")
        print(f"   RSI-14:     {data.get('rsi_14', 'N/A')}")
        print(f"   SMA-20/50/200: {data.get('sma_20','N/A')} / "
              f"{data.get('sma_50','N/A')} / {data.get('sma_200','N/A')}")
        info_ok = meta.get("info_available", False)
        if info_ok:
            print(ok("   ticker.info available (P/E, fundamentals, etc.)"))
        else:
            print(warn("   ticker.info unavailable — using fast_info + history fallback"))

    for w in meta.get("warnings", []):
        print(f"   WARN: {w}")
    for e in meta.get("errors", []):
        print(f"   ERR:  {e}")
    return data


def test_news(symbol: str, company: str) -> dict:
    _section(2, "NEWS")
    from research.sources.news import get_news
    data = get_news(symbol, company)
    meta = data.get("_meta", {})
    count = data.get("article_count", 0)

    if count > 0:
        print(ok(f"Status: {meta.get('status','ok').upper()}  |  {count} articles  |  "
                 f"{meta.get('duration_ms',0)} ms"))
        print(f"   Avg sentiment: {data.get('avg_sentiment',0.0):.3f}  "
              f"({data.get('sentiment_label','N/A')})")
        if data.get("sources_hit"):
            print(f"   Sources hit:   {', '.join(data['sources_hit'])}")
    else:
        print(err(f"0 articles  — {meta.get('reason','all sources empty')}"))

    if not data.get("vader_available"):
        print(warn("   vaderSentiment NOT installed — all scores will be 0.000"))

    for s in meta.get("sources", []):
        status = s.get("status", "?")
        n      = s.get("count", 0)
        e      = s.get("error", "")
        icon   = ok if status == "ok" else (warn if status == "empty" else err)
        msg    = f"{status.upper():8}  {s.get('source','')[:55]}  ({n} articles)"
        if e:
            msg += f"  — {e}"
        print("  " + icon(msg))
        if s.get("url"):
            print(f"             URL: {s['url'][:80]}")

    for e in meta.get("errors", []):
        print(f"   ERROR: {e}")
    return data


def test_sentiment(symbol: str, company: str, news_sentiment: float) -> dict:
    _section(3, "REDDIT SENTIMENT")
    from research.sources.sentiment import get_sentiment
    data   = get_sentiment(symbol, company, news_sentiment)
    meta   = data.get("_meta", {})
    reddit = data.get("reddit", {})

    if reddit.get("available"):
        print(ok(f"Reddit available  |  {reddit.get('post_count',0)} posts  |  "
                 f"{meta.get('duration_ms',0)} ms"))
        print(f"   Weighted sentiment: {reddit.get('avg_sentiment',0.0):.3f}  "
              f"({reddit.get('sentiment_label','')})")
        print(f"   Subreddits: {', '.join('r/'+s for s in reddit.get('subreddits',[]))}")
    else:
        print(warn(f"Reddit unavailable — {reddit.get('reason','no posts found')}"))
        print(warn("   Falling back to news-only sentiment"))

    print(f"   Overall score: {data.get('overall_score',0.0):.3f}  "
          f"({data.get('overall_label','')})")

    if not data.get("vader_available"):
        print(warn("   vaderSentiment NOT installed"))

    tried = meta.get("subreddits_tried", [])
    hit   = set(meta.get("subreddits_hit", []))
    if tried:
        for s in tried:
            marker = ok(f"r/{s}") if s in hit else err(f"r/{s} (no posts / blocked)")
            print(f"   {marker}")

    for e in meta.get("errors", []):
        print(f"   ERROR: {e}")
    return data


def test_macro() -> dict:
    _section(4, "MACRO DATA")
    from research.sources.macro import get_macro_data
    data   = get_macro_data()
    meta   = data.get("_meta", {})
    n_ok   = len(data.get("sources", []))
    n_tot  = len(meta.get("tickers_tried", {}))
    status = meta.get("status", "failed")

    if n_ok >= 1:
        print(ok(f"Status: {status.upper()}  |  {n_ok}/{n_tot} tickers OK  |  "
                 f"{meta.get('duration_ms',0)} ms"))
    else:
        print(err(f"FAILED  — 0/{n_tot} tickers returned data"))

    for name, val in meta.get("sources_status", {}).items():
        icon = ok if val.startswith("ok") else (warn if "no API" in val else err)
        print(f"   {icon(f'{name:10} {val}')}")

    if data.get("vix") is not None:
        print(f"   VIX:     {data['vix']:.1f}  ({data.get('vix_signal','')})")
    if data.get("sp500_price"):
        print(f"   S&P 500: {data['sp500_price']:.0f}")
    if data.get("market_regime"):
        print(f"   Regime:  {data['market_regime']}")

    for field, e in meta.get("fetch_errors", {}).items():
        print(f"   ERR [{field}]: {e}")
    return data


def main():
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper()
    from datetime import datetime
    print(f"\n{SEP}")
    print(f"  Data Source Diagnostics  —  {symbol}")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(SEP)

    market    = test_market(symbol)
    news      = test_news(symbol, market.get("name", symbol))
    sentiment = test_sentiment(symbol, market.get("name", symbol),
                               news.get("avg_sentiment", 0.0))
    macro     = test_macro()

    # ── Final summary ────────────────────────────────────────────────────────
    reddit = sentiment.get("reddit", {})
    print(f"\n{SEP}")
    print("SUMMARY")
    print(LINE)

    checks = [
        (not bool(market.get("error")),
         f"Market data  — ${market.get('price_usd','N/A')}"),
        (news.get("article_count", 0) > 0,
         f"News         — {news.get('article_count',0)} articles"),
        (reddit.get("available", False),
         "Reddit       — " + ("available" if reddit.get("available")
                              else "unavailable (news sentiment used as fallback)")),
        (len(macro.get("sources", [])) >= 1,
         f"Macro        — {len(macro.get('sources',[]))}/{len(macro.get('_meta',{}).get('tickers_tried',{}))} tickers"),
    ]

    failures = 0
    for passed, label in checks:
        if passed:
            print(ok(label))
        else:
            print(err(label))
            failures += 1

    print(f"\n  {4 - failures}/4 sources healthy")
    print(SEP + "\n")

    sys.exit(1 if failures >= 3 else 0)


if __name__ == "__main__":
    main()
