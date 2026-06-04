"""
src/news_discovery.py — Automated News Discovery

Pulls recent headlines and summaries for every stock in the discovered universe.
Supports multiple news providers with automatic fallback.

Provider hierarchy (tried in order):
  1. AlpacaNewsProvider  — Alpaca Market Data News API (free with any Alpaca account)
  2. YahooFinanceNewsProvider — yfinance Ticker.news (free, no key required)
  3. ManualCSVNewsProvider — reads existing reports/news_cache.csv (last resort)

If all providers fail for a symbol, it is flagged in reports/needs_catalyst_review.csv
for manual review.

Future provider placeholders (not yet implemented):
  - BenzingaNewsProvider (requires paid API key)
  - FinnhubNewsProvider (requires Finnhub API key)
  - PolygonNewsProvider (requires Polygon.io subscription)
  - SECEDGARProvider (free SEC 8-K filings — good for material events)

Output file: reports/news_cache.csv

news_cache.csv columns:
  timestamp_pulled      — when the bot downloaded this item
  news_datetime         — when the news was originally published
  ticker                — stock symbol
  headline              — one-line headline
  summary               — brief summary paragraph (if available)
  source                — publisher name (e.g. "Reuters", "Seeking Alpha")
  url                   — link to original article
  symbols_mentioned     — other tickers mentioned (comma-separated)
  provider              — which provider returned this item
  is_stale              — True if older than max_age_days
  is_duplicate          — True if headline already seen for this ticker
  passed_quality_filter — True if passes all quality checks

Usage:
  from src.news_discovery import NewsDiscovery

  nd = NewsDiscovery(config)
  all_news, no_news_tickers = nd.get_news_for_discovered_universe(tickers)
  nd.cache_news_to_csv(all_news)
"""

import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.utils import setup_logger, ensure_dir, now_str, load_csv_safe

logger = setup_logger(__name__)

_DEFAULT_NEWS_CACHE = "reports/news_cache.csv"
_DEFAULT_DAYS_BACK = 7
_DEFAULT_MAX_AGE_DAYS = 7
_DEFAULT_MAX_HEADLINES = 10

NEWS_CSV_COLUMNS = [
    "timestamp_pulled", "news_datetime", "ticker", "headline", "summary",
    "source", "url", "symbols_mentioned", "provider",
    "is_stale", "is_duplicate", "passed_quality_filter",
]

_LOW_QUALITY_PATTERNS = [
    "sponsored", "advertis", "press release", "promo", "promotion",
    "paid content", "click here", "sign up now",
]


# ============================================================
# News item factory
# ============================================================

def _make_news_item(
    ticker: str,
    headline: str,
    news_datetime: str = "",
    summary: str = "",
    source: str = "",
    url: str = "",
    symbols_mentioned: str = "",
    provider: str = "",
) -> dict:
    """Create a standardized news item dict with all required fields."""
    return {
        "timestamp_pulled": now_str(),
        "news_datetime": news_datetime,
        "ticker": str(ticker).strip().upper(),
        "headline": str(headline).strip(),
        "summary": str(summary).strip(),
        "source": str(source).strip(),
        "url": str(url).strip(),
        "symbols_mentioned": str(symbols_mentioned).strip(),
        "provider": str(provider).strip(),
        "is_stale": False,
        "is_duplicate": False,
        "passed_quality_filter": True,
    }


# ============================================================
# Abstract provider interface
# ============================================================

class BaseNewsProvider(ABC):
    """
    Abstract interface that all news providers must implement.

    To add a new provider in a future stage:
      1. Subclass BaseNewsProvider
      2. Implement the name property and get_news_for_symbol()
      3. Optionally override get_news_for_symbols() for batch efficiency
      4. Add the new provider to NewsDiscovery._build_provider_chain()
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short name used for logging and the 'provider' field in news items."""
        ...

    @abstractmethod
    def get_news_for_symbol(
        self,
        symbol: str,
        days_back: int = _DEFAULT_DAYS_BACK,
        max_items: int = _DEFAULT_MAX_HEADLINES,
    ) -> List[dict]:
        """
        Pull recent news for a single symbol.

        Must return a list of dicts from _make_news_item().
        Must return an empty list (not raise) if no news found or on error.
        """
        ...

    def get_news_for_symbols(
        self,
        symbols: List[str],
        days_back: int = _DEFAULT_DAYS_BACK,
        max_items_per_symbol: int = _DEFAULT_MAX_HEADLINES,
    ) -> List[dict]:
        """
        Pull news for multiple symbols.

        Default implementation calls get_news_for_symbol() in a loop.
        Providers that support batch requests should override this method
        to use a single API call for efficiency.
        """
        all_news = []
        for sym in symbols:
            try:
                items = self.get_news_for_symbol(sym, days_back, max_items_per_symbol)
                all_news.extend(items)
            except Exception as e:
                logger.debug(f"{self.name}: error fetching {sym}: {e}")
        return all_news


# ============================================================
# Alpaca News Provider
# ============================================================

class AlpacaNewsProvider(BaseNewsProvider):
    """
    Pulls news from Alpaca's Market Data News API.

    Requirements:
      - ALPACA_API_KEY and ALPACA_SECRET_KEY in .env
      - alpaca-py installed (pip install alpaca-py)
      - Free with all Alpaca accounts

    Uses alpaca-py's NewsClient (alpaca.data.historical.NewsClient).
    Falls back gracefully (returns empty lists) if alpaca-py is not installed
    or credentials are missing.
    """

    def __init__(self, api_key: str = "", secret_key: str = "") -> None:
        self._api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self._secret_key = secret_key or os.getenv("ALPACA_SECRET_KEY", "")
        self._client = None
        self._available = False
        self._init_client()

    def _init_client(self) -> None:
        """Try to initialize the Alpaca NewsClient."""
        if not self._api_key or not self._secret_key:
            logger.debug("AlpacaNewsProvider: no API keys — provider unavailable")
            return
        try:
            from alpaca.data.historical import NewsClient
            self._client = NewsClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
            self._available = True
            logger.debug("AlpacaNewsProvider: initialized")
        except ImportError:
            logger.debug("AlpacaNewsProvider: alpaca-py NewsClient not found — skipping")
        except Exception as e:
            logger.debug(f"AlpacaNewsProvider: init error: {e}")

    @property
    def name(self) -> str:
        return "alpaca"

    @property
    def is_available(self) -> bool:
        return self._available

    def get_news_for_symbol(
        self,
        symbol: str,
        days_back: int = _DEFAULT_DAYS_BACK,
        max_items: int = _DEFAULT_MAX_HEADLINES,
    ) -> List[dict]:
        """Pull news from Alpaca API for one symbol."""
        if not self._available or self._client is None:
            return []
        try:
            from alpaca.data.requests import NewsRequest

            end_dt = datetime.now(timezone.utc)
            start_dt = end_dt - timedelta(days=days_back)

            request = NewsRequest(
                symbols=symbol.upper(),
                start=start_dt,
                end=end_dt,
                limit=max_items,
            )
            response = self._client.get_news(request)
            news_list = response.data.get("news", []) if hasattr(response, "data") else []

            items = []
            for article in news_list:
                pub_dt = _parse_alpaca_dt(article)
                other_symbols = _parse_alpaca_symbols(article, symbol)
                items.append(_make_news_item(
                    ticker=symbol,
                    headline=str(getattr(article, "headline", "") or ""),
                    news_datetime=pub_dt,
                    summary=str(getattr(article, "summary", "") or "")[:500],
                    source=str(getattr(article, "source", "") or ""),
                    url=str(getattr(article, "url", "") or ""),
                    symbols_mentioned=other_symbols,
                    provider=self.name,
                ))

            logger.debug(f"AlpacaNewsProvider: {len(items)} articles for {symbol}")
            return items

        except Exception as e:
            logger.debug(f"AlpacaNewsProvider: error for {symbol}: {e}")
            return []

    def get_news_for_symbols(
        self,
        symbols: List[str],
        days_back: int = _DEFAULT_DAYS_BACK,
        max_items_per_symbol: int = _DEFAULT_MAX_HEADLINES,
    ) -> List[dict]:
        """Batch pull: fetch news for up to 50 symbols at once."""
        if not self._available or self._client is None:
            return []
        if not symbols:
            return []

        try:
            from alpaca.data.requests import NewsRequest

            end_dt = datetime.now(timezone.utc)
            start_dt = end_dt - timedelta(days=days_back)

            all_items: List[dict] = []
            BATCH_SIZE = 50

            for i in range(0, len(symbols), BATCH_SIZE):
                batch = [s.upper() for s in symbols[i : i + BATCH_SIZE]]
                try:
                    request = NewsRequest(
                        symbols=",".join(batch),
                        start=start_dt,
                        end=end_dt,
                        limit=min(len(batch) * max_items_per_symbol, 1000),
                    )
                    response = self._client.get_news(request)
                    news_list = response.data.get("news", []) if hasattr(response, "data") else []

                    for article in news_list:
                        syms = getattr(article, "symbols", []) or []
                        primary = syms[0] if syms else ""
                        if not primary:
                            continue
                        pub_dt = _parse_alpaca_dt(article)
                        other_syms = ",".join(syms[1:5]) if len(syms) > 1 else ""
                        all_items.append(_make_news_item(
                            ticker=primary,
                            headline=str(getattr(article, "headline", "") or ""),
                            news_datetime=pub_dt,
                            summary=str(getattr(article, "summary", "") or "")[:500],
                            source=str(getattr(article, "source", "") or ""),
                            url=str(getattr(article, "url", "") or ""),
                            symbols_mentioned=other_syms,
                            provider=self.name,
                        ))
                except Exception as e:
                    logger.debug(f"AlpacaNewsProvider: batch error for symbols {batch[:3]}...: {e}")
                    continue

            logger.info(
                f"AlpacaNewsProvider: {len(all_items)} articles "
                f"for {len(symbols)} symbols"
            )
            return all_items

        except Exception as e:
            logger.warning(f"AlpacaNewsProvider: batch fetch failed: {e}")
            return []


def _parse_alpaca_dt(article) -> str:
    """Extract a YYYY-MM-DD HH:MM:SS string from an Alpaca news article."""
    try:
        for attr in ("created_at", "updated_at"):
            val = getattr(article, attr, None)
            if val:
                return str(val)[:19].replace("T", " ")
    except Exception:
        pass
    return ""


def _parse_alpaca_symbols(article, primary_symbol: str) -> str:
    """Return comma-separated other symbols mentioned (excluding primary)."""
    try:
        syms = getattr(article, "symbols", []) or []
        others = [s for s in syms if s != primary_symbol.upper()]
        return ",".join(others[:5])
    except Exception:
        return ""


# ============================================================
# Yahoo Finance News Provider
# ============================================================

class YahooFinanceNewsProvider(BaseNewsProvider):
    """
    Pulls news using yfinance's Ticker.news property.

    Requirements:
      - yfinance installed (already in requirements.txt)
      - No API key needed

    Limitations:
      - Returns only the most recent ~10 headlines
      - Timestamps are unreliable on some tickers
      - No summaries for most articles
    """

    @property
    def name(self) -> str:
        return "yahoo"

    def get_news_for_symbol(
        self,
        symbol: str,
        days_back: int = _DEFAULT_DAYS_BACK,
        max_items: int = _DEFAULT_MAX_HEADLINES,
    ) -> List[dict]:
        """Pull news for one symbol using yfinance."""
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol.upper())
            raw_news = ticker.news or []
        except Exception as e:
            logger.debug(f"YahooFinanceNewsProvider: error for {symbol}: {e}")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        items = []

        for article in raw_news[:max_items]:
            try:
                headline = str(article.get("title", "") or "").strip()
                if not headline:
                    continue

                # providePublishTime is a Unix timestamp in yfinance
                pub_ts = (
                    article.get("providePublishTime")
                    or article.get("providerPublishTime", 0)
                )
                pub_dt = ""
                try:
                    pub_dt_obj = datetime.fromtimestamp(int(pub_ts), tz=timezone.utc)
                    if pub_dt_obj < cutoff:
                        continue
                    pub_dt = pub_dt_obj.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError, OSError):
                    pass

                items.append(_make_news_item(
                    ticker=symbol,
                    headline=headline,
                    news_datetime=pub_dt,
                    summary=str(article.get("summary", "") or "")[:500],
                    source=str(article.get("publisher", "") or ""),
                    url=str(article.get("link", "") or ""),
                    provider=self.name,
                ))
            except Exception:
                continue

        logger.debug(f"YahooFinanceNewsProvider: {len(items)} articles for {symbol}")
        return items


# ============================================================
# Manual CSV News Provider (fallback / cached)
# ============================================================

class ManualCSVNewsProvider(BaseNewsProvider):
    """
    Returns news items previously saved to reports/news_cache.csv.

    This is the fallback of last resort. It returns whatever was saved
    last time pull-news ran, even if slightly stale.

    You can also add rows to news_cache.csv manually. The heuristic scorer
    will pick them up on the next score-catalysts run.
    """

    def __init__(self, cache_path: str = _DEFAULT_NEWS_CACHE) -> None:
        self._cache_path = cache_path
        self._cache: Optional[pd.DataFrame] = None

    @property
    def name(self) -> str:
        return "manual_csv"

    def _load_cache(self) -> Optional[pd.DataFrame]:
        if self._cache is None:
            self._cache = load_csv_safe(self._cache_path)
        return self._cache

    def get_news_for_symbol(
        self,
        symbol: str,
        days_back: int = _DEFAULT_DAYS_BACK,
        max_items: int = _DEFAULT_MAX_HEADLINES,
    ) -> List[dict]:
        """Return cached news items for a symbol."""
        df = self._load_cache()
        if df is None or df.empty or "ticker" not in df.columns:
            return []

        sym = symbol.upper()
        rows = df[df["ticker"].astype(str).str.upper() == sym]
        if rows.empty:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
        items = []

        for _, row in rows.head(max_items).iterrows():
            try:
                dt_str = str(row.get("news_datetime", "") or "")
                if dt_str and dt_str not in ("nan", "None", ""):
                    try:
                        dt = datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
                        dt = dt.replace(tzinfo=timezone.utc)
                        if dt < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass

                items.append(_make_news_item(
                    ticker=sym,
                    headline=str(row.get("headline", "") or ""),
                    news_datetime=dt_str,
                    summary=str(row.get("summary", "") or ""),
                    source=str(row.get("source", "") or ""),
                    url=str(row.get("url", "") or ""),
                    symbols_mentioned=str(row.get("symbols_mentioned", "") or ""),
                    provider="manual_csv",
                ))
            except Exception:
                continue

        return items


# ============================================================
# Future provider placeholders
# ============================================================

class BenzingaNewsProvider(BaseNewsProvider):
    """PLACEHOLDER — Requires paid Benzinga Pro API key. Not yet implemented."""

    @property
    def name(self) -> str:
        return "benzinga"

    def get_news_for_symbol(self, symbol, days_back=7, max_items=10):
        logger.debug("BenzingaNewsProvider: not implemented (requires paid API key)")
        return []


class FinnhubNewsProvider(BaseNewsProvider):
    """PLACEHOLDER — Requires Finnhub API key at finnhub.io."""

    @property
    def name(self) -> str:
        return "finnhub"

    def get_news_for_symbol(self, symbol, days_back=7, max_items=10):
        logger.debug("FinnhubNewsProvider: not implemented (requires Finnhub API key)")
        return []


class PolygonNewsProvider(BaseNewsProvider):
    """PLACEHOLDER — Requires Polygon.io subscription."""

    @property
    def name(self) -> str:
        return "polygon"

    def get_news_for_symbol(self, symbol, days_back=7, max_items=10):
        logger.debug("PolygonNewsProvider: not implemented (requires Polygon.io subscription)")
        return []


class SECEDGARProvider(BaseNewsProvider):
    """PLACEHOLDER — Reads SEC EDGAR 8-K filings (free, material events)."""

    @property
    def name(self) -> str:
        return "sec_edgar"

    def get_news_for_symbol(self, symbol, days_back=7, max_items=10):
        logger.debug("SECEDGARProvider: not implemented yet")
        return []


# ============================================================
# Filtering functions
# ============================================================

def filter_stale_news(
    news_items: List[dict],
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
) -> List[dict]:
    """
    Mark news items as stale if they are older than max_age_days.

    Does NOT remove items — sets is_stale=True so the cache preserves
    the full history while the scorer can filter them out.

    Items without a parseable news_datetime are treated as NOT stale
    (we can't prove they are old).

    Args:
        news_items:   List of news item dicts
        max_age_days: Maximum age in calendar days

    Returns:
        Same list with is_stale field updated in-place.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    for item in news_items:
        dt_str = str(item.get("news_datetime", "") or "")
        if not dt_str or dt_str in ("nan", "None", ""):
            item["is_stale"] = False
            continue
        try:
            dt = datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            item["is_stale"] = dt < cutoff
        except (ValueError, TypeError):
            item["is_stale"] = False

    return news_items


def filter_low_quality_news(news_items: List[dict]) -> List[dict]:
    """
    Mark low-quality news items as not passing the quality filter.

    An item fails quality if:
    - Headline is empty or fewer than 10 characters
    - Headline contains known low-quality patterns (sponsored, ads, etc.)

    Does NOT remove items — sets passed_quality_filter=False.

    Args:
        news_items: List of news item dicts

    Returns:
        Same list with passed_quality_filter updated in-place.
    """
    for item in news_items:
        headline = str(item.get("headline", "") or "").strip().lower()

        if len(headline) < 10:
            item["passed_quality_filter"] = False
            continue

        if any(pat in headline for pat in _LOW_QUALITY_PATTERNS):
            item["passed_quality_filter"] = False
            continue

        item["passed_quality_filter"] = True

    return news_items


def deduplicate_headlines(news_items: List[dict]) -> List[dict]:
    """
    Mark duplicate headlines as is_duplicate=True.

    Two headlines are duplicates if they are identical after lowercasing
    and stripping whitespace for the same ticker. The first occurrence
    is kept; subsequent identical headlines are flagged.

    Does NOT remove items — sets is_duplicate=True for reporting.

    Args:
        news_items: List of news item dicts

    Returns:
        Same list with is_duplicate field updated in-place.
    """
    seen: Dict[str, set] = {}  # ticker -> set of normalized headlines

    for item in news_items:
        ticker = str(item.get("ticker", "") or "").upper()
        headline = str(item.get("headline", "") or "").strip().lower()

        if not headline:
            item["is_duplicate"] = False
            continue

        if ticker not in seen:
            seen[ticker] = set()

        if headline in seen[ticker]:
            item["is_duplicate"] = True
        else:
            seen[ticker].add(headline)
            item["is_duplicate"] = False

    return news_items


# ============================================================
# Caching
# ============================================================

def cache_news_to_csv(
    news_items: List[dict],
    path: str = _DEFAULT_NEWS_CACHE,
    append: bool = False,
) -> int:
    """
    Save news items to reports/news_cache.csv.

    By default, overwrites the entire file (today's fresh pull).
    Set append=True to add rows to an existing cache file.

    Args:
        news_items: List of news item dicts
        path:       Output path
        append:     If True, append to existing file; if False, overwrite

    Returns:
        Number of rows written.
    """
    if not news_items:
        logger.info(f"cache_news_to_csv: no items to save")
        return 0

    ensure_dir("reports")

    rows = [{col: item.get(col, "") for col in NEWS_CSV_COLUMNS} for item in news_items]
    df = pd.DataFrame(rows, columns=NEWS_CSV_COLUMNS)

    if append and os.path.exists(path):
        existing = load_csv_safe(path)
        if existing is not None and not existing.empty:
            df = pd.concat([existing, df], ignore_index=True)

    df.to_csv(path, index=False)
    logger.info(f"cache_news_to_csv: saved {len(rows)} items to {path}")
    return len(rows)


def load_cached_news(
    path: str = _DEFAULT_NEWS_CACHE,
    ticker: Optional[str] = None,
    max_age_days: int = _DEFAULT_MAX_AGE_DAYS,
) -> List[dict]:
    """
    Load news items from the cache CSV.

    Args:
        path:         Path to news_cache.csv
        ticker:       If provided, return only news for this ticker
        max_age_days: Only return news newer than this many days

    Returns:
        List of news item dicts (empty list if nothing found).
    """
    df = load_csv_safe(path)
    if df is None or df.empty:
        return []

    if ticker:
        df = df[df["ticker"].astype(str).str.upper() == ticker.upper()]

    if df.empty:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    items = []

    for _, row in df.iterrows():
        try:
            dt_str = str(row.get("news_datetime", "") or "")
            if dt_str and dt_str not in ("nan", "None", ""):
                try:
                    dt = datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass

            item = {col: str(row.get(col, "") or "") for col in NEWS_CSV_COLUMNS if col in row.index}
            items.append(item)
        except Exception:
            continue

    return items


# ============================================================
# Format for catalyst scorer
# ============================================================

def format_news_for_catalyst_scorer(
    symbol: str,
    news_items: List[dict],
    max_items: int = 5,
) -> dict:
    """
    Format a list of news items into the input format expected by the
    heuristic catalyst scorer.

    Filters to quality-passing, non-stale, non-duplicate items.
    Sorts by news_datetime descending (freshest first).

    Args:
        symbol:     Stock ticker
        news_items: List of news item dicts
        max_items:  Maximum items to include (default 5)

    Returns:
        {
            "ticker": str,
            "headlines": [str, ...],
            "summaries": [str, ...],
            "sources": [str, ...],
            "dates": [str, ...],
            "combined_text": str,    — headlines + summaries joined for keyword search
        }
    """
    quality = [
        item for item in news_items
        if str(item.get("passed_quality_filter", True)).lower() not in ("false", "0")
        and str(item.get("is_stale", False)).lower() not in ("true", "1")
        and str(item.get("is_duplicate", False)).lower() not in ("true", "1")
    ]

    def _parse_dt(item: dict) -> datetime:
        s = str(item.get("news_datetime", "") or "")
        try:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return datetime.min

    quality.sort(key=_parse_dt, reverse=True)
    top = quality[:max_items]

    headlines = [str(i.get("headline", "") or "").strip() for i in top]
    summaries = [str(i.get("summary", "") or "").strip() for i in top]
    sources = [str(i.get("source", "") or "").strip() for i in top]
    dates = [str(i.get("news_datetime", "") or "").strip() for i in top]

    combined = " ".join(
        f"{h} {s}" for h, s in zip(headlines, summaries) if h
    ).strip()

    return {
        "ticker": symbol.upper(),
        "headlines": headlines,
        "summaries": summaries,
        "sources": sources,
        "dates": dates,
        "combined_text": combined,
    }


# ============================================================
# Main orchestration class
# ============================================================

class NewsDiscovery:
    """
    Orchestrates news discovery across multiple providers with automatic fallback.

    Tries providers in order (Alpaca → Yahoo → ManualCSV).
    Alpaca batch endpoint is used first for efficiency (one API call for all symbols).
    Yahoo is used as a fallback for symbols Alpaca missed.
    ManualCSV is the last resort (returns previously cached items).

    Symbols with no news from any provider are returned in the no_news_symbols list
    and flagged for manual review.

    Usage:
        nd = NewsDiscovery(config)
        all_news, no_news_tickers = nd.get_news_for_discovered_universe(tickers)
        nd.cache_news_to_csv(all_news)
    """

    def __init__(self, config=None) -> None:
        self._config = config
        self._cache_path = _DEFAULT_NEWS_CACHE
        self._days_back = _DEFAULT_DAYS_BACK
        self._max_headlines = _DEFAULT_MAX_HEADLINES

        if config is not None:
            self._cache_path = getattr(config, "news_cache_path", _DEFAULT_NEWS_CACHE)
            self._days_back = getattr(config, "news_days_back", _DEFAULT_DAYS_BACK)
            self._max_headlines = getattr(
                config, "news_max_headlines_per_symbol", _DEFAULT_MAX_HEADLINES
            )

        self._providers: List[BaseNewsProvider] = []
        self._build_provider_chain()

    def _build_provider_chain(self) -> None:
        """Initialize providers in priority order."""
        try:
            alpaca = AlpacaNewsProvider()
            if alpaca.is_available:
                self._providers.append(alpaca)
                logger.info("NewsDiscovery: AlpacaNewsProvider available")
            else:
                logger.info("NewsDiscovery: AlpacaNewsProvider unavailable (no API keys or import error)")
        except Exception as e:
            logger.debug(f"NewsDiscovery: AlpacaNewsProvider init error: {e}")

        self._providers.append(YahooFinanceNewsProvider())
        self._providers.append(ManualCSVNewsProvider(self._cache_path))

    def get_recent_news_for_symbol(
        self,
        symbol: str,
        days_back: int = None,
    ) -> List[dict]:
        """
        Get recent news for one symbol, trying providers in order.

        Returns the first non-empty result. Applies all filters before returning.
        """
        days = days_back or self._days_back

        for provider in self._providers:
            try:
                items = provider.get_news_for_symbol(symbol, days, self._max_headlines)
                if items:
                    items = filter_stale_news(items, days)
                    items = filter_low_quality_news(items)
                    items = deduplicate_headlines(items)
                    return items
            except Exception as e:
                logger.debug(f"NewsDiscovery: {provider.name} failed for {symbol}: {e}")

        return []

    def get_news_for_discovered_universe(
        self,
        symbols: List[str],
        days_back: int = None,
    ) -> Tuple[List[dict], List[str]]:
        """
        Pull news for all symbols in the discovered universe.

        Strategy:
          1. Try Alpaca batch endpoint first (all symbols at once)
          2. For any symbol still without news, try Yahoo individually
          3. Flag symbols with no news from any provider

        Args:
            symbols:   List of ticker symbols
            days_back: Days of news history to pull (default from config)

        Returns:
            (all_news_items, no_news_symbols)
            no_news_symbols: tickers that got no usable news from any provider
        """
        days = days_back or self._days_back
        all_news: List[dict] = []
        covered: Dict[str, int] = {s.upper(): 0 for s in symbols}

        for provider in self._providers:
            missing = [s for s in symbols if covered.get(s.upper(), 0) == 0]
            if not missing:
                break

            try:
                items = provider.get_news_for_symbols(missing, days, self._max_headlines)

                for item in items:
                    t = str(item.get("ticker", "") or "").upper()
                    if t:
                        covered[t] = covered.get(t, 0) + 1

                all_news.extend(items)
                covered_count = sum(1 for c in covered.values() if c > 0)
                logger.info(
                    f"NewsDiscovery: {provider.name} → {len(items)} items, "
                    f"{covered_count}/{len(symbols)} symbols covered"
                )

            except Exception as e:
                logger.warning(f"NewsDiscovery: provider {provider.name} failed: {e}")

        no_news_symbols = [s for s in symbols if covered.get(s.upper(), 0) == 0]

        if no_news_symbols:
            logger.info(
                f"NewsDiscovery: {len(no_news_symbols)} symbols with no news — "
                f"flagging for manual review"
            )

        # Apply all filters to the combined result
        all_news = filter_stale_news(all_news, days)
        all_news = filter_low_quality_news(all_news)
        all_news = deduplicate_headlines(all_news)

        return all_news, no_news_symbols

    def cache_news_to_csv(
        self,
        news_items: List[dict],
        append: bool = False,
    ) -> int:
        """Save news to the cache CSV. Returns number of rows written."""
        return cache_news_to_csv(news_items, self._cache_path, append)
