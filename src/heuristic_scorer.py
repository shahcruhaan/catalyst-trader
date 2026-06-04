"""
src/heuristic_scorer.py — Keyword-Based Heuristic Catalyst Scorer

Scores news headlines and summaries using a comprehensive keyword dictionary
to determine whether a catalyst is strong enough to trade on automatically.

Works entirely offline — no API keys required.
Optional LLM integration is supported but not required (Stage 10+).

How it works:
  1. Combines all headlines + summaries into one text block (lowercased)
  2. Matches against keyword dictionaries for each catalyst type
  3. Assigns a base score based on the strongest matched category
  4. Applies bonuses, penalties, and caps
  5. Returns a fully transparent scoring result

Scoring guide:
  Score 9-10: Major catalyst — quantified, economically meaningful, fresh
              (earnings beat + guidance raise, FDA approval, large contract)
  Score 8:    Strong catalyst with clear business impact
  Score 6-7:  Interesting but not strong enough for automated trading
  Score 4-5:  Mixed signals or unclear impact
  Score 1-3:  Weak, vague, stale, risky, or negative catalyst

Automated trading rule:
  Only trade if score >= 8, confidence medium or high, no major negatives.

LLM interface:
  build_llm_prompt() and score_with_llm_if_enabled() are implemented but
  LLM calls are disabled by default. To enable, set
  catalyst_scoring.use_llm_if_available: true in config.yaml and add an
  OPENAI_API_KEY (primary) or ANTHROPIC_API_KEY (fallback) to .env.
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from src.utils import setup_logger

logger = setup_logger(__name__)


# ============================================================
# Keyword dictionaries
# ============================================================

# Strong bullish — base score 9+ per category match
STRONG_BULLISH_KEYWORDS: Dict[str, List[str]] = {
    "earnings_beat": [
        "earnings beat", "beat earnings", "beat estimates", "beat expectations",
        "beat consensus", "topped estimates", "topped expectations",
        "beats earnings", "beats estimates", "beats expectations", "beats consensus",
        "record earnings", "record revenue", "record profit", "record quarter",
        "blowout earnings", "blowout quarter", "blew past", "blew away estimates",
        "eps beat", "profit beat", "smashed estimates", "crushed estimates",
        "demolished estimates", "shattered estimates",
    ],
    "revenue_beat": [
        "revenue beat", "revenue exceeded", "revenue surpassed",
        "revenue topped", "record sales", "sales beat",
        "top-line beat", "quarterly revenue beat",
    ],
    "guidance_raise": [
        "raised guidance", "raised full-year guidance", "raised outlook",
        "raised annual guidance", "raised forecast", "raised earnings guidance",
        "raised revenue guidance", "boosted guidance", "increased guidance",
        "lifted guidance", "upped guidance", "raised full year",
        "guidance raised", "outlook raised", "forecast raised",
        "raised its full-year", "raised annual forecast",
    ],
    "major_contract": [
        "billion dollar contract", "billion-dollar contract",
        "multi-billion", "multi-year contract", "major contract win",
        "awarded contract", "won the contract", "government contract",
        "defense contract", "pentagon contract", "dod contract", "dod award",
        "u.s. military contract", "large customer deal", "landmark deal",
        "landmark contract", "historic deal", "record deal",
    ],
    "fda_approval": [
        "fda approved", "fda approval", "fda approves",
        "received fda approval", "granted fda approval",
        "fda clearance", "fda cleared", "fda authorized",
        "breakthrough therapy designation", "fast track designation",
        "accelerated approval", "priority review granted",
        "regulatory approval", "ema approval", "european approval",
    ],
    "acquisition_offer": [
        "acquisition offer", "takeover bid", "buyout offer",
        "agreed to acquire", "acquisition agreement", "merger agreement",
        "going private", "buyout deal", "strategic acquisition",
        "tender offer", "acquisition premium", "deal premium",
    ],
    "activist_stake": [
        "activist stake", "activist investor", "discloses stake",
        "13d filing", "13g filing", "built a stake", "acquired stake",
        "taken a position", "significant stake",
        "carl icahn", "nelson peltz", "bill ackman", "dan loeb",
        "starboard value", "third point", "elliott management",
    ],
    "ai_infrastructure": [
        "ai infrastructure deal", "ai chip demand", "data center demand",
        "hyperscaler demand", "cloud demand surge", "gpu demand surge",
        "ai investment surge", "capex increase for ai", "raised capex",
        "ai spending surge", "nvidia partnership",
    ],
}

# Moderate bullish — base score 7-8
MODERATE_BULLISH_KEYWORDS: Dict[str, List[str]] = {
    "analyst_upgrade": [
        "upgraded to buy", "upgraded to strong buy", "upgraded to overweight",
        "analyst upgrade", "raises price target", "raised price target",
        "increased price target", "boosted price target",
        "new buy rating", "initiates with buy", "initiates coverage with buy",
        "outperform rating", "positive catalyst watch",
    ],
    "partnership": [
        "strategic partnership", "exclusive partnership", "global partnership",
        "commercial agreement", "licensing deal", "distribution agreement",
        "joint venture", "collaboration agreement", "definitive agreement",
    ],
    "expansion": [
        "capacity expansion", "new facility", "new factory",
        "manufacturing expansion", "geographic expansion",
        "international expansion", "new market entry",
        "opens new", "product launch",
    ],
    "margin_expansion": [
        "margin expansion", "margin improvement", "improving margins",
        "higher margins", "gross margin increase", "operating margin up",
        "profitability improvement", "cost reduction achieved",
        "efficiency gains",
    ],
    "buyback": [
        "share buyback", "stock buyback", "repurchase program",
        "repurchase authorization", "buyback program",
        "returns capital to shareholders", "dividend increase",
        "dividend raise", "special dividend",
    ],
}

# Weak bullish — score bump only
WEAK_BULLISH_KEYWORDS: List[str] = [
    "collaboration", "new customer", "customer win",
    "market share gain", "analyst day", "investor day",
    "reiterated buy", "maintains buy", "upside potential",
    "positive outlook", "optimistic", "momentum", "recovery",
    "rebound", "turnaround story",
]

# Strong negative — significantly reduce score
STRONG_NEGATIVE_KEYWORDS: List[str] = [
    "missed earnings", "earnings miss", "missed estimates",
    "missed expectations", "revenue miss", "missed revenue",
    "guidance cut", "lowered guidance", "reduced guidance",
    "slashed guidance", "cut guidance", "guidance reduction",
    "warned of losses", "profit warning", "earnings warning",
    "sec investigation", "sec probe", "sec subpoena",
    "doj investigation", "doj probe", "accounting fraud",
    "restatement", "restated earnings", "material weakness",
    "going concern", "bankruptcy", "chapter 11", "chapter 7",
    "insolvency", "liquidity crisis",
    "dilutive offering", "dilutive share offering", "share dilution",
    "public offering at discount", "below market offering",
    "reverse split", "reverse stock split",
    "class action lawsuit", "securities fraud lawsuit",
    "insider selling", "executive selling", "ceo selling shares",
    "cfo resigned", "ceo resigned", "ceo departure", "ceo fired",
    "product recall", "safety recall", "fda rejection", "fda denied",
    "complete response letter", "trial failure", "phase 3 failed",
    "missed primary endpoint", "trial stopped early",
    "downgraded to sell", "downgraded to underperform",
    "downgraded to underweight", "analyst downgrade",
    "price target cut", "price target reduced",
    "customer loss", "lost major contract", "contract cancelled",
    "contract terminated",
]

# Hype-only — signals retail or meme momentum without fundamentals
HYPE_KEYWORDS: List[str] = [
    "reddit", "wallstreetbets", "wsb", "retail investors piling",
    "going viral", "trending on social", "social media buzz",
    "meme stock", "short squeeze play", "gamma squeeze",
    "options play", "yolo", "diamond hands",
    "to the moon", "moonshot", "10x potential",
    "next nvidia", "next tesla",
    "influencer", "youtube stock tip", "tiktok stock",
    "pump and dump",
]

# Catalyst type keyword mapping (for identify_catalyst_type)
_CATALYST_TYPE_MAP: Dict[str, List[str]] = {
    **{k: v for k, v in STRONG_BULLISH_KEYWORDS.items()},
    **{k: v for k, v in MODERATE_BULLISH_KEYWORDS.items()},
    "weak_bullish": WEAK_BULLISH_KEYWORDS,
}


# ============================================================
# Main scoring function
# ============================================================

def score_headline_heuristic(
    ticker: str,
    headlines: List[str],
    summaries: Optional[List[str]] = None,
    news_dates: Optional[List[str]] = None,
    max_news_age_days: int = 7,
) -> dict:
    """
    Score a set of headlines/summaries using keyword matching.

    Algorithm:
      1. Combine headlines + summaries into one lowercase text block
      2. Match against strong, moderate, and weak keyword dictionaries
      3. Compute base score from matched categories
      4. Apply freshness bonus (+1 if any news within 3 days)
      5. Apply negative flag penalty (reduces score by 2-8 points)
      6. Apply hype cap (max 4 if hype only, no fundamentals)
      7. Clip final score to [1, 10]

    Args:
        ticker:           Stock symbol
        headlines:        List of headlines for this ticker
        summaries:        Optional parallel list of summaries
        news_dates:       Optional parallel list of "YYYY-MM-DD HH:MM:SS" strings
        max_news_age_days: Used for freshness bonus threshold

    Returns:
        {
            "ticker": str,
            "catalyst_score": int (1-10, or 0 if no headlines),
            "tradeable": bool,
            "confidence": "low" | "medium" | "high",
            "catalyst_type": str,
            "one_sentence_reason": str,
            "bullish_mechanism": str,
            "risk": str,
            "matched_keywords": [str, ...],
            "negative_flags": [str, ...],
            "hype_flags": [str, ...],
            "score_method": "heuristic",
            "headlines_used": [str, ...],
        }
    """
    summaries = summaries or [""] * len(headlines)
    news_dates = news_dates or []

    combined = " ".join(
        f"{h} {s}" for h, s in zip(headlines, summaries) if h
    ).lower()

    if not combined.strip():
        return _no_catalyst_result(ticker)

    # --- Match bullish keywords ---
    matched_strong: Dict[str, List[str]] = {}
    matched_moderate: Dict[str, List[str]] = {}
    all_matched: List[str] = []

    for cat, keywords in STRONG_BULLISH_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in combined]
        if hits:
            matched_strong[cat] = hits
            all_matched.extend(hits[:2])

    for cat, keywords in MODERATE_BULLISH_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in combined]
        if hits:
            matched_moderate[cat] = hits
            all_matched.extend(hits[:1])

    weak_hits = [kw for kw in WEAK_BULLISH_KEYWORDS if kw in combined]

    # --- Match negative and hype flags ---
    negative_hits = [kw for kw in STRONG_NEGATIVE_KEYWORDS if kw in combined]
    hype_hits = [kw for kw in HYPE_KEYWORDS if kw in combined]

    # --- Compute base score ---
    if matched_strong:
        base_score = 10 if len(matched_strong) >= 2 else 9
    elif matched_moderate:
        base_score = 8 if len(matched_moderate) >= 2 else 7
    elif weak_hits:
        base_score = 5
    else:
        base_score = 3

    # --- Freshness bonus (applied before penalty caps so caps are absolute) ---
    pre_cap_score = base_score + _calc_freshness_bonus(news_dates)

    # --- Negative flag penalty ---
    if negative_hits:
        if len(negative_hits) >= 3:
            pre_cap_score = min(pre_cap_score, 2)
        elif len(negative_hits) >= 2:
            pre_cap_score = min(pre_cap_score, 4)
        else:
            pre_cap_score = min(pre_cap_score, 6)

    # --- Hype-only cap ---
    if hype_hits and not matched_strong and not matched_moderate:
        pre_cap_score = min(pre_cap_score, 4)

    # --- Final score ---
    final_score = min(10, max(1, pre_cap_score))

    # --- Derived fields ---
    catalyst_type = _determine_catalyst_type(matched_strong, matched_moderate, weak_hits, hype_hits)
    confidence = _determine_confidence(final_score, matched_strong, matched_moderate, negative_hits, hype_hits)
    tradeable = (
        final_score >= 8
        and confidence in ("medium", "high")
        and len(negative_hits) == 0
        and not (hype_hits and not matched_strong and not matched_moderate)
    )

    # Deduplicate matched keywords
    seen_kw: set = set()
    unique_matched = []
    for kw in all_matched:
        if kw not in seen_kw:
            seen_kw.add(kw)
            unique_matched.append(kw)

    return {
        "ticker": ticker.upper(),
        "catalyst_score": final_score,
        "tradeable": tradeable,
        "confidence": confidence,
        "catalyst_type": catalyst_type,
        "one_sentence_reason": _build_reason(catalyst_type, negative_hits, final_score),
        "bullish_mechanism": _build_mechanism(catalyst_type),
        "risk": _build_risk(negative_hits, hype_hits, final_score),
        "matched_keywords": unique_matched[:10],
        "negative_flags": negative_hits[:5],
        "hype_flags": hype_hits[:5],
        "score_method": "heuristic",
        "headlines_used": [h for h in headlines[:5] if h],
    }


# ============================================================
# Scoring helpers
# ============================================================

def _calc_freshness_bonus(news_dates: List[str]) -> int:
    """Return +1 if any news item is within 3 days of now, else 0."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    for dt_str in news_dates:
        if not dt_str:
            continue
        try:
            dt = datetime.strptime(str(dt_str)[:19], "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                return 1
        except (ValueError, TypeError):
            continue
    return 0


def _determine_catalyst_type(
    strong: Dict[str, List[str]],
    moderate: Dict[str, List[str]],
    weak: List[str],
    hype: List[str],
) -> str:
    """Return the primary catalyst type string in priority order."""
    # Combined earnings + guidance is the strongest possible catalyst
    if "earnings_beat" in strong and "guidance_raise" in strong:
        return "earnings_beat_and_guidance_raise"

    strong_priority = [
        "earnings_beat", "guidance_raise", "revenue_beat",
        "fda_approval", "acquisition_offer", "major_contract",
        "activist_stake", "ai_infrastructure",
    ]
    for cat in strong_priority:
        if cat in strong:
            return cat

    mod_priority = ["analyst_upgrade", "margin_expansion", "buyback", "partnership", "expansion"]
    for cat in mod_priority:
        if cat in moderate:
            return cat

    if weak:
        return "general_bullish"
    if hype:
        return "hype_only"
    return "unknown"


def _determine_confidence(
    score: int,
    strong: Dict,
    moderate: Dict,
    negative: List[str],
    hype: List[str],
) -> str:
    """Return 'high', 'medium', or 'low'."""
    if negative:
        return "low"
    if hype and not strong and not moderate:
        return "low"
    if score >= 9 and strong:
        return "high"
    if score >= 8 and (strong or len(moderate) >= 2):
        return "medium"
    if score >= 7 and moderate:
        return "medium"
    return "low"


# ============================================================
# Human-readable explanation builders
# ============================================================

_REASON_MAP: Dict[str, str] = {
    "earnings_beat_and_guidance_raise": "Company beat earnings estimates and raised forward guidance — the strongest possible catalyst.",
    "earnings_beat": "Company reported earnings above analyst estimates.",
    "revenue_beat": "Company reported revenue above analyst estimates.",
    "guidance_raise": "Company raised its forward earnings or revenue guidance.",
    "fda_approval": "Company received FDA regulatory approval.",
    "major_contract": "Company won a major contract or large customer deal.",
    "acquisition_offer": "Company received an acquisition offer or announced a merger.",
    "activist_stake": "Activist investor disclosed a significant stake.",
    "ai_infrastructure": "Company benefits from AI infrastructure demand surge.",
    "analyst_upgrade": "Analyst upgraded the stock or raised price target.",
    "partnership": "Company announced a strategic partnership.",
    "expansion": "Company announced meaningful capacity or geographic expansion.",
    "margin_expansion": "Company showed improving margins or profitability.",
    "buyback": "Company announced a share buyback or increased dividend.",
    "general_bullish": "Multiple bullish signals detected in recent headlines.",
    "hype_only": "Social media or retail momentum without clear fundamental catalyst.",
    "unknown": "No clear catalyst pattern identified in recent headlines.",
}

_MECHANISM_MAP: Dict[str, str] = {
    "earnings_beat_and_guidance_raise": "Beat + raise surprises both current and forward expectations, driving institutional re-rating.",
    "earnings_beat": "Earnings strength signals healthy fundamentals and may drive institutional buying.",
    "revenue_beat": "Revenue beat suggests demand is stronger than expected, supporting higher valuation.",
    "guidance_raise": "Raised guidance increases the forward EPS estimate, mechanically lifting fair value.",
    "fda_approval": "Regulatory approval opens a new revenue stream or de-risks the pipeline.",
    "major_contract": "Large contract provides visible, recurring revenue and validates competitive position.",
    "acquisition_offer": "Acquisition bid creates a price floor and often includes a premium.",
    "activist_stake": "Activist involvement signals potential for strategic changes, buybacks, or a sale process.",
    "ai_infrastructure": "Rising AI capex from hyperscalers drives demand for the company's products or services.",
    "analyst_upgrade": "Analyst upgrade may drive institutional visibility and buying.",
    "partnership": "Strategic partnerships can expand addressable market or create new revenue streams.",
    "expansion": "Capacity expansion signals management confidence and can support revenue growth.",
    "margin_expansion": "Improving margins drive earnings growth even if revenue growth is modest.",
    "buyback": "Capital return programs reduce share count, increasing EPS and signaling confidence.",
    "general_bullish": "Positive news signals may attract momentum buyers.",
    "hype_only": "No fundamental mechanism — driven by retail sentiment only.",
    "unknown": "No clear fundamental mechanism identified.",
}


def _build_reason(catalyst_type: str, negative: List[str], score: int) -> str:
    if negative and score < 5:
        return f"Negative indicators detected: {', '.join(negative[:2])}."
    return _REASON_MAP.get(catalyst_type, f"Score {score}/10 based on keyword analysis.")


def _build_mechanism(catalyst_type: str) -> str:
    return _MECHANISM_MAP.get(catalyst_type, "Potential positive impact on future earnings or valuation.")


def _build_risk(negative: List[str], hype: List[str], score: int) -> str:
    if negative:
        return f"Negative indicators present: {', '.join(negative[:2])}. High risk of continued decline."
    if hype and score < 6:
        return "Appears to be retail/social media momentum without fundamental catalyst. High reversal risk."
    if score >= 9:
        return "High expectations priced in. Post-catalyst fade risk if next report disappoints."
    if score >= 7:
        return "Catalyst may already be partially priced in. Monitor for fading volume."
    return "Limited catalyst signal. Risk that position lacks fundamental support."


def _no_catalyst_result(ticker: str) -> dict:
    """Return a zero-score result when no headlines are available."""
    return {
        "ticker": ticker.upper(),
        "catalyst_score": 0,
        "tradeable": False,
        "confidence": "low",
        "catalyst_type": "no_news",
        "one_sentence_reason": "No news headlines available for this ticker.",
        "bullish_mechanism": "N/A — no news found.",
        "risk": "No news means no fundamental reason to enter.",
        "matched_keywords": [],
        "negative_flags": [],
        "hype_flags": [],
        "score_method": "heuristic",
        "headlines_used": [],
    }


# ============================================================
# Batch scorer
# ============================================================

def score_all_tickers(
    news_by_ticker: Dict[str, dict],
    max_news_age_days: int = 7,
) -> Dict[str, dict]:
    """
    Score catalysts for all tickers in one call.

    Args:
        news_by_ticker: {ticker: formatted_news_dict}
                        (output of format_news_for_catalyst_scorer())
        max_news_age_days: Maximum age for freshness bonus

    Returns:
        {ticker: score_result_dict}
    """
    results: Dict[str, dict] = {}

    for ticker, news_data in news_by_ticker.items():
        try:
            result = score_headline_heuristic(
                ticker=ticker,
                headlines=news_data.get("headlines", []),
                summaries=news_data.get("summaries", []),
                news_dates=news_data.get("dates", []),
                max_news_age_days=max_news_age_days,
            )
            results[ticker] = result
        except Exception as e:
            logger.warning(f"heuristic_scorer: error scoring {ticker}: {e}")
            results[ticker] = _no_catalyst_result(ticker)

    return results


# ============================================================
# LLM-ready interface (optional, Stage 10+)
# ============================================================

def build_llm_prompt(
    ticker: str,
    headlines: List[str],
    summaries: Optional[List[str]] = None,
) -> str:
    """
    Build a structured prompt for LLM-based catalyst scoring.

    This prompt is designed for Claude or GPT-4. To enable LLM scoring:
      1. Add ANTHROPIC_API_KEY or OPENAI_API_KEY to .env
      2. Set catalyst_scoring.use_llm_if_available: true in config.yaml
      3. Implement the API call in score_with_llm_if_enabled()

    Returns the prompt string ready to send to an LLM.
    """
    summaries = summaries or [""] * len(headlines)
    news_block = "\n".join(
        f"- {h}" + (f"\n  Summary: {s}" if s else "")
        for h, s in zip(headlines, summaries)
        if h
    )

    return f"""You are a professional equity trader analyzing whether a news catalyst justifies entering a momentum breakout trade on {ticker}.

Recent news headlines:
{news_block}

Score this catalyst from 1-10 and provide your assessment in this exact JSON format:
{{
  "catalyst_score": <integer 1-10>,
  "tradeable": <true or false>,
  "confidence": "<low or medium or high>",
  "catalyst_type": "<type>",
  "one_sentence_reason": "<one sentence>",
  "bullish_mechanism": "<how this drives the business>",
  "risk": "<main downside risk>",
  "negative_flags": ["<flag1>", "<flag2>"]
}}

Scoring guide:
- 9-10: Major catalyst (earnings beat + guidance raise, FDA approval, large contract, acquisition offer)
- 8: Strong catalyst with clear business impact, not vague
- 6-7: Interesting but insufficient for automated trading
- 1-5: Weak, vague, stale, or contains negative news

Only set tradeable=true if: score >= 8, confidence medium or high, no major negative flags."""


def score_with_llm_if_enabled(
    ticker: str,
    headlines: List[str],
    summaries: Optional[List[str]] = None,
    config=None,
) -> Optional[dict]:
    """
    Score a catalyst with an LLM if enabled in config and API keys are present.

    Falls back to None (heuristic is used) if:
    - LLM scoring is not enabled in config
    - No API key is found
    - LLM call fails for any reason

    This is a placeholder. Actual API calls will be added in a future stage.
    """
    if config is None:
        return None

    use_llm = getattr(config, "use_llm_if_available", False)
    if not use_llm:
        return None

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()

    if not openai_key and not anthropic_key:
        logger.debug(f"score_with_llm_if_enabled: no API keys available for {ticker}")
        return None

    prompt = build_llm_prompt(ticker, headlines, summaries)

    # --- OpenAI (GPT-4o-mini primary) ---
    if openai_key:
        try:
            import openai
            client = openai.OpenAI(api_key=openai_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = response.choices[0].message.content or ""
            result = parse_llm_response(response_text, ticker)
            if result:
                result["score_method"] = "llm_openai"
                logger.info(
                    f"LLM scored {ticker}: {result['catalyst_score']}/10 "
                    f"({result['confidence']}) — {result.get('catalyst_type','')}"
                )
                return result
        except Exception as e:
            logger.warning(f"score_with_llm_if_enabled: OpenAI call failed for {ticker}: {e}")

    # --- Anthropic (Claude Haiku fallback) ---
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            response_text = message.content[0].text if message.content else ""
            result = parse_llm_response(response_text, ticker)
            if result:
                result["score_method"] = "llm_anthropic"
                logger.info(
                    f"LLM scored {ticker}: {result['catalyst_score']}/10 "
                    f"({result['confidence']}) — {result.get('catalyst_type','')}"
                )
                return result
        except Exception as e:
            logger.warning(f"score_with_llm_if_enabled: Anthropic call failed for {ticker}: {e}")

    return None


def parse_llm_response(response_text: str, ticker: str) -> Optional[dict]:
    """
    Parse a JSON response from an LLM into a standardized catalyst score dict.

    Validates all required fields. Returns None if the response is malformed
    or the score is out of the 1-10 range.

    Args:
        response_text: Raw text from the LLM (may contain markdown code blocks)
        ticker:        Stock symbol

    Returns:
        Validated score dict, or None if parsing fails.
    """
    import json

    try:
        # Strip markdown code blocks if present
        text = response_text
        if "```" in text:
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)

        data = json.loads(text)

        score = int(data.get("catalyst_score", 0))
        if not (1 <= score <= 10):
            return None

        return {
            "ticker": ticker.upper(),
            "catalyst_score": score,
            "tradeable": bool(data.get("tradeable", False)),
            "confidence": str(data.get("confidence", "low")),
            "catalyst_type": str(data.get("catalyst_type", "unknown")),
            "one_sentence_reason": str(data.get("one_sentence_reason", ""))[:300],
            "bullish_mechanism": str(data.get("bullish_mechanism", ""))[:300],
            "risk": str(data.get("risk", ""))[:300],
            "matched_keywords": [],
            "negative_flags": list(data.get("negative_flags", []))[:5],
            "hype_flags": [],
            "score_method": "llm",
            "headlines_used": [],
        }

    except Exception as e:
        logger.debug(f"parse_llm_response: failed for {ticker}: {e}")
        return None
