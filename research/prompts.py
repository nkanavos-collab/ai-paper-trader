"""Build the data packet → Claude prompt. Claude is the reasoning layer only."""

SYSTEM = """You are a senior equity research analyst. You have been given pre-collected, structured data from multiple sources.

Your role is EXCLUSIVELY to reason about the provided data and produce a structured investment analysis.

Rules:
- Do NOT invent facts. Every claim in your thesis must be traceable to the provided data.
- Do NOT make up prices, earnings, news stories, or metrics not listed below.
- Be specific — cite the actual numbers from the data.
- When multiple independent data sources agree (revenue accelerating + analysts upgrading + StockTwits bullish + unusual calls), that convergence is a STRONGER signal than any single source alone.
- When data sources conflict (e.g. strong fundamentals but bearish social sentiment), explicitly flag the conflict and reduce confidence accordingly.
- If a "DO NOT TRADE" condition is present in the data (earnings in <2 days, extreme portfolio correlation), set position size to 0 and explain why.
- Output must be valid JSON matching the schema exactly. No text outside the JSON."""


def _fv(v, fmt=".2f") -> str:
    if v is None:
        return "N/A"
    try:
        return f"{v:{fmt}}"
    except (TypeError, ValueError):
        return str(v)


def _fp(v) -> str:
    if v is None:
        return "N/A"
    try:
        return f"{float(v)*100:.1f}%"
    except (TypeError, ValueError):
        return str(v)


def build_prompt(symbol: str, data: dict, cash: float, holdings: str) -> str:
    m        = data.get("market", {})
    news     = data.get("news", {})
    sent     = data.get("sentiment", {})
    macro    = data.get("macro", {})
    fund     = data.get("fundamentals", {})
    ins      = data.get("insiders", {})
    earn     = data.get("earnings_history", {})
    rev      = data.get("revisions", {})
    opts     = data.get("options_iv", {})
    corr     = data.get("correlation", {})
    edgar    = data.get("edgar", {})
    uopts    = data.get("unusual_options", {})
    squeeze  = data.get("squeeze", {})
    stkt     = data.get("stocktwits", {})
    inst     = data.get("institutions", {})
    gov      = data.get("gov_contracts", {})
    sec_rot  = data.get("sector_rotation", {})

    sections: list[str] = []

    # ── Market Data ───────────────────────────────────────────────────────────
    cap = m.get("market_cap")
    cap_str = f"${cap/1e9:.1f}B" if cap and cap > 1e9 else (f"${cap/1e6:.0f}M" if cap else "N/A")

    sections.append(f"""=== MARKET DATA ===
Symbol: {symbol}  |  Company: {m.get('name', symbol)}
Sector: {m.get('sector','N/A')}  |  Industry: {m.get('industry','N/A')}
Market Cap: {cap_str}

PRICE & PERFORMANCE:
  Price: ${_fv(m.get('price_usd'))}  (€{_fv(m.get('price_eur'))})  |  EUR/USD: {_fv(m.get('eur_usd_rate'), '.4f')}
  Change: 1D {_fv(m.get('change_pct'),'+.2f')}%  1W {_fv(m.get('change_1w_pct'),'+.2f')}%  1M {_fv(m.get('change_1m_pct'),'+.2f')}%  3M {_fv(m.get('change_3m_pct'),'+.2f')}%
  52w Range: ${_fv(m.get('low_52w'))} – ${_fv(m.get('high_52w'))}  |  From 52w High: {_fv(m.get('pct_from_52w_high'),'+.1f')}%

TECHNICALS:
  RSI(14): {_fv(m.get('rsi_14'),'.1f')}
  SMA20:  ${_fv(m.get('sma_20'))} (price {_fv(m.get('price_vs_sma20_pct'),'+.1f')}%)
  SMA50:  ${_fv(m.get('sma_50'))} (price {_fv(m.get('price_vs_sma50_pct'),'+.1f')}%)
  SMA200: ${_fv(m.get('sma_200'))} (price {_fv(m.get('price_vs_sma200_pct'),'+.1f')}%)
  Volume Ratio vs Avg: {_fv(m.get('volume_ratio'),'.2f')}x

FUNDAMENTALS:
  P/E: {_fv(m.get('pe_ratio'),'.1f')}  |  Fwd P/E: {_fv(m.get('forward_pe'),'.1f')}  |  PEG: {_fv(m.get('peg_ratio'),'.2f')}
  EPS: ${_fv(m.get('eps'),'.2f')}  |  Beta: {_fv(m.get('beta'),'.2f')}  |  Div Yield: {_fp(m.get('dividend_yield'))}
  Revenue Growth: {_fp(m.get('revenue_growth'))}  |  Earnings Growth: {_fp(m.get('earnings_growth'))}
  Gross Margin: {_fp(m.get('gross_margin'))}  |  Net Margin: {_fp(m.get('profit_margin'))}
  ROE: {_fp(m.get('roe'))}  |  Debt/Equity: {_fv(m.get('debt_to_equity'),'.2f')}  |  Current Ratio: {_fv(m.get('current_ratio'),'.2f')}
  Short % Float: {_fp(m.get('short_pct_float'))}  |  Institutional: {_fp(m.get('institutional_pct'))}

ANALYST CONSENSUS:
  Rating: {m.get('analyst_rating','N/A')}  |  {m.get('analyst_count','N/A')} analysts
  Mean Target: ${_fv(m.get('target_mean_price'))}  ({_fv(m.get('target_upside_pct'),'+.1f')}% upside)""")

    # ── News ──────────────────────────────────────────────────────────────────
    articles = news.get("articles", [])
    if articles:
        news_lines = [
            f"\n=== NEWS ({news.get('article_count',0)} articles, last 14 days) ===",
            f"Avg Sentiment: {news.get('avg_sentiment',0):.3f} → {news.get('sentiment_label','N/A')}",
            f"Positive: {news.get('positive_count',0)}  |  Negative: {news.get('negative_count',0)}  |  Neutral: {news.get('neutral_count',0)}",
            "",
            "Headlines (sentiment score):",
        ]
        for a in articles[:12]:
            s = a.get("sentiment", 0)
            arrow = "▲" if s > 0.1 else ("▼" if s < -0.1 else "–")
            news_lines.append(f"  {arrow} [{s:+.2f}] {a['title'][:100]}  ({a.get('source','')}, {a.get('date','')})")
        if news.get("top_positive"):
            news_lines.append(f"\nTop Positive: {news['top_positive'][:110]}")
        if news.get("top_negative"):
            news_lines.append(f"Top Negative: {news['top_negative'][:110]}")
        sections.append("\n".join(news_lines))

    # ── Sentiment ─────────────────────────────────────────────────────────────
    reddit = sent.get("reddit", {})
    sent_lines = ["\n=== SENTIMENT ANALYSIS ==="]
    sent_lines.append(f"Overall Combined Score: {sent.get('overall_score',0):.3f} → {sent.get('overall_label','N/A')}")
    sent_lines.append(f"News Sentiment: {sent.get('news_sentiment_score',0):.3f} ({sent.get('news_sentiment_label','N/A')})")

    if isinstance(reddit, dict) and reddit.get("available"):
        sent_lines.append(
            f"Reddit Sentiment: {reddit.get('avg_sentiment',0):.3f} ({reddit.get('sentiment_label','N/A')}) "
            f"— {reddit.get('post_count',0)} posts in r/{', r/'.join(reddit.get('subreddits',[]))}"
        )
        sent_lines.append(f"  Bullish: {reddit.get('bullish_count',0)}  |  Bearish: {reddit.get('bearish_count',0)}  |  Total Engagement: {reddit.get('total_engagement',0):,}")
        top = reddit.get("top_posts", [])[:4]
        if top:
            sent_lines.append("Top Reddit Posts:")
            for p in top:
                arrow = "↑" if p.get("sentiment", 0) > 0.05 else ("↓" if p.get("sentiment", 0) < -0.05 else "→")
                sent_lines.append(f"  {arrow} [{p.get('score',0):,}pts] {p['title'][:90]}")
    else:
        sent_lines.append("Reddit: unavailable or no posts found")

    sections.append("\n".join(sent_lines))

    # ── Macro ─────────────────────────────────────────────────────────────────
    if macro and not macro.get("error"):
        m_lines = ["\n=== MACRO CONTEXT ===",
                   f"Market Regime: {macro.get('market_regime','N/A')}",
                   f"VIX: {_fv(macro.get('vix'),'.1f')} — {macro.get('vix_signal','N/A')}",
                   f"S&P 500: {_fv(macro.get('sp500_price'),'.0f')} | 1M: {_fv(macro.get('sp500_1m_pct'),'+.2f')}% | YTD: {_fv(macro.get('sp500_ytd_pct'),'+.2f')}%",
                   "",
                   "RATES:",
                   f"  10Y Treasury: {_fv(macro.get('ten_year_yield_pct'),'.2f')}%",
                   f"  2Y/Short-term: {_fv(macro.get('two_year_yield_pct'),'.2f')}%",
                   f"  Yield Curve: {macro.get('yield_curve_signal','N/A')} (spread {_fv(macro.get('yield_curve_spread'),'.3f')}%)"]

        if macro.get("fed_funds_rate_pct"):
            m_lines.append(f"  Fed Funds Rate: {macro['fed_funds_rate_pct']:.2f}%")
        if macro.get("cpi_yoy_pct"):
            m_lines.append(f"  CPI (YoY): {macro['cpi_yoy_pct']:.1f}%")
        if macro.get("unemployment_pct"):
            m_lines.append(f"  Unemployment: {macro['unemployment_pct']:.1f}%")

        m_lines.extend([
            "",
            "COMMODITIES & USD:",
            f"  Gold: ${_fv(macro.get('gold_price_usd'),'.0f')} (1M: {_fv(macro.get('gold_1m_pct'),'+.2f')}%)",
            f"  Oil WTI: ${_fv(macro.get('oil_wti_usd'),'.2f')} (1M: {_fv(macro.get('oil_1m_pct'),'+.2f')}%)",
            f"  USD Index: {_fv(macro.get('usd_index'),'.2f')} (1M: {_fv(macro.get('usd_1m_pct'),'+.2f')}%)",
        ])
        sections.append("\n".join(m_lines))

    # ── Earnings date ─────────────────────────────────────────────────────────
    ed = m.get("earnings_date")
    dte = m.get("days_until_earnings")
    if ed and dte is not None:
        if dte <= 0:
            sections.insert(0, f"⚠️  EARNINGS RECENTLY PASSED ({ed}, {-dte} days ago) — results may not yet be priced in")
        elif dte <= 7:
            sections.insert(0, f"⚠️  EARNINGS IN {dte} DAYS ({ed}) — HIGH VOLATILITY RISK: factor into position sizing")
        else:
            sections.append(f"\n=== UPCOMING EARNINGS ===\nEarnings date: {ed} ({dte} days away)")

    # ── Sector relative strength ───────────────────────────────────────────────
    sector_etf = m.get("sector_etf")
    if sector_etf and m.get("sector_etf_1m_pct") is not None:
        rs1m = m.get("rel_strength_1m_pct")
        rs3m = m.get("rel_strength_3m_pct")
        perf = "OUTPERFORMING" if (rs1m or 0) > 0 else "UNDERPERFORMING"
        s_lines = [
            f"\n=== SECTOR RELATIVE STRENGTH ===",
            f"Stock 1M: {_fv(m.get('change_1m_pct'),'+.2f')}%  |  {sector_etf} sector ETF 1M: {_fv(m.get('sector_etf_1m_pct'),'+.2f')}%  |  Relative: {_fv(rs1m,'+.2f')}%",
            f"Stock 3M: {_fv(m.get('change_3m_pct'),'+.2f')}%  |  {sector_etf} sector ETF 3M: {_fv(m.get('sector_etf_3m_pct'),'+.2f')}%  |  Relative: {_fv(rs3m,'+.2f')}%",
            f"→ Stock is {perf} its sector over 1 month",
        ]
        sections.append("\n".join(s_lines))

    # ── Quarterly fundamentals ─────────────────────────────────────────────────
    if fund.get("available"):
        quarters = fund.get("quarters", [])
        f_lines = [f"\n=== QUARTERLY FUNDAMENTALS (most recent first) ==="]
        if fund.get("revenue_trend"):
            f_lines.append(f"Revenue Trend: {fund['revenue_trend'].upper()}")
        if fund.get("yoy_revenue_growth_pct") is not None:
            f_lines.append(f"YoY Revenue Growth (latest Q): {fund['yoy_revenue_growth_pct']:+.1f}%")
        if fund.get("yoy_earnings_growth_pct") is not None:
            f_lines.append(f"YoY Earnings Growth (latest Q): {fund['yoy_earnings_growth_pct']:+.1f}%")
        f_lines.append("")
        f_lines.append(f"{'Period':<12} {'Revenue':>12} {'Net Income':>12} {'Gross Margin':>13}")
        f_lines.append("-" * 52)
        for q in quarters[:4]:
            rev = q.get("revenue")
            ni  = q.get("net_income")
            gm  = q.get("gross_margin_pct")
            rev_s = (f"${rev/1e9:.2f}B"  if rev and rev >= 1e9 else
                     f"${rev/1e6:.0f}M"  if rev else "N/A")
            ni_s  = (f"${ni/1e9:.2f}B"   if ni  and abs(ni) >= 1e9 else
                     f"${ni/1e6:.0f}M"   if ni  else "N/A")
            gm_s  = f"{gm:.1f}%" if gm else "N/A"
            f_lines.append(f"{q['period']:<12} {rev_s:>12} {ni_s:>12} {gm_s:>13}")
        sections.append("\n".join(f_lines))

    # ── Earnings surprise history ──────────────────────────────────────────────
    if earn.get("available"):
        quarters = earn.get("quarters", [])
        e_lines = [
            f"\n=== EARNINGS SURPRISE HISTORY ===",
            f"Beat Rate: {earn.get('beat_rate','N/A')}%  |  Streak: {earn.get('streak','N/A')}  |  Avg Surprise: {_fv(earn.get('avg_surprise_pct'),'+.2f')}%",
            "",
            f"{'Quarter':<12} {'Estimate':>10} {'Actual':>10} {'Surprise':>10} {'Beat'}",
            "-" * 50,
        ]
        for q in quarters[:8]:
            beat_str = "✓" if q.get("beat") else ("✗" if q.get("beat") is False else "–")
            e_lines.append(
                f"{q['period']:<12} "
                f"{_fv(q.get('eps_estimate'), '.2f'):>10} "
                f"{_fv(q.get('eps_actual'), '.2f'):>10} "
                f"{_fv(q.get('surprise_pct'), '+.2f'):>9}% "
                f"{beat_str}"
            )
        sections.append("\n".join(e_lines))

    # ── Analyst revision momentum ──────────────────────────────────────────────
    if rev.get("available"):
        r_lines = [
            f"\n=== ANALYST REVISION MOMENTUM ===",
            f"Momentum: {rev.get('momentum','N/A').upper()}",
            f"Last 30 days: {rev.get('upgrades_30d',0)} upgrades / {rev.get('downgrades_30d',0)} downgrades",
            f"Last 90 days: {rev.get('upgrades_90d',0)} upgrades / {rev.get('downgrades_90d',0)} downgrades",
            "",
            "Recent actions:",
        ]
        for r in rev.get("recent", [])[:8]:
            r_lines.append(
                f"  {r['date']} | {r['firm']:<25} | {r['action'].upper():<8} | "
                f"{r['from_grade'][:15]} → {r['to_grade'][:15]}"
            )
        sections.append("\n".join(r_lines))

    # ── Options IV ────────────────────────────────────────────────────────────
    if opts.get("available"):
        sections.append(
            f"\n=== OPTIONS IMPLIED VOLATILITY ===\n"
            f"ATM IV: {opts.get('atm_iv_pct','N/A')}%  |  HV(20d): {opts.get('hv_20_pct','N/A')}%  |  "
            f"IV/HV: {opts.get('iv_vs_hv','N/A')}x  |  Signal: {opts.get('signal','N/A').upper()}\n"
            f"Expected 1-week move: ±{opts.get('expected_move_pct','N/A')}%\n"
            f"{'⚠️ Options are EXPENSIVE — poor risk/reward for buyers' if opts.get('signal') == 'expensive' else ''}"
        )

    # ── Portfolio correlation ─────────────────────────────────────────────────
    if corr.get("warning"):
        high_pairs = corr.get("high_correlation_pairs", [])
        c_lines = ["\n=== ⚠️  PORTFOLIO CORRELATION WARNING ==="]
        for s, c in high_pairs:
            c_lines.append(f"  {symbol} ↔ {s}: {c:.2f} correlation (>0.70 = same risk exposure)")
        c_lines.append("Consider whether adding this position creates meaningful diversification.")
        sections.insert(1, "\n".join(c_lines))  # high priority — put near top

    # ── Insider transactions ───────────────────────────────────────────────────
    if ins.get("available"):
        txns = ins.get("transactions", [])
        i_lines = [
            f"\n=== INSIDER TRANSACTIONS (last {len(txns)}) ===",
            f"Net Bias: {ins.get('net_bias','neutral').upper()} "
            f"({ins.get('buy_count',0)} purchases / {ins.get('sell_count',0)} sales)",
            "",
        ]
        for t in txns[:10]:
            val = (f"  ${t['value_usd']/1e6:.1f}M" if t.get("value_usd") and t["value_usd"] >= 1e6
                   else (f"  ${t['value_usd']/1e3:.0f}K" if t.get("value_usd") and t["value_usd"] > 0 else ""))
            i_lines.append(
                f"  {t['date']} | {t['insider'][:25]:<25} ({t['relation'][:20]}) "
                f"| {t['transaction']:<12} | {t['shares']:>10,} shares{val}"
            )
        sections.append("\n".join(i_lines))

    # ── SEC EDGAR 8-K filings ─────────────────────────────────────────────────
    if edgar.get("available"):
        filings = edgar.get("filings", [])
        e_lines = [f"\n=== SEC EDGAR RECENT 8-K FILINGS ({edgar.get('count',0)} in last 60 days) ==="]
        for f in filings[:6]:
            items = f.get("items", "")
            desc  = f.get("description", "")[:60]
            e_lines.append(f"  {f['date']} | {items[:50]} | {desc}")
        e_lines.append("Note: 8-K filings are mandatory disclosures of material corporate events.")
        sections.append("\n".join(e_lines))

    # ── Short squeeze potential ────────────────────────────────────────────────
    sq_score = squeeze.get("score", 0)
    if sq_score >= 30:
        sq_lines = [
            f"\n=== SHORT SQUEEZE POTENTIAL ===",
            f"Score: {sq_score}/100 — Level: {squeeze.get('level','').upper()}",
            f"Short Interest: {squeeze.get('short_pct','N/A')}% of float  |  Days to Cover: {squeeze.get('days_to_cover','N/A')}",
        ]
        for sig in squeeze.get("signals", []):
            sq_lines.append(f"  • {sig}")
        sections.append("\n".join(sq_lines))

    # ── Unusual options activity ───────────────────────────────────────────────
    if uopts.get("available"):
        u_lines = [
            f"\n=== OPTIONS ACTIVITY ===",
            f"Signal: {uopts.get('signal_label','')}",
            f"Call Vol: {uopts.get('total_call_vol',0):,}  Put Vol: {uopts.get('total_put_vol',0):,}  "
            f"C/P Ratio: {uopts.get('call_put_ratio','N/A')}",
            f"Call Vol/OI: {uopts.get('call_vol_oi_ratio','N/A')}x  Put Vol/OI: {uopts.get('put_vol_oi_ratio','N/A')}x",
        ]
        if uopts.get("unusual_calls"):
            u_lines.append("Top unusual call strikes:")
            for c in uopts["unusual_calls"][:3]:
                u_lines.append(f"  ${c['strike']} {c['expiry']} — Vol {c['volume']:,} vs OI {c['oi']:,} ({c['vol_oi']}x)")
        sections.append("\n".join(u_lines))

    # ── StockTwits sentiment ──────────────────────────────────────────────────
    if stkt.get("available"):
        br = stkt.get("bull_ratio", 0.5)
        sections.append(
            f"\n=== STOCKTWITS REAL-TIME SENTIMENT ===\n"
            f"Bull Ratio: {br*100:.0f}%  ({stkt.get('bull_count',0)} bull / {stkt.get('bear_count',0)} bear)  "
            f"Signal: {stkt.get('signal','neutral').replace('_',' ').upper()}\n"
            f"Message Volume: {stkt.get('total_messages',0)} recent  "
            f"Velocity: {stkt.get('msgs_per_hour','N/A')} msgs/hr\n"
            f"Note: Finance-specific platform — every post is explicitly tagged bullish/bearish."
        )

    # ── Institutional holders ─────────────────────────────────────────────────
    if inst.get("available"):
        i_lines = [
            f"\n=== INSTITUTIONAL OWNERSHIP ===",
            f"Institutional: {inst.get('inst_pct','N/A')}%  |  Insider: {inst.get('insider_pct','N/A')}%",
            f"Active filers: {inst.get('recent_filers',0)} of {inst.get('holder_count',0)} filed in last 60 days",
            "Top holders:",
        ]
        for h in inst.get("holders", [])[:6]:
            pct_str = f"({h['pct_out']:.1f}%)" if h.get("pct_out") else ""
            i_lines.append(f"  {h['name'][:35]:<35} {pct_str}")
        sections.append("\n".join(i_lines))

    # ── Government contracts ──────────────────────────────────────────────────
    if gov.get("available"):
        total = gov.get("total_value", 0) or 0
        g_lines = [
            f"\n=== GOVERNMENT CONTRACTS (last 12 months) ===",
            f"Total Value: ${total/1e6:.1f}M  |  Contracts: {gov.get('contract_count',0)}",
        ]
        for c in gov.get("contracts", [])[:4]:
            g_lines.append(f"  {c['date']} | ${c.get('amount',0)/1e6:.1f}M | {c.get('agency','')[:30]}")
        sections.append("\n".join(g_lines))

    # ── Sector rotation context ───────────────────────────────────────────────
    if sec_rot.get("available"):
        stock_etf = m.get("sector_etf", "")
        hot  = sec_rot.get("hot_sectors", [])
        cold = sec_rot.get("cold_sectors", [])
        flow_note = ""
        if stock_etf in hot:
            flow_note = f"⬆️ {stock_etf} has rising money flows — TAILWIND"
        elif stock_etf in cold:
            flow_note = f"⬇️ {stock_etf} has declining flows — HEADWIND for this stock"
        sections.append(
            f"\n=== SECTOR ROTATION ===\n"
            f"Stock sector ETF: {stock_etf or 'N/A'}\n"
            f"Hot (inflows): {', '.join(hot) or 'N/A'}\n"
            f"Cold (outflows): {', '.join(cold) or 'N/A'}\n"
            + (f"{flow_note}\n" if flow_note else "")
        )

    # ── Recent listing / IPO context ──────────────────────────────────────────
    if m.get("is_recent_listing"):
        age = m.get("listing_age_days", 0)
        sections.append(
            f"\n=== RECENT PUBLIC LISTING ===\n"
            f"Listed ~{age//30} months ago — post-IPO sweet spot where institutional "
            f"coverage is thin and analyst initiations create outsized re-ratings.\n"
            f"Higher return potential but also higher uncertainty than established companies."
        )

    # ── Portfolio ─────────────────────────────────────────────────────────────
    sections.append(f"""\n=== PORTFOLIO CONTEXT ===
Starting Balance: €100.00 (paper trading)
Cash Available: €{cash:.2f}
Current Holdings: {holdings}""")

    # ── Output Schema ─────────────────────────────────────────────────────────
    sections.append(f"""\n=== REQUIRED OUTPUT — JSON ONLY ===
{{
  "company_name": "...",
  "recommendation": "BUY" | "HOLD" | "SELL",
  "confidence": integer 1-10,
  "bull_thesis": "2-3 sentences. Cite specific numbers from the data above.",
  "bear_thesis": "2-3 sentences. Cite specific risks from the data above.",
  "risks": ["risk 1", "risk 2", "risk 3", "risk 4"],
  "target_price_usd": float or null,
  "time_horizon": "short-term (days)" | "medium-term (weeks)" | "long-term (months+)",
  "key_metrics_assessment": "1-2 sentences on valuation vs growth.",
  "macro_impact": "1 sentence on how current macro environment affects this stock.",
  "sentiment_assessment": "1 sentence on news and social sentiment signal.",
  "suitable_for_small_account": true | false,
  "suggested_position_size_eur": float (out of €{cash:.2f} available),
  "entry_condition": "Specific entry trigger: exact price level, technical condition, or event (e.g. 'Buy on pullback to $142 (50-day SMA)' or 'Buy on break above $155 with volume > 1.5x average')",
  "stop_loss_usd": float (exact price level below which thesis is invalidated — not a percentage),
  "take_profit_usd": float (first specific price target based on technical resistance or analyst target),
  "catalyst": "The single most important upcoming event or condition that could trigger the anticipated move"
}}""")

    return "\n".join(sections)
