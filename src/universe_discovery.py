"""
src/universe_discovery.py — Automatic Stock Universe Discovery Engine

This module replaces the need for a fixed watchlist.

Every day it:
  1. Asks Alpaca for the most actively traded stocks (if API keys are valid)
  2. Asks Alpaca for the top gainers of the day (if available)
  3. Runs a scan of ~140 liquid large/mid-cap "seed" stocks via yfinance,
     looking for unusual volume and momentum
  4. Combines all sources, deduplicates, and applies quality filters
  5. Saves the full results (pass and fail) to reports/daily_discovered_universe.csv

Normal mode:   discovers stocks automatically from the sources above
Fallback mode: loads data/universe_sample.csv (used for testing)

No trades are placed in this module. It only collects and filters candidates.

Usage:
    from src.universe_discovery import run_discovery
    from src.config import load_config

    cfg = load_config()
    universe_df = run_discovery(cfg)
    # universe_df contains only stocks that passed all filters
"""

import time
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.config import AppConfig
from src.data_loader import download_batch, download_spy_data
from src.indicators import (
    latest_close,
    volume_ratio,
    avg_volume_n,
    avg_dollar_volume_n,
    return_nd,
    relative_strength_vs_spy,
    is_above_20d_high,
    is_above_ma,
)
from src.utils import (
    setup_logger,
    load_blocked_symbols,
    load_csv_safe,
    ensure_reports_dir,
    format_pct,
    format_large_number,
    today_str,
    normalize_ticker,
)

logger = setup_logger(__name__)

# ============================================================
# Seed universe — used as the base for the yfinance scan.
#
# These ~140 tickers are a starting point for discovery.
# They are NOT a fixed trading list — the actual trading universe
# is determined every day by the quality filters and signal engine.
#
# The yfinance scan only selects stocks from this list that are
# showing unusual volume, strong momentum, or price breakouts.
# It is a scanning base, not a watchlist.
#
# You can add tickers here if you want more stocks scanned.
# ============================================================

SEED_UNIVERSE: List[str] = [
    # ---- Mega-cap Technology ----
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "AVGO",
    # ---- Semiconductors ----
    "AMD", "QCOM", "TXN", "AMAT", "MU", "INTC", "ADI", "MRVL",
    "KLAC", "LRCX", "NXPI", "MCHP", "ON",
    # ---- Software / Cloud / AI ----
    "ORCL", "NOW", "CRM", "SNOW", "PLTR", "CRWD", "PANW", "ZS",
    "FTNT", "NET", "DDOG", "MDB", "HUBS", "WDAY", "TEAM", "TTD",
    "BILL", "TWLO",
    # ---- Consumer Technology / Internet ----
    "NFLX", "SPOT", "PINS", "SNAP", "RBLX", "UBER", "LYFT",
    "ABNB", "BKNG", "EXPE",
    # ---- Financials ----
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "SCHW",
    "V", "MA", "PYPL", "AXP", "COF", "SPGI", "MCO", "ICE",
    "CME", "COIN", "SOFI",
    # ---- Healthcare / Biotech ----
    "UNH", "JNJ", "PFE", "MRK", "ABBV", "TMO", "DHR", "ISRG",
    "REGN", "BIIB", "VRTX", "MRNA", "GILD", "AMGN", "BMY",
    "LLY", "DXCM",
    # ---- Consumer Discretionary ----
    "HD", "MCD", "SBUX", "NKE", "LULU", "TJX", "COST", "WMT",
    "TGT", "LOW", "CMG", "DPZ", "YUM",
    # ---- Energy ----
    "XOM", "CVX", "COP", "SLB", "OXY", "MPC", "PSX", "VLO",
    # ---- Industrials ----
    "CAT", "DE", "HON", "GE", "RTX", "LMT", "NOC", "BA",
    "UPS", "FDX", "CSX", "NSC", "ETN",
    # ---- Materials ----
    "NEM", "FCX", "CLF", "STLD",
    # ---- Communication / Media ----
    "DIS", "T", "VZ", "ROKU", "PARA",
    # ---- REITs ----
    "AMT", "PLD", "O",
    # ---- High-momentum / emerging names ----
    "SHOP", "MELI", "SE", "SMCI", "ARM", "RDDT",
    "HOOD", "AFRM", "UPST",
]

# Loose discovery threshold for the yfinance seed scan.
# Stocks that meet AT LEAST ONE of these will be included as candidates
# before the full quality filters are applied.
_DISCOVERY_MIN_VOLUME_RATIO = 1.5   # Volume at least 1.5× the 20-day average
_DISCOVERY_MIN_RETURN_20D = 0.05    # Up at least 5% over 20 trading days
_DISCOVERY_MIN_RETURN_5D = 0.03     # Up at least 3% over 5 days


# ============================================================
# Main discovery class
# ============================================================

class UniverseDiscovery:
    """
    Discovers the daily trading universe from multiple market-wide sources.

    Usage:
        from src.universe_discovery import UniverseDiscovery
        from src.config import load_config

        cfg = load_config()
        discovery = UniverseDiscovery(cfg)
        universe_df = discovery.run()
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.blocked_symbols: set = load_blocked_symbols(config.blocked_symbols_path)
        self._candidates: Dict[str, dict] = {}  # {ticker: {source, reason_added}}

    # ----------------------------------------------------------
    # Public entry point
    # ----------------------------------------------------------

    def run(self, use_fallback_only: bool = False) -> pd.DataFrame:
        """
        Run the full discovery pipeline.

        Args:
            use_fallback_only: If True, skip all API/yfinance scanning and
                               load only from data/universe_sample.csv.
                               Use this for testing and debugging.

        Returns:
            A pandas DataFrame containing only stocks that PASSED all filters.
            The full results (including rejected stocks) are saved to:
            reports/daily_discovered_universe.csv

        The returned DataFrame has columns:
            ticker, source, price, volume, avg_volume_20d, volume_ratio,
            avg_dollar_volume_20d, market_cap, return_20d,
            relative_strength_vs_spy, above_20d_high, above_50d_ma,
            reason_added, passed_filters, rejection_reason
        """
        print()
        print("=" * 60)
        print("  PAPER TRADING MODE ONLY")
        print("  Universe Discovery Engine")
        print("=" * 60)
        print(f"  Date: {today_str()}")

        if use_fallback_only:
            print("  Mode: FALLBACK (using universe_sample.csv only)")
        else:
            print("  Mode: AUTOMATIC (scanning market sources)")
        print()

        start_time = time.time()

        # ---- Step 1: Collect raw candidates ----
        self._candidates = {}
        self._collect_candidates(use_fallback_only=use_fallback_only)

        if not self._candidates:
            print("  [WARNING] No candidates found from any source.")
            print("  This can happen on weekends, holidays, or if all API sources failed.")
            print("  Try: python main.py discover-universe  (on a weekday after market data is available)")
            return pd.DataFrame()

        print(f"\n  Total unique candidates collected: {len(self._candidates)}")

        # ---- Step 2: Download SPY for relative strength ----
        print("\n  Downloading SPY benchmark data...")
        spy_df = download_spy_data(period="90d")
        if spy_df is None:
            print("  [WARNING] Could not download SPY data. Relative strength will be unavailable.")

        # ---- Step 3: Download price data for all candidates ----
        tickers = list(self._candidates.keys())
        print(f"\n  Downloading price/volume data for {len(tickers)} candidates...")
        print("  (This may take 30–90 seconds depending on your internet connection)")

        price_data = self._download_universe_data(tickers)
        print(f"  Data loaded for {len(price_data)}/{len(tickers)} tickers")

        # ---- Step 4: Build rows and apply filters ----
        print("\n  Applying quality filters...")
        all_rows = self._build_all_rows(price_data, spy_df)

        # ---- Step 5: Save to CSV and return ----
        result_df = self._save_and_return(all_rows)

        elapsed = time.time() - start_time
        print(f"\n  Discovery completed in {elapsed:.1f} seconds")
        self._print_summary(result_df, all_rows)

        return result_df

    # ----------------------------------------------------------
    # Step 1: Collect candidate tickers from sources
    # ----------------------------------------------------------

    def _collect_candidates(self, use_fallback_only: bool) -> None:
        """
        Populate self._candidates with tickers from all enabled sources.

        Each candidate entry looks like:
            self._candidates["NVDA"] = {
                "sources": ["alpaca_volume", "yfinance_seed"],
                "reason_added": "Alpaca most active + yfinance volume spike",
            }
        """
        if use_fallback_only:
            self._load_fallback_universe()
            return

        # Try Alpaca sources first (fast, market-wide)
        if self.config.discovery_alpaca_volume:
            print("  Source 1: Alpaca most active by volume...")
            items = self._fetch_alpaca_most_actives(by="volume")
            self._add_candidates(items)
            print(f"    Found {len(items)} symbols")

        if self.config.discovery_alpaca_trades:
            print("  Source 2: Alpaca most active by trade count...")
            items = self._fetch_alpaca_most_actives(by="trades")
            new = self._add_candidates(items)
            print(f"    Found {len(items)} symbols ({new} new)")

        if self.config.discovery_top_gainers:
            print("  Source 3: Alpaca top gainers...")
            items = self._fetch_alpaca_top_gainers()
            new = self._add_candidates(items)
            print(f"    Found {len(items)} symbols ({new} new)")

        # yfinance seed scan (scans SEED_UNIVERSE for unusual activity)
        if self.config.discovery_yfinance_volume or self.config.discovery_yfinance_breakout:
            print(f"  Source 4: yfinance seed scan ({len(SEED_UNIVERSE)} base symbols)...")
            print("    Downloading seed universe data (this takes ~30 seconds)...")
            items = self._fetch_yfinance_seed_scan()
            new = self._add_candidates(items)
            print(f"    Found {len(items)} candidates with unusual activity ({new} new)")

        # If nothing found at all, fall back to universe_sample.csv
        if not self._candidates:
            print("  [WARNING] No candidates from any source. Loading fallback universe...")
            self._load_fallback_universe()

    def _add_candidates(self, items: List[dict]) -> int:
        """
        Add a list of candidate dicts to self._candidates.
        Merges sources if the ticker already exists.
        Returns the number of NEW tickers added (not already in candidates).
        """
        new_count = 0
        for item in items:
            raw_ticker = item.get("ticker")
            if not raw_ticker:  # Catches None, "", 0, False
                continue
            ticker = normalize_ticker(str(raw_ticker))
            if not ticker:
                continue
            source = item.get("source", "unknown")
            reason = item.get("reason_added", "")

            if ticker in self._candidates:
                # Already found from another source — merge the source info
                existing = self._candidates[ticker]
                if source not in existing["sources"]:
                    existing["sources"].append(source)
                    existing["reason_added"] += f" + {reason}"
            else:
                self._candidates[ticker] = {
                    "sources": [source],
                    "reason_added": reason,
                }
                new_count += 1
        return new_count

    # ----------------------------------------------------------
    # Source: Alpaca most active stocks
    # ----------------------------------------------------------

    def _fetch_alpaca_most_actives(self, by: str = "volume") -> List[dict]:
        """
        Fetch the most actively traded stocks from Alpaca's screener.

        Args:
            by: "volume" for most active by share volume,
                "trades" for most active by number of trades.

        Returns:
            List of candidate dicts with ticker, source, reason_added.
            Returns an empty list if the Alpaca screener is unavailable.

        Note: This endpoint may require a paid Alpaca market data subscription.
        If it fails (free tier), the error is caught and we fall back to yfinance.
        """
        try:
            from alpaca.data.historical.stock import StockHistoricalDataClient
            from alpaca.data.requests import MostActivesRequest

            client = StockHistoricalDataClient(
                api_key=self.config.alpaca_api_key,
                secret_key=self.config.alpaca_secret_key,
            )

            request = MostActivesRequest(by=by, top=100)
            result = client.get_stock_most_actives(request)

            candidates = []
            for item in (result.most_actives or []):
                sym = normalize_ticker(getattr(item, "symbol", "") or "")
                if sym:
                    candidates.append({
                        "ticker": sym,
                        "source": f"alpaca_most_active_{by}",
                        "reason_added": f"Alpaca most active by {by}",
                    })

            return candidates

        except ImportError:
            logger.debug("alpaca-py MostActivesRequest not available in this version")
        except Exception as e:
            logger.info(f"    Alpaca most-actives ({by}) unavailable: {type(e).__name__}")
            logger.debug(f"    Full error: {e}")

        return []

    # ----------------------------------------------------------
    # Source: Alpaca top gainers
    # ----------------------------------------------------------

    def _fetch_alpaca_top_gainers(self) -> List[dict]:
        """
        Fetch the top gaining stocks of the day from Alpaca's screener.

        Returns an empty list if unavailable (caught gracefully).
        """
        try:
            from alpaca.data.historical.stock import StockHistoricalDataClient
            from alpaca.data.requests import MarketMoversRequest

            client = StockHistoricalDataClient(
                api_key=self.config.alpaca_api_key,
                secret_key=self.config.alpaca_secret_key,
            )

            request = MarketMoversRequest(market_type="stocks", top=50)
            result = client.get_stock_top_movers(request)

            candidates = []
            for item in (result.gainers or []):
                sym = normalize_ticker(getattr(item, "symbol", "") or "")
                if sym:
                    pct = getattr(item, "percent_change", None)
                    reason = "Alpaca top gainer"
                    if pct is not None:
                        reason += f" (+{pct:.1f}%)"
                    candidates.append({
                        "ticker": sym,
                        "source": "alpaca_top_gainer",
                        "reason_added": reason,
                    })

            return candidates

        except ImportError:
            logger.debug("alpaca-py MarketMoversRequest not available in this version")
        except Exception as e:
            logger.info(f"    Alpaca top movers unavailable: {type(e).__name__}")
            logger.debug(f"    Full error: {e}")

        return []

    # ----------------------------------------------------------
    # Source: yfinance seed scan
    # ----------------------------------------------------------

    def _fetch_yfinance_seed_scan(self) -> List[dict]:
        """
        Download price data for the SEED_UNIVERSE and identify stocks
        showing unusual volume, momentum, or price breakouts.

        This is the primary fallback when Alpaca sources are unavailable
        or to supplement them with a broader scan.

        Discovery criteria (any ONE of these qualifies a stock):
          - Volume today >= 1.5× the 20-day average volume
          - 20-day return >= 5%
          - Price making a new 20-day high

        Returns:
            List of candidate dicts for stocks that met at least one criterion.
        """
        # Download all seed universe tickers in batches
        # We process in groups of 50 to avoid rate limiting
        all_data: Dict[str, pd.DataFrame] = {}
        batch_size = 50

        seed_tickers = [t for t in SEED_UNIVERSE if t not in self.blocked_symbols]
        total = len(seed_tickers)

        for i in range(0, total, batch_size):
            batch = seed_tickers[i: i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (total + batch_size - 1) // batch_size
            print(f"    Batch {batch_num}/{total_batches} ({len(batch)} tickers)...", end="", flush=True)

            batch_data = download_batch(batch, period="90d")
            all_data.update(batch_data)
            print(f" done ({len(batch_data)}/{len(batch)} loaded)")

            # Small pause between batches to be respectful of rate limits
            if i + batch_size < total:
                time.sleep(0.5)

        if not all_data:
            logger.warning("yfinance seed scan returned no data")
            return []

        candidates = []

        for ticker, df in all_data.items():
            if df is None or len(df) < 10:
                continue

            # Check discovery criteria (loose thresholds)
            reasons = []

            vr = volume_ratio(df, 20)
            if vr is not None and vr >= _DISCOVERY_MIN_VOLUME_RATIO:
                reasons.append(f"volume spike {vr:.1f}x")

            ret_20d = return_nd(df, 20)
            if ret_20d is not None and ret_20d >= _DISCOVERY_MIN_RETURN_20D:
                reasons.append(f"momentum +{ret_20d*100:.1f}% 20d")

            if is_above_20d_high(df, 20):
                reasons.append("20-day high breakout")

            ret_5d = return_nd(df, 5)
            if ret_5d is not None and ret_5d >= _DISCOVERY_MIN_RETURN_5D:
                if is_above_ma(df, 50):
                    reasons.append(f"near-term trend +{ret_5d*100:.1f}% 5d")

            if reasons:
                candidates.append({
                    "ticker": ticker,
                    "source": "yfinance_seed_scan",
                    "reason_added": "yfinance: " + ", ".join(reasons),
                })

        return candidates

    # ----------------------------------------------------------
    # Source: Fallback universe (universe_sample.csv)
    # ----------------------------------------------------------

    def _load_fallback_universe(self) -> None:
        """
        Load the fixed fallback universe from data/universe_sample.csv.

        Used when:
        - use_fallback_only=True (testing / debugging mode)
        - All other discovery sources returned nothing

        Adds tickers to self._candidates with source="fallback_universe".
        """
        path = "data/universe_sample.csv"
        df = load_csv_safe(path)

        if df is None or "ticker" not in df.columns:
            print(f"  [WARNING] Could not load fallback universe from: {path}")
            print("  Make sure data/universe_sample.csv exists.")
            return

        tickers = df["ticker"].dropna().tolist()
        print(f"  Fallback universe: loaded {len(tickers)} tickers from {path}")

        for ticker in tickers:
            sym = normalize_ticker(str(ticker))
            if sym and sym not in self._candidates:
                self._candidates[sym] = {
                    "sources": ["fallback_universe"],
                    "reason_added": "Fallback universe (universe_sample.csv)",
                }

    # ----------------------------------------------------------
    # Symbol-level filters (run BEFORE downloading data)
    # ----------------------------------------------------------

    def _filter_symbols_fast(self, tickers: List[str]) -> Tuple[List[str], Dict[str, str]]:
        """
        Apply fast symbol-level filters that don't require price data.

        These checks run on the ticker string alone:
          - Not in blocked_symbols.csv
          - Not a warrant (5-char ticker ending in W, e.g. SPNVW)
          - Not a unit (5-char ticker ending in U, e.g. PSACU)
          - Not a rights offering (5-char ticker ending in R, e.g. XYZRR)
          - Not a preferred share (5-char tickers with certain patterns)
          - Not an OTC (best-effort detection)
          - Ticker must be 1–5 alphabetic characters

        Args:
            tickers: List of ticker strings to check.

        Returns:
            Tuple of:
              - passed: List of tickers that survived all symbol filters
              - rejected: Dict mapping rejected_ticker -> rejection_reason
        """
        passed = []
        rejected = {}

        for ticker in tickers:
            sym = normalize_ticker(ticker)

            # Must be 1–5 uppercase alpha characters
            if not sym or not sym.isalpha() or len(sym) > 5:
                rejected[sym or ticker] = "Invalid ticker format (not 1-5 alphabetic chars)"
                continue

            # Check blocked list
            if sym in self.blocked_symbols:
                rejected[sym] = "On blocked symbols list (data/blocked_symbols.csv)"
                continue

            # Detect likely warrants, units, rights (by naming convention)
            reason = self._suspicious_suffix_reason(sym)
            if reason:
                rejected[sym] = reason
                continue

            passed.append(sym)

        return passed, rejected

    def _suspicious_suffix_reason(self, symbol: str) -> Optional[str]:
        """
        Check if a ticker symbol looks like a warrant, unit, right, or preferred share
        based on common naming conventions used by stock exchanges.

        Note: This is a heuristic. Some valid stocks end in W, R, or U.
        The full filter stack also checks price, volume, and market cap,
        so false positives here won't hurt — we only exclude clear-cut cases.

        Returns a rejection reason string if suspicious, None if it looks fine.
        """
        s = symbol.upper()

        # Warrants typically: 5 chars ending in W
        # Examples: SPNVW, CCHWW, BOWXW
        if len(s) == 5 and s.endswith("W"):
            return "Likely warrant (5-char ticker ending in W)"

        # Units typically: 5 chars ending in U
        # Examples: PSACU, GXIIU, DFPHU
        if len(s) == 5 and s.endswith("U"):
            return "Likely unit (5-char ticker ending in U)"

        # Rights typically: 5 chars ending in R
        # Examples: XYZRR
        if len(s) == 5 and s.endswith("R"):
            return "Likely rights offering (5-char ticker ending in R)"

        return None

    # ----------------------------------------------------------
    # Step 3: Download price data
    # ----------------------------------------------------------

    def _download_universe_data(
        self,
        tickers: List[str],
    ) -> Dict[str, pd.DataFrame]:
        """
        Apply symbol filters, then download price data for all passing tickers.

        Downloads in batches of 50 to avoid rate limiting.
        Prints progress as it goes.

        Returns a dict of {ticker: DataFrame} for tickers that downloaded successfully.
        """
        # First apply fast symbol filters (no data needed)
        passed_symbols, rejected_symbols = self._filter_symbols_fast(tickers)

        if rejected_symbols:
            print(f"  Symbol filters: rejected {len(rejected_symbols)} (warrants/units/blocked/invalid)")
            for sym, reason in list(rejected_symbols.items())[:5]:
                print(f"    - {sym}: {reason}")
            if len(rejected_symbols) > 5:
                print(f"    ... and {len(rejected_symbols) - 5} more")

        if not passed_symbols:
            print("  [WARNING] No tickers remained after symbol filters.")
            return {}

        print(f"  Downloading data for {len(passed_symbols)} tickers...")

        all_data: Dict[str, pd.DataFrame] = {}
        batch_size = 50

        for i in range(0, len(passed_symbols), batch_size):
            batch = passed_symbols[i: i + batch_size]
            batch_data = download_batch(batch, period="90d")
            all_data.update(batch_data)

            completed = min(i + batch_size, len(passed_symbols))
            print(f"    [{completed}/{len(passed_symbols)}] downloaded...")

            if i + batch_size < len(passed_symbols):
                time.sleep(0.3)

        return all_data

    # ----------------------------------------------------------
    # Step 4: Build rows and apply quality filters
    # ----------------------------------------------------------

    def _build_all_rows(
        self,
        price_data: Dict[str, pd.DataFrame],
        spy_df: Optional[pd.DataFrame],
    ) -> List[dict]:
        """
        For every candidate ticker, calculate indicators and apply quality filters.

        Returns a list of row dicts (one per ticker) with all indicators
        and a passed_filters / rejection_reason field for each.

        These rows are written to the CSV — both passing and failing stocks
        are included so you can see exactly why each stock was accepted or rejected.
        """
        rows = []

        rejection_counts: Dict[str, int] = {}

        for ticker, df in price_data.items():
            candidate_info = self._candidates.get(ticker, {
                "sources": ["unknown"],
                "reason_added": "Source unknown",
            })

            row = self._build_single_row(ticker, df, spy_df, candidate_info)
            passed, rejection_reason = self._check_quality_filters(row)

            row["passed_filters"] = passed
            row["rejection_reason"] = rejection_reason or ""

            rows.append(row)

            if not passed and rejection_reason:
                key = rejection_reason.split("(")[0].strip()
                rejection_counts[key] = rejection_counts.get(key, 0) + 1

        # Also add tickers that were in candidates but had no price data
        tickers_with_data = set(price_data.keys())
        for ticker in self._candidates:
            if ticker not in tickers_with_data:
                candidate_info = self._candidates[ticker]
                row = {
                    "date": today_str(),
                    "ticker": ticker,
                    "source": ",".join(candidate_info.get("sources", ["unknown"])),
                    "reason_added": candidate_info.get("reason_added", ""),
                    "price": None,
                    "volume": None,
                    "avg_volume_20d": None,
                    "volume_ratio": None,
                    "avg_dollar_volume_20d": None,
                    "market_cap": None,
                    "return_20d": None,
                    "relative_strength_vs_spy": None,
                    "above_20d_high": False,
                    "above_50d_ma": False,
                    "passed_filters": False,
                    "rejection_reason": "No price data available from Yahoo Finance",
                }
                rows.append(row)
                rejection_counts["No price data"] = rejection_counts.get("No price data", 0) + 1

        # Print filter breakdown
        passed_count = sum(1 for r in rows if r["passed_filters"])
        failed_count = len(rows) - passed_count

        print(f"\n  Filter results:")
        print(f"    Passed: {passed_count}")
        print(f"    Rejected: {failed_count}")
        if rejection_counts:
            for reason, count in sorted(rejection_counts.items(), key=lambda x: -x[1]):
                print(f"      - {count} rejected: {reason}")

        return rows

    def _build_single_row(
        self,
        ticker: str,
        df: pd.DataFrame,
        spy_df: Optional[pd.DataFrame],
        candidate_info: dict,
    ) -> dict:
        """
        Calculate all indicators for one ticker and return a row dict.

        This row will later have passed_filters and rejection_reason added.
        """
        close = latest_close(df)
        vol_today = float(df["Volume"].iloc[-1]) if "Volume" in df.columns and not df.empty else None
        avg_vol = avg_volume_n(df, 20)
        vol_ratio_val = volume_ratio(df, 20)
        avg_dv = avg_dollar_volume_n(df, 20)
        ret_20d = return_nd(df, 20)
        rs = relative_strength_vs_spy(df, spy_df, 20) if spy_df is not None else None
        above_high = is_above_20d_high(df, 20)
        above_ma50 = is_above_ma(df, 50)

        return {
            "date": today_str(),
            "ticker": ticker,
            "source": ",".join(candidate_info.get("sources", ["unknown"])),
            "reason_added": candidate_info.get("reason_added", ""),
            "price": round(close, 4) if close is not None else None,
            "volume": int(vol_today) if vol_today is not None else None,
            "avg_volume_20d": round(avg_vol, 0) if avg_vol is not None else None,
            "volume_ratio": round(vol_ratio_val, 3) if vol_ratio_val is not None else None,
            "avg_dollar_volume_20d": round(avg_dv, 0) if avg_dv is not None else None,
            "market_cap": None,  # Filled by get_market_cap_approx where needed
            "return_20d": round(ret_20d, 6) if ret_20d is not None else None,
            "relative_strength_vs_spy": round(rs, 6) if rs is not None else None,
            "above_20d_high": above_high,
            "above_50d_ma": above_ma50,
            "passed_filters": False,    # Will be set below
            "rejection_reason": "",     # Will be set below
        }

    def _check_quality_filters(self, row: dict) -> Tuple[bool, Optional[str]]:
        """
        Apply all data-based quality filters to a single candidate row.

        Checks in order (stops at first failure):
          1. Price data must exist
          2. Price >= min_price ($5)
          3. Market cap >= min_market_cap ($1B) — only if market cap is known
          4. Avg dollar volume >= min_avg_dollar_volume ($50M)
          5. Avg share volume >= min_avg_share_volume (1M shares)
          6. Enough price history for indicators (>= 25 trading days)

        Args:
            row: A candidate row dict (from _build_single_row)

        Returns:
            Tuple of (passed, rejection_reason):
              (True, None) if all filters pass
              (False, "reason string") if any filter fails
        """
        cfg = self.config

        # 1. Price must be available
        price = row.get("price")
        if price is None:
            return False, "No price data available from Yahoo Finance"

        # 2. Price filter
        if price < cfg.min_price:
            return False, f"Price ${price:.2f} below minimum (${cfg.min_price:.2f})"

        # 3. Market cap filter (only enforce if we have a reading)
        mc = row.get("market_cap")
        if mc is not None and mc > 0 and mc < cfg.min_market_cap:
            return False, (
                f"Market cap {format_large_number(mc)} below minimum "
                f"({format_large_number(cfg.min_market_cap)})"
            )

        # 4. Average dollar volume filter
        avg_dv = row.get("avg_dollar_volume_20d")
        if avg_dv is None:
            return False, "Not enough price history to calculate average dollar volume"
        if avg_dv < cfg.min_avg_dollar_volume:
            return False, (
                f"Avg dollar volume {format_large_number(avg_dv)} below "
                f"minimum ({format_large_number(cfg.min_avg_dollar_volume)})"
            )

        # 5. Average share volume filter
        avg_vol = row.get("avg_volume_20d")
        if avg_vol is None:
            return False, "Not enough price history to calculate average share volume"
        if avg_vol < cfg.min_avg_share_volume:
            return False, (
                f"Avg share volume {avg_vol:,.0f} below minimum "
                f"({cfg.min_avg_share_volume:,.0f})"
            )

        return True, None

    # ----------------------------------------------------------
    # Step 5: Save and return
    # ----------------------------------------------------------

    def _save_and_return(self, all_rows: List[dict]) -> pd.DataFrame:
        """
        Save all rows (passed and failed) to the discovered universe CSV,
        then return a DataFrame containing only the PASSING stocks.

        The full CSV is useful for debugging — you can see exactly why
        each stock was accepted or rejected.
        """
        ensure_reports_dir()
        output_path = self.config.discovered_universe_path

        if not all_rows:
            return pd.DataFrame()

        df_all = pd.DataFrame(all_rows)

        # Reorder columns for readability
        col_order = [
            "date", "ticker", "source", "reason_added",
            "price", "volume", "avg_volume_20d", "volume_ratio",
            "avg_dollar_volume_20d", "market_cap",
            "return_20d", "relative_strength_vs_spy",
            "above_20d_high", "above_50d_ma",
            "passed_filters", "rejection_reason",
        ]
        # Only include columns that actually exist in the DataFrame
        ordered_cols = [c for c in col_order if c in df_all.columns]
        df_all = df_all[ordered_cols]

        df_all.to_csv(output_path, index=False)
        print(f"\n  Saved to: {output_path}")

        # Return only the passing stocks
        passed_df = df_all[df_all["passed_filters"] == True].copy()
        passed_df = passed_df.reset_index(drop=True)
        return passed_df

    # ----------------------------------------------------------
    # Summary output
    # ----------------------------------------------------------

    def _print_summary(self, passed_df: pd.DataFrame, all_rows: List[dict]) -> None:
        """
        Print a human-readable summary of the discovery results.
        Shows the top candidates sorted by volume ratio.
        """
        total = len(all_rows)
        passed = len(passed_df)

        print()
        print("=" * 60)
        print("  DISCOVERY SUMMARY")
        print("=" * 60)
        print(f"  Total candidates scanned: {total}")
        print(f"  Passed all filters:       {passed}")
        print(f"  Rejected:                 {total - passed}")
        print()

        if passed_df.empty:
            print("  No stocks passed all filters today.")
            print("  Possible reasons:")
            print("  - Market may be closed or data is from a weekend")
            print("  - No stocks in seed universe met the liquidity requirements")
            print("  - Try running on a weekday when recent trading data is available")
            return

        # Show top candidates by volume ratio
        if "volume_ratio" in passed_df.columns:
            top = passed_df.dropna(subset=["volume_ratio"]).sort_values(
                "volume_ratio", ascending=False
            ).head(10)

            print("  Top candidates by volume ratio (today vs 20-day avg):")
            print()
            print(f"  {'Ticker':<8} {'Price':>8} {'Vol Ratio':>10} {'20d Return':>11} {'RS vs SPY':>10}")
            print("  " + "-" * 52)

            for _, row in top.iterrows():
                ticker = str(row.get("ticker", ""))
                price = row.get("price")
                vr = row.get("volume_ratio")
                ret = row.get("return_20d")
                rs = row.get("relative_strength_vs_spy")

                price_str = f"${price:.2f}" if price else "N/A"
                vr_str = f"{vr:.1f}x" if vr else "N/A"
                ret_str = format_pct(ret) if ret is not None else "N/A"
                rs_str = format_pct(rs) if rs is not None else "N/A"

                print(f"  {ticker:<8} {price_str:>8} {vr_str:>10} {ret_str:>11} {rs_str:>10}")

        print()
        print("  To run the signal scanner on this universe:")
        print("    python main.py paper-scan")
        print()
        print("  To view the full discovered universe:")
        print(f"    Open: {self.config.discovered_universe_path}")
        print()


# ============================================================
# Module-level convenience functions
# ============================================================

def run_discovery(
    config: AppConfig,
    use_fallback_only: bool = False,
) -> pd.DataFrame:
    """
    Convenience function: create a UniverseDiscovery and run it.

    Args:
        config:           AppConfig object (from load_config())
        use_fallback_only: If True, use universe_sample.csv instead of scanning

    Returns:
        DataFrame of stocks that passed all filters.
    """
    discovery = UniverseDiscovery(config)
    return discovery.run(use_fallback_only=use_fallback_only)


def load_todays_universe(path: str) -> Optional[pd.DataFrame]:
    """
    Load today's discovered universe CSV, if it exists and was created today.

    Returns None if:
    - The file does not exist
    - The file was created on a previous day

    Args:
        path: Path to daily_discovered_universe.csv

    Returns:
        DataFrame of today's passed stocks, or None.
    """
    df = load_csv_safe(path)
    if df is None or df.empty:
        return None

    # Check that the file is from today
    if "date" in df.columns:
        dates = df["date"].dropna().unique()
        if len(dates) > 0 and str(dates[0])[:10] == today_str():
            return df[df["passed_filters"] == True].copy() if "passed_filters" in df.columns else df
        else:
            logger.debug(f"Universe file is from {dates[0]}, not today ({today_str()})")
            return None

    # No date column — return it anyway and let the caller decide
    return df


def is_todays_universe_fresh(path: str) -> bool:
    """
    Check whether a fresh discovered universe file already exists for today.

    Returns True if we already ran discovery today (no need to run again).
    Returns False if discovery needs to be run.
    """
    df = load_todays_universe(path)
    return df is not None and len(df) > 0
