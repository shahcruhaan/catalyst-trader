"""
============================================================
AI Catalyst Momentum Breakout Paper Trader
main.py — Command-line entry point

PAPER TRADING ONLY. This system will never place live trades.

Usage:
    python main.py setup-check
    python main.py explain
    python main.py discover-universe
    python main.py scan
    python main.py paper-scan
    python main.py paper-execute
    python main.py paper-reconcile
    python main.py paper-report
    python main.py run-once-full-cycle
    python main.py run-bot
    python main.py emergency-close-all
    python main.py backtest --start 2020-01-01 --end 2024-12-31
============================================================
"""

import sys
import os


# ============================================================
# Utility: print a formatted banner
# ============================================================

def print_banner():
    print()
    print("=" * 60)
    print("  AI Catalyst Momentum Breakout Paper Trader")
    print("  PAPER TRADING MODE ONLY")
    print("=" * 60)
    print()


def print_coming_soon(command: str):
    """Shown for commands that will be built in a future stage."""
    print_banner()
    print(f"  Command: python main.py {command}")
    print()
    print("  This command will be available in a future stage.")
    print("  Currently available commands:")
    print("    python main.py setup-check         (Stage 2 — ready)")
    print("    python main.py explain             (Stage 2 — ready)")
    print("    python main.py discover-universe   (Stage 3 — ready)")
    print("    python main.py paper-scan          (Stage 4 — ready)")
    print()
    print("  Coming next:")
    print("    Stage 6: paper-report")
    print("    Stage 7: run-bot, run-once-full-cycle")
    print("    Stage 8: backtest")
    print()


# ============================================================
# Command: setup-check
# ============================================================

def cmd_setup_check():
    """
    Verifies that your environment is correctly configured.

    Checks:
      1. Python version is 3.8 or higher
      2. All required packages are installed
      3. config.yaml exists and is readable
      4. .env file exists
      5. ALPACA_PAPER=true is set
      6. API keys are present and not placeholder values
      7. Can connect to Alpaca Paper Trading
      8. Account status is ACTIVE
      9. Reports folder exists (creates it if needed)
    """
    print_banner()
    print("  SETUP CHECK")
    print("=" * 60)
    print()

    all_passed = True
    results = []

    # ---- 1. Python version ----
    major = sys.version_info.major
    minor = sys.version_info.minor
    py_version = f"Python {major}.{minor}.{sys.version_info.micro}"
    py_ok = (major >= 3 and minor >= 8)
    results.append(("Python version", py_ok, py_version))
    if not py_ok:
        results.append(("", False, "  Need Python 3.8+. Download from https://python.org"))

    # ---- 2. Required packages ----
    required_packages = [
        ("pandas",      "pandas"),
        ("numpy",       "numpy"),
        ("yfinance",    "yfinance"),
        ("yaml",        "PyYAML"),
        ("dotenv",      "python-dotenv"),
        ("alpaca",      "alpaca-py"),
        ("rich",        "rich"),
        ("tabulate",    "tabulate"),
        ("schedule",    "schedule"),
        ("pytz",        "pytz"),
        ("requests",    "requests"),
    ]

    missing_packages = []
    for import_name, pip_name in required_packages:
        try:
            __import__(import_name)
        except ImportError:
            missing_packages.append(pip_name)

    packages_ok = len(missing_packages) == 0
    if packages_ok:
        results.append(("Required packages", True, f"All {len(required_packages)} packages found"))
    else:
        results.append(("Required packages", False, f"Missing: {', '.join(missing_packages)}"))
        results.append(("", False, "  Fix: Run:  pip install -r requirements.txt"))
    all_passed = all_passed and packages_ok

    # ---- 3. config.yaml ----
    config_exists = os.path.exists("config.yaml")
    results.append(("config.yaml", config_exists,
                    "Found" if config_exists else "MISSING — restore from project template"))
    all_passed = all_passed and config_exists

    # ---- 4. Load config (loads .env automatically) ----
    cfg = None
    if config_exists and packages_ok:
        try:
            from src.config import load_config, reset_config
            reset_config()  # Fresh load
            cfg = load_config()
        except Exception as e:
            results.append(("Load config", False, f"Error: {e}"))
            all_passed = False

    # ---- 5. .env file ----
    env_exists = os.path.exists(".env")
    results.append((".env file", env_exists,
                    "Found" if env_exists else "MISSING — see instructions below"))
    if not env_exists:
        results.append(("", False, "  Fix: Run:   cp .env.example .env"))
        results.append(("", False, "  Then fill in your Alpaca paper API keys"))
    all_passed = all_passed and env_exists

    # ---- 6. Check env variables via config ----
    if cfg is not None:
        env_status = cfg.get_env_status()

        paper_ok = env_status["paper_mode_true"]
        results.append(("ALPACA_PAPER=true", paper_ok,
                        "true" if paper_ok else "NOT SET or not 'true'"))
        if not paper_ok:
            results.append(("", False, "  Fix: Open .env and add: ALPACA_PAPER=true"))
        all_passed = all_passed and paper_ok

        key_ok = env_status["api_key_set"]
        results.append(("ALPACA_API_KEY", key_ok,
                        "Set (hidden for security)" if key_ok else "Missing or placeholder"))
        if not key_ok:
            results.append(("", False, "  Fix: Open .env and set ALPACA_API_KEY=your_real_key"))
        all_passed = all_passed and key_ok

        secret_ok = env_status["secret_key_set"]
        results.append(("ALPACA_SECRET_KEY", secret_ok,
                        "Set (hidden for security)" if secret_ok else "Missing or placeholder"))
        if not secret_ok:
            results.append(("", False, "  Fix: Open .env and set ALPACA_SECRET_KEY=your_real_secret"))
        all_passed = all_passed and secret_ok

        live_disabled = env_status["live_trading_disabled"]
        results.append(("Live trading disabled", live_disabled,
                        "DISABLED (correct)" if live_disabled else "WARNING: live trading enabled!"))
        all_passed = all_passed and live_disabled

    # ---- 7. Connect to Alpaca Paper Trading ----
    alpaca_ok = False
    account_status = None
    buying_power = None

    if cfg is not None and cfg.is_paper_mode_valid():
        try:
            from alpaca.trading.client import TradingClient

            print("  Connecting to Alpaca Paper Trading...", end="", flush=True)

            client = TradingClient(
                api_key=cfg.alpaca_api_key,
                secret_key=cfg.alpaca_secret_key,
                paper=True,  # ALWAYS paper=True — this is non-negotiable
            )

            account = client.get_account()
            account_status = str(account.status)
            buying_power = float(account.buying_power)
            alpaca_ok = True
            print(" connected!")

        except Exception as e:
            print(f" FAILED")
            error_msg = str(e)
            # Give friendly hints for common errors
            if "forbidden" in error_msg.lower() or "unauthorized" in error_msg.lower() or "403" in error_msg:
                hint = "API keys may be wrong or expired. Check your Alpaca paper trading keys."
            elif "connection" in error_msg.lower() or "timeout" in error_msg.lower():
                hint = "Network error. Check your internet connection."
            else:
                hint = f"Error: {error_msg}"
            results.append(("Alpaca connection", False, hint))
            all_passed = False

    if alpaca_ok:
        results.append(("Alpaca Paper connection", True, "Connected successfully"))
        status_ok = (account_status == "AccountStatus.ACTIVE")
        results.append(("Account status", status_ok,
                        account_status if account_status else "Unknown"))
        all_passed = all_passed and status_ok

        from src.utils import format_currency
        results.append(("Buying power", True, format_currency(buying_power)))
    elif cfg is not None and cfg.is_paper_mode_valid():
        # Connection was attempted but failed (already added to results above)
        pass
    elif cfg is not None:
        results.append(("Alpaca connection", False,
                        "Skipped — fix API key issues above first"))

    # ---- 8. reports/ folder ----
    os.makedirs("reports", exist_ok=True)
    results.append(("reports/ folder", True, "Ready"))

    # ---- Print all results ----
    print()
    for label, passed, detail in results:
        if not label:
            # Empty label = a hint/fix message, indent it
            if detail:
                print(f"       {detail}")
            continue
        status = "[OK]  " if passed else "[FAIL]"
        label_padded = (label + "...").ljust(40)
        print(f"  {label_padded} {status} {detail}")

    # ---- Final summary ----
    print()
    print("=" * 60)
    if all_passed:
        print()
        print("  PAPER TRADING MODE ONLY")
        print()
        print("  All checks passed! Your setup is complete.")
        print()
        print("  What to do next:")
        print("    1. Run:  python main.py explain")
        print("       (to understand how the algorithm works)")
        print()
        print("    Tell Claude: 'Stage 2 worked. Continue to Stage 3.'")
    else:
        print()
        print("  Some checks FAILED. Fix the issues listed above,")
        print("  then run this command again:")
        print()
        print("    python main.py setup-check")
        print()
        print("  If you need help, look at README.md Section 34:")
        print("  'Common Errors and Fixes'")
    print()
    print("=" * 60)
    print()


# ============================================================
# Command: explain
# ============================================================

def cmd_explain():
    """
    Prints a plain-English explanation of how the entire algorithm works,
    what each file does, and what order to run commands.
    """
    print_banner()
    print("  ALGORITHM EXPLANATION")
    print("=" * 60)

    text = """
WHAT THIS BOT DOES (Simple Version)
--------------------------------------
This bot is a paper trading robot. It uses fake money ($100,000)
in an Alpaca Paper Trading account to simulate a real trading strategy.

Every day it automatically:
  1. Discovers stocks from the market (no fixed watchlist)
  2. Filters out low-quality, risky, and illiquid stocks
  3. Calculates technical indicators (momentum, volume, trend)
  4. Reads catalyst data you provide (news events, earnings, etc.)
  5. Scores and ranks the best trade candidates
  6. Buys candidates that pass all conditions
  7. Monitors existing positions for exit signals
  8. Sells positions when exit conditions trigger
  9. Generates daily reports

THE STRATEGY IN SIMPLE TERMS
--------------------------------------
The strategy is called "Catalyst Momentum Breakout."

It looks for stocks where ALL of these things are true at once:

  1. PRICE BREAKOUT
     The stock is making a new 20-day high.
     Buyers are more aggressive than they have been in weeks.

  2. UPTREND CONFIRMED
     The stock is above its 50-day moving average.
     The longer-term trend is positive.

  3. STRONG RECENT MOMENTUM
     The stock is up more than 10% over the last 20 trading days.
     This filters out stocks that are just bouncing from a crash.

  4. VOLUME SURGE
     Today's trading volume is 2.5× or more than the 20-day average.
     Unusual volume often signals that large funds are buying.

  5. MARKET OUTPERFORMANCE
     The stock has beaten SPY (S&P 500) by more than 8% over 20 days.
     If something specific is driving the stock, it should beat the market.

  6. REAL CATALYST
     There is an identifiable news event with a score of 8+/10.
     The bot never trades on price action alone.

WHY ALL SIX CONDITIONS?
--------------------------------------
Each condition alone is just noise.
All six together means something real is probably happening.
An earnings beat + new high + high volume + market outperformance
= likely institutional buying = potential continuation move.

HOW THE BOT DISCOVERS STOCKS
--------------------------------------
Instead of a fixed watchlist, the bot:
  - Pulls Alpaca's "most active by volume" list
  - Pulls Alpaca's "most active by trade count" list
  - Scans for top gainers of the day
  - Scans yfinance for unusual volume
  - Scans yfinance for stocks making 20-day highs

All discovered stocks go through quality filters:
  - Price must be above $5
  - Market cap must be above $1 Billion
  - Average daily dollar volume must be above $50 Million
  - Must not be a leveraged ETF, warrant, preferred, or OTC stock
  - Must not be on the blocked symbols list

WHAT HAPPENS WITHOUT A CATALYST?
--------------------------------------
If a stock passes all price/volume/momentum filters but has NO catalyst
data in catalyst_overrides.csv, the bot will NOT trade it.

Instead, it saves the stock to:
  reports/needs_catalyst_review.csv

You can research these stocks manually. If you find a strong catalyst,
add it to data/catalyst_overrides.csv and it will be picked up next scan.

HOW THE BOT EXITS POSITIONS
--------------------------------------
The bot sells a position when ANY of these is true:

  - Stop loss:     Price drops 8% from entry
  - Trend break:   Price drops below the 10-day moving average
  - RS weakness:   Underperforms SPY for 5 straight trading days
  - Volume stall:  Volume dries up AND price stalls for 5 days
  - Time limit:    Held for more than 20 trading days

Winner management:
  - After +20%: Move stop to breakeven (can't lose money)
  - After +40%: Sell 1/3 of position, trail the rest on 10-day MA
  - After +75%: Trail the remainder on the 20-day MA

WHAT EACH FOLDER CONTAINS
--------------------------------------
  data/
    blocked_symbols.csv     - Stocks the bot will never trade
    catalyst_overrides.csv  - Your manual catalyst notes and scores
    universe_sample.csv     - Fallback stock list for testing only

  src/
    config.py               - Loads settings from config.yaml and .env
    utils.py                - Shared helper functions
    data_loader.py          - Downloads price/volume data from Yahoo Finance
    indicators.py           - Calculates all technical indicators
    universe_discovery.py   - (Stage 3) Discovers stocks from the market
    catalyst_scorer.py      - (Stage 4) Scores catalyst quality
    signal_engine.py        - (Stage 4) Generates buy/sell signals
    ranking.py              - (Stage 4) Ranks and filters candidates
    options_overlay.py      - (Stage 4) Educational options recommendations
    broker_alpaca.py        - (Stage 5) Connects to Alpaca Paper Trading
    execution_engine.py     - (Stage 5) Submits paper orders
    paper_trader.py         - (Stage 6) Manages the full trading workflow
    market_calendar.py      - (Stage 7) U.S. market hours and holidays
    scheduler.py            - (Stage 7) Runs tasks automatically
    backtester.py           - (Stage 8) Historical backtesting

  reports/
    daily_discovered_universe.csv - Stocks found today by auto-discovery
    paper_signals.csv       - Today's buy and sell signals
    needs_catalyst_review.csv - Stocks missing catalyst data
    options_overlay_recommendations.csv - Educational options ideas
    orders_log.csv          - Every order submitted with timestamps
    paper_state.json        - Current positions and account state
    scheduler_log.csv       - Log of every automated task

COMMAND ORDER (DAILY MANUAL MODE)
--------------------------------------
After market close (4:00 PM Eastern):
  python main.py discover-universe    (find today's candidate stocks)
  python main.py paper-scan           (generate buy/sell signals)

Next morning after 9:30 AM Eastern:
  python main.py paper-execute        (submit paper orders to Alpaca)
  python main.py paper-reconcile      (sync local records with Alpaca)
  python main.py paper-report         (view current status)

AUTOMATED MODE
--------------------------------------
Run once and leave it running:
  python main.py run-bot

The bot will handle the entire daily schedule automatically.
It knows U.S. market hours and holidays.

IMPORTANT REMINDERS
--------------------------------------
  - This bot uses PAPER TRADING ONLY (no real money)
  - ALPACA_PAPER must always be 'true' in your .env
  - The bot refuses to run without a catalyst score >= 8
  - You can always stop the bot with Ctrl+C
  - Use 'python main.py emergency-close-all' to close all paper positions
  - Nothing in this project is financial advice
"""

    print(text)
    print("=" * 60)
    print()
    print("  To run setup-check:")
    print("    python main.py setup-check")
    print()
    print("  When Stage 2 is working, tell Claude:")
    print("    'Stage 2 worked. Continue to Stage 3.'")
    print()
    print("=" * 60)
    print()


# ============================================================
# Command: scan (local fallback mode — Stage 2 stub)
# ============================================================

def cmd_scan_stub():
    """
    Stub for the 'scan' command.
    In Stage 2, this demonstrates that data_loader and indicators work
    by downloading one test stock and calculating its indicators.
    Full scan logic comes in Stage 3/4.
    """
    print_banner()
    print("  INDICATOR TEST (Stage 2 Demo)")
    print("=" * 60)
    print()
    print("  Testing data download and indicator calculation...")
    print("  Downloading NVDA (last 90 days from Yahoo Finance)...")
    print()

    try:
        from src.data_loader import download_single, download_spy_data
        from src.indicators import calculate_all_indicators
        from src.utils import format_currency, format_pct

        stock_df = download_single("NVDA", period="90d")
        spy_df = download_spy_data(period="90d")

        if stock_df is None:
            print("  [ERROR] Could not download NVDA data.")
            print("  This may happen if you have no internet connection,")
            print("  or if Yahoo Finance is temporarily unavailable.")
            print("  Try again in a moment.")
            return

        if spy_df is None:
            print("  [ERROR] Could not download SPY data.")
            return

        print(f"  Downloaded {len(stock_df)} days of NVDA data.")
        print(f"  Downloaded {len(spy_df)} days of SPY data.")
        print()

        indicators = calculate_all_indicators(stock_df, spy_df)

        if indicators["error"]:
            print(f"  [ERROR] Indicator calculation failed: {indicators['error']}")
            return

        close = indicators["close"]
        vol_ratio = indicators["volume_ratio"]
        ret_20d = indicators["return_20d"]
        rs = indicators["relative_strength_20d"]
        above_high = indicators["above_20d_high"]
        above_ma50 = indicators["above_50d_ma"]
        enough = indicators["enough_data"]

        print("  NVDA Indicator Results:")
        print("  " + "-" * 38)
        print(f"  Latest close price:   {format_currency(close) if close else 'N/A'}")

        if vol_ratio is not None:
            print(f"  Volume ratio (20d):   {vol_ratio:.2f}x  (need > 2.5x for signal)")
        else:
            print("  Volume ratio (20d):   Not enough data")

        if ret_20d is not None:
            print(f"  20-day return:        {format_pct(ret_20d)}  (need > 10%)")
        else:
            print("  20-day return:        Not enough data")

        if rs is not None:
            print(f"  Rel. strength vs SPY: {format_pct(rs)}  (need > 8%)")
        else:
            print("  Rel. strength vs SPY: Not enough data")

        print(f"  Above 20-day high:    {'YES' if above_high else 'NO'}")
        print(f"  Above 50-day MA:      {'YES' if above_ma50 else 'NO'}")
        print(f"  Enough history:       {'YES (55+ days)' if enough else 'NO (need 55+ days)'}")
        print()
        print("  Data download and indicator calculation: WORKING")
        print()

    except ImportError as e:
        print(f"  [ERROR] Could not import module: {e}")
        print("  Make sure your virtual environment is activated:")
        print("    Mac:     source venv/bin/activate")
        print("    Windows: venv\\Scripts\\activate")
        print("  Then run:  pip install -r requirements.txt")

    print("=" * 60)
    print()


# ============================================================
# Command: discover-universe
# ============================================================

def cmd_discover_universe():
    """
    Automatically discover candidate stocks from market-wide sources.

    Modes:
      Normal:   python main.py discover-universe
      Fallback: python main.py discover-universe --fallback
                  (uses data/universe_sample.csv only, no API calls)

    This command does NOT place any trades.
    It only collects, filters, and saves candidate stocks.
    """
    use_fallback = "--fallback" in sys.argv

    try:
        from src.config import load_config, reset_config
        from src.universe_discovery import run_discovery

        reset_config()
        cfg = load_config()

        # Discovery doesn't require Alpaca paper mode — it's just data collection.
        # But if Alpaca keys are missing, Alpaca sources will be skipped automatically.

        universe_df = run_discovery(cfg, use_fallback_only=use_fallback)

        if universe_df is not None and not universe_df.empty:
            print(f"\n  {len(universe_df)} stocks passed all filters.")
            print("  Ready for signal scanning.")
            print()
            print("  Next step: run the signal scanner:")
            print("    python main.py paper-scan")
        else:
            print()
            print("  No stocks passed all filters today.")
            print("  This is normal on weekends or when market data is unavailable.")
            print()
            print("  To test with a fixed list instead:")
            print("    python main.py discover-universe --fallback")

    except ImportError as e:
        print_banner()
        print(f"  [ERROR] Could not import required module: {e}")
        print()
        print("  Make sure your virtual environment is active:")
        print("    Mac:     source venv/bin/activate")
        print("    Windows: venv\\Scripts\\activate")
        print("  Then:  pip install -r requirements.txt")

    except Exception as e:
        print_banner()
        print(f"  [ERROR] Discovery failed: {e}")
        print()
        print("  Common causes:")
        print("  - No internet connection")
        print("  - Yahoo Finance temporarily unavailable (try again in a few minutes)")
        print("  - Missing config.yaml or .env file")
        print()
        print("  Run setup-check first:")
        print("    python main.py setup-check")


# ============================================================
# Command: paper-scan  (Stage 4)
# ============================================================

def cmd_paper_scan():
    """
    Run the full signal scan over today's discovered universe.

    Steps:
      1. Load config
      2. Auto-run discover-universe if no fresh universe exists for today
      3. Download price data for all discovered tickers + SPY
      4. Load catalysts from data/catalyst_overrides.csv
      5. Run the signal engine (buy signals, exit signals, needs-review)
      6. Rank buy signals by composite score
      7. Generate educational options overlay recommendations (top 3)
      8. Save all three report files to reports/
      9. Print a human-readable summary

    Output files:
      reports/paper_signals.csv                   — buy and exit signals
      reports/needs_catalyst_review.csv           — technical passes, no catalyst
      reports/options_overlay_recommendations.csv — educational options ideas

    This command does NOT place any orders.
    """
    use_fallback = "--fallback" in sys.argv

    print_banner()
    print("  PAPER SCAN")
    print("=" * 60)
    print()
    print("  PAPER TRADING ONLY — No orders will be placed.")
    print()

    try:
        from src.config import load_config, reset_config
        from src.universe_discovery import load_todays_universe, is_todays_universe_fresh, run_discovery
        from src.data_loader import download_price_data, download_spy_data
        from src.catalyst_scorer import load_catalysts, get_catalyst_count_summary
        from src.signal_engine import run_signal_scan, save_needs_review, save_signals_csv
        from src.ranking import rank_signals, calculate_position_size
        from src.options_overlay import generate_options_recommendations, save_options_recommendations
        from src.utils import format_currency, format_pct

        reset_config()
        cfg = load_config()

        # ---- Step 1: Ensure we have today's universe ----
        print("  [1/7] Checking for today's discovered universe...")

        universe_path = getattr(cfg, "discovered_universe_path",
                                "reports/daily_discovered_universe.csv")

        universe_df = None
        if not use_fallback and is_todays_universe_fresh(universe_path):
            universe_df = load_todays_universe(universe_path)
            if universe_df is not None and not universe_df.empty:
                print(f"        Found {len(universe_df)} tickers from today's discovery.")
            else:
                universe_df = None

        if universe_df is None or universe_df.empty:
            print("        No fresh universe found — running discover-universe now...")
            print()
            universe_df = run_discovery(cfg, use_fallback_only=use_fallback)
            print()

        if universe_df is None or universe_df.empty:
            print("  [ERROR] Could not build a universe of stocks to scan.")
            print()
            print("  Try running with the fallback sample list:")
            print("    python main.py paper-scan --fallback")
            return

        # Column is named "ticker" in the discovered universe CSV
        ticker_col = "ticker" if "ticker" in universe_df.columns else universe_df.columns[0]
        tickers = list(universe_df[ticker_col].dropna().unique())

        print(f"        Scanning {len(tickers)} tickers.")

        # ---- Step 2: Download price data ----
        print()
        print(f"  [2/7] Downloading price data for {len(tickers)} stocks + SPY...")
        print("        (This may take 1-2 minutes for large universes.)")

        price_data = download_price_data(tickers, period="90d")
        spy_df = download_spy_data(period="90d")

        downloaded = sum(1 for v in price_data.values() if v is not None and not v.empty)
        print(f"        Downloaded data for {downloaded}/{len(tickers)} tickers.")

        if spy_df is None or spy_df.empty:
            print("  [ERROR] Could not download SPY data (needed as benchmark).")
            print("  Check your internet connection and try again.")
            return

        # ---- Step 3: Load catalysts ----
        print()
        print("  [3/7] Loading catalyst data...")

        catalysts = load_catalysts(cfg.manual_catalyst_path)
        cat_summary = get_catalyst_count_summary(catalysts)

        print(f"        {cat_summary['total_tickers']} tickers in catalyst file.")
        print(f"        {cat_summary['fresh_tickers']} with fresh catalysts (within 10 days).")
        print(f"        {cat_summary['tradeable_tickers']} with tradeable catalysts (score >= 8).")

        if cat_summary["tradeable_tickers"] == 0:
            print()
            print("  NOTE: No tradeable catalysts found. All technical passes will go")
            print("        to needs_catalyst_review.csv for your manual research.")
            print("        To add catalysts, edit: data/catalyst_overrides.csv")

        # ---- Step 4: Load held positions (for exit checks) ----
        print()
        print("  [4/7] Loading portfolio state...")

        held_positions = _load_held_positions(cfg)
        if held_positions:
            print(f"        {len(held_positions)} open position(s) — will check exit conditions.")
        else:
            print("        No open positions (no exit checks needed).")

        # ---- Step 5: Run signal engine ----
        print()
        print("  [5/7] Running signal engine...")

        scan_result = run_signal_scan(
            universe=tickers,
            price_data=price_data,
            spy_df=spy_df,
            catalysts=catalysts,
            config=cfg,
            held_positions=held_positions,
        )

        buy_signals = scan_result["buy_signals"]
        exit_signals = scan_result["exit_signals"]
        needs_review = scan_result["needs_review"]

        print(f"        {scan_result['tickers_scanned']} tickers scanned.")
        print(f"        {len(buy_signals)} buy signal(s) found.")
        print(f"        {len(exit_signals)} exit signal(s) found.")
        print(f"        {len(needs_review)} stock(s) need catalyst review.")
        if scan_result["errors"] > 0:
            print(f"        {scan_result['errors']} ticker(s) had data errors (skipped).")

        # ---- Step 6: Rank buy signals ----
        print()
        print("  [6/7] Ranking buy signals...")

        current_position_count = len(held_positions)
        max_positions = getattr(cfg, "max_positions", 8)

        ranked_signals = rank_signals(
            buy_signals,
            max_positions=max_positions,
            current_position_count=current_position_count,
        )

        available_slots = max(0, max_positions - current_position_count)
        print(f"        {len(ranked_signals)} signal(s) ranked ({available_slots} slot(s) available).")

        # ---- Step 7: Options overlay ----
        print()
        print("  [7/7] Generating educational options recommendations...")

        max_recs = getattr(cfg, "options_max_recommendations", 3)
        long_itm = getattr(cfg, "options_long_call_itm_pct", 0.07)
        short_otm = getattr(cfg, "options_short_call_otm_pct", 0.20)
        target_dte = getattr(cfg, "options_target_dte", 75)

        options_recs = generate_options_recommendations(
            ranked_signals,
            max_recommendations=max_recs,
            long_call_itm_pct=long_itm,
            short_call_otm_pct=short_otm,
            target_dte=target_dte,
        )
        print(f"        {len(options_recs)} options recommendation(s) generated.")

        # ---- Save all reports ----
        save_signals_csv(ranked_signals, exit_signals)
        save_needs_review(needs_review)
        save_options_recommendations(options_recs)

        # ---- Print summary ----
        _print_scan_summary(ranked_signals, exit_signals, needs_review, options_recs, cfg)

    except ImportError as e:
        print(f"  [ERROR] Could not import required module: {e}")
        print()
        print("  Make sure your virtual environment is active:")
        print("    Mac:     source venv/bin/activate")
        print("    Windows: venv\\\\Scripts\\\\activate")
        print("  Then:  pip install -r requirements.txt")

    except Exception as e:
        import traceback
        print(f"  [ERROR] Paper scan failed: {e}")
        print()
        print("  Full error details:")
        traceback.print_exc()
        print()
        print("  Common causes:")
        print("  - No internet connection")
        print("  - Yahoo Finance temporarily unavailable (try again in a few minutes)")
        print("  - Missing config.yaml or .env file")


def _load_held_positions(cfg) -> dict:
    """
    Load current held positions from reports/paper_state.json.

    Returns an empty dict if the file does not exist or cannot be read.
    This is expected on first run (no positions open yet).
    """
    import json

    state_path = getattr(cfg, "state_file_path", "reports/paper_state.json")

    if not os.path.exists(state_path):
        return {}

    try:
        with open(state_path, "r") as f:
            state = json.load(f)
        positions = state.get("positions", {})
        return {str(k).upper(): v for k, v in positions.items() if isinstance(v, dict)}
    except Exception:
        return {}


def _print_scan_summary(ranked_signals, exit_signals, needs_review, options_recs, cfg):
    """Print a clean, human-readable scan summary."""
    from src.utils import format_currency, format_pct

    print()
    print("=" * 60)
    print("  PAPER SCAN RESULTS — PAPER TRADING ONLY, NO ORDERS PLACED")
    print("=" * 60)

    # ---- Buy signals ----
    print()
    if ranked_signals:
        print(f"  BUY SIGNALS ({len(ranked_signals)} stock(s)):")
        print()
        for i, sig in enumerate(ranked_signals, 1):
            ticker = sig.get("ticker", "?")
            close = sig.get("close")
            vol_r = sig.get("volume_ratio")
            ret = sig.get("return_20d")
            rs = sig.get("relative_strength_20d")
            cat_score = sig.get("catalyst_score", "?")
            cat_type = sig.get("catalyst_type", "?")
            cat_hl = sig.get("catalyst_headline", "")
            rank = sig.get("rank_score", 0)

            close_str = f"${close:.2f}" if close else "N/A"
            vol_str = f"{vol_r:.1f}x" if vol_r else "N/A"
            ret_str = format_pct(ret) if ret else "N/A"
            rs_str = format_pct(rs) if rs else "N/A"

            print(f"  {i}. {ticker:<6}  {close_str:<10} rank={rank:.3f}")
            print(f"     Volume: {vol_str}  Return: {ret_str}  RS: {rs_str}")
            print(f"     Catalyst: [{cat_score}/10] {cat_type} — {cat_hl}")

            # Position size estimate
            if close and hasattr(cfg, "starting_cash"):
                portfolio_val = getattr(cfg, "starting_cash", 100_000)
                target_pct = getattr(cfg, "target_position_size", 0.18)
                max_pct = getattr(cfg, "max_position_size", 0.25)
                tx_bps = getattr(cfg, "transaction_cost_bps", 5)
                slip_bps = getattr(cfg, "slippage_bps", 10)
                from src.ranking import calculate_position_size
                sizing = calculate_position_size(
                    portfolio_val, close, target_pct, max_pct, tx_bps, slip_bps
                )
                if sizing["shares"] > 0:
                    print(f"     Size est: ~{sizing['shares']} shares "
                          f"({format_currency(sizing['adjusted_dollars'])})")
            print()
    else:
        print("  BUY SIGNALS: None today.")
        print()
        print("  No stocks met all 6 conditions simultaneously.")
        print("  Common reasons:")
        print("    - No stocks had both a strong catalyst (score >= 8) AND all 5 technical conditions")
        print("    - Make sure pull-news and score-catalysts have run today")
        print("    - Market conditions not meeting volume/momentum thresholds")
        print()

    # ---- Exit signals ----
    if exit_signals:
        print(f"  EXIT SIGNALS ({len(exit_signals)} position(s) to close):")
        print()
        for sig in exit_signals:
            ticker = sig.get("ticker", "?")
            signal_type = sig.get("signal_type", "EXIT")
            reason = sig.get("reason", "")
            gain = sig.get("gain_pct")
            gain_str = format_pct(gain) if gain is not None else "N/A"
            partial = sig.get("partial_fraction", 1.0)
            if signal_type == "EXIT_PARTIAL":
                print(f"  - {ticker}: SELL {partial:.0%} of position  reason: {reason}  gain: {gain_str}")
            else:
                print(f"  - {ticker}: SELL ALL  reason: {reason}  gain: {gain_str}")
        print()
    else:
        print("  EXIT SIGNALS: None. All held positions are healthy.")
        print()

    # ---- Needs review ----
    if needs_review:
        print(f"  NEEDS CATALYST REVIEW ({len(needs_review)} stock(s)):")
        for rec in needs_review[:10]:  # Show max 10
            ticker = rec.get("ticker", "?")
            vol_r = rec.get("volume_ratio")
            vol_str = f"{vol_r:.1f}x" if vol_r else "N/A"
            print(f"    {ticker:<6} volume={vol_str} — low catalyst score (see reports/automated_catalyst_scores.csv)")
        if len(needs_review) > 10:
            print(f"    ... and {len(needs_review) - 10} more in reports/needs_catalyst_review.csv")
        print()

    # ---- Options recommendations ----
    if options_recs:
        print("  OPTIONS RECOMMENDATIONS (EDUCATIONAL ONLY — NOT REAL ORDERS):")
        print()
        for rec in options_recs:
            print(f"  {rec['ticker']}: Bull Call Spread")
            print(f"    Buy  ${rec['long_strike']} call / Sell ${rec['short_strike']} call")
            print(f"    Expiry ~{rec['estimated_expiry']} ({rec['target_dte']} DTE)")
            print(f"    Target: {rec['target_gain_pct']}")
            print(f"    NOTE: {rec['note']}")
            print()

    # ---- Report file locations ----
    print("  REPORTS SAVED:")
    print("    reports/paper_signals.csv                    (buy and exit signals)")
    print("    reports/needs_catalyst_review.csv            (stocks needing catalysts)")
    print("    reports/options_overlay_recommendations.csv  (educational options ideas)")
    print()
    print("  NEXT STEP:")
    if ranked_signals:
        print("  Execute today's signals with:")
        print("    python main.py paper-execute")
    else:
        print("  Re-run tomorrow after pull-news + score-catalysts:")
        print("    python main.py full-auto-cycle")
    print()
    print("=" * 60)
    print()


# ============================================================
# Command: paper-execute  (Stage 5)
# ============================================================

def cmd_paper_execute():
    """
    Read today's signals from reports/paper_signals.csv and submit
    paper orders to Alpaca for all signals that pass pre-order checks.

    Requires:
      - ALPACA_PAPER=true in .env
      - Valid API keys
      - reports/paper_signals.csv from a recent paper-scan run

    Logs every order attempt to reports/orders_log.csv.
    """
    print_banner()
    print("  PAPER EXECUTE")
    print("=" * 60)
    print()
    print("  PAPER TRADING ONLY — All orders use Alpaca Paper Trading.")
    print()

    try:
        from src.config import load_config, reset_config
        from src.execution_engine import run_paper_execute

        reset_config()
        cfg = load_config()

        result = run_paper_execute(cfg)

        print()
        print("=" * 60)
        print("  EXECUTION SUMMARY")
        print("=" * 60)
        print()

        buys_submitted = result.get("buys_submitted", 0)
        buys_blocked   = result.get("buys_blocked", 0)
        sells_submitted = result.get("sells_submitted", 0)
        sells_blocked   = result.get("sells_blocked", 0)

        print(f"  BUY orders:  {buys_submitted} submitted,  {buys_blocked} blocked")
        print(f"  SELL orders: {sells_submitted} submitted, {sells_blocked} blocked")
        print()

        orders = result.get("orders", [])
        if orders:
            print("  Orders placed:")
            for o in orders:
                side = o.get("side", "?").upper()
                ticker = o.get("ticker", "?")
                status = o.get("status", "?")
                notional = o.get("notional")
                qty = o.get("qty")
                if notional:
                    print(f"    {side:<4} {ticker:<6}  ${notional:,.2f}  status={status}")
                else:
                    print(f"    {side:<4} {ticker:<6}  qty={qty}  status={status}")
            print()

        print("  Full order log: reports/orders_log.csv")
        print()
        print("  Next step: after market close, reconcile with Alpaca:")
        print("    python main.py paper-reconcile")
        print()
        print("=" * 60)
        print()

    except RuntimeError as e:
        print_banner()
        print(f"  [BLOCKED] {e}")
        print()
        print("  Fix the issue above, then try again.")
        print()

    except ImportError as e:
        print_banner()
        print(f"  [ERROR] Could not import required module: {e}")
        print()
        print("  Make sure your virtual environment is active:")
        print("    Mac:     source venv/bin/activate")
        print("    Windows: venv\\Scripts\\activate")
        print("  Then:  pip install -r requirements.txt")

    except Exception as e:
        import traceback
        print_banner()
        print(f"  [ERROR] paper-execute failed: {e}")
        print()
        traceback.print_exc()


# ============================================================
# Command: paper-reconcile  (Stage 5)
# ============================================================

def cmd_paper_reconcile():
    """
    Sync local paper_state.json with actual Alpaca Paper positions.

    Stage 6 improvement over Stage 5: also updates peak_price for every
    position after syncing, enabling accurate winner management stops.

    Use this after market close to ensure local records match what Alpaca
    reports as actually filled.
    """
    print_banner()
    print("  PAPER RECONCILE")
    print("=" * 60)
    print()
    print("  Syncing local state with Alpaca Paper Trading...")
    print()

    try:
        from src.config import load_config, reset_config
        from src.paper_trader import run_reconcile_with_portfolio
        from src.utils import format_currency

        reset_config()
        cfg = load_config()

        state = run_reconcile_with_portfolio(cfg)

        print()
        print("=" * 60)
        print("  RECONCILE SUMMARY")
        print("=" * 60)
        print()

        positions = state.get("positions", {})
        portfolio_value = float(state.get("portfolio_value") or 0)
        buying_power    = float(state.get("buying_power") or 0)
        cash            = float(state.get("cash") or 0)

        print(f"  Portfolio value:  {format_currency(portfolio_value)}")
        print(f"  Cash:             {format_currency(cash)}")
        print(f"  Buying power:     {format_currency(buying_power)}")
        print()

        if positions:
            print(f"  Open positions ({len(positions)}):")
            print()
            for ticker, pos in positions.items():
                qty     = pos.get("shares", 0)
                mkt_val = float(pos.get("market_value") or 0)
                upl     = float(pos.get("unrealized_pl") or 0)
                uplpc   = float(pos.get("unrealized_plpc") or 0)
                peak    = float(pos.get("peak_price") or 0)
                sign    = "+" if upl >= 0 else ""
                print(f"    {ticker:<6}  {qty} shares  "
                      f"{format_currency(mkt_val)}  "
                      f"P/L: {sign}{format_currency(upl)} ({sign}{uplpc*100:.2f}%)  "
                      f"peak={format_currency(peak)}")
            print()
        else:
            print("  No open positions.")
            print()

        print("  Local state updated: reports/paper_state.json")
        print("  (peak_price updated for all positions)")
        print()
        print("  Next step: run paper-report to see full portfolio status:")
        print("    python main.py paper-report")
        print()
        print("=" * 60)
        print()

    except RuntimeError as e:
        print_banner()
        print(f"  [BLOCKED] {e}")
        print()
        print("  Fix the issue above, then try again.")
        print()

    except ImportError as e:
        print_banner()
        print(f"  [ERROR] Could not import required module: {e}")

    except Exception as e:
        import traceback
        print_banner()
        print(f"  [ERROR] paper-reconcile failed: {e}")
        print()
        traceback.print_exc()


# ============================================================
# Command: paper-report  (Stage 6)
# ============================================================

def cmd_paper_report():
    """
    Display a full paper trading portfolio report.

    Connects to Alpaca to fetch live account balance and position
    prices. Merges with local paper_state.json to show entry prices,
    catalyst notes, and stop levels. Prints a formatted table.
    """
    print_banner()
    print("  PAPER REPORT")
    print("=" * 60)
    print()
    print("  PAPER TRADING ONLY — Fetching live data from Alpaca...")
    print()

    try:
        from src.config import load_config, reset_config
        from src.paper_trader import run_paper_report
        from src.utils import format_currency, format_pct

        reset_config()
        cfg = load_config()

        report = run_paper_report(cfg)
        _print_paper_report(report, cfg)

    except RuntimeError as e:
        print_banner()
        print(f"  [BLOCKED] {e}")
        print()
        print("  Fix the issue above, then try again.")
        print()

    except ImportError as e:
        print_banner()
        print(f"  [ERROR] Could not import required module: {e}")

    except Exception as e:
        import traceback
        print_banner()
        print(f"  [ERROR] paper-report failed: {e}")
        print()
        traceback.print_exc()


def _print_paper_report(report: dict, cfg):
    """Print a formatted paper portfolio report to the terminal."""
    from src.utils import format_currency, format_pct

    summary = report.get("summary", {})
    positions = report.get("positions", [])
    recent_orders = report.get("recent_orders", [])

    portfolio_value  = summary.get("portfolio_value", 0)
    cash             = summary.get("cash", 0)
    buying_power     = summary.get("buying_power", 0)
    position_count   = summary.get("position_count", 0)
    total_pl         = summary.get("total_unrealized_pl", 0)
    total_plpc       = summary.get("total_unrealized_plpc", 0)
    total_return_pct = summary.get("total_return_pct", 0)
    starting_cash    = summary.get("starting_cash", 100_000)

    # ---- Portfolio summary ----
    print("=" * 60)
    print("  PORTFOLIO SUMMARY")
    print("=" * 60)
    print()

    return_sign = "+" if total_return_pct >= 0 else ""
    print(f"  Portfolio value:  {format_currency(portfolio_value)}"
          f"  ({return_sign}{format_pct(total_return_pct)} vs starting"
          f" {format_currency(starting_cash)})")
    print(f"  Cash:             {format_currency(cash)}")
    print(f"  Buying power:     {format_currency(buying_power)}")
    print(f"  Open positions:   {position_count}")

    if total_pl != 0:
        pl_sign = "+" if total_pl >= 0 else ""
        print(f"  Open P/L:         {pl_sign}{format_currency(total_pl)}"
              f"  ({pl_sign}{format_pct(total_plpc)})")
    print()

    # ---- Open positions ----
    if positions:
        print(f"  OPEN POSITIONS  ({len(positions)})")
        print()
        print(f"  {'Ticker':<6}  {'Shares':>6}  {'Entry':>8}  {'Current':>8}  "
              f"{'P/L':>12}  {'Days':>4}  Stop / Trail")
        print("  " + "-" * 72)

        for pos in positions:
            ticker   = pos["ticker"]
            shares   = pos["shares"]
            entry    = pos["entry_price"]
            current  = pos["current_price"]
            pl       = pos["unrealized_pl"]
            plpc     = pos["unrealized_plpc"]
            days     = pos["holding_days"]
            stop_type = pos["stop_type"]
            stop_price = pos["stop_price"]
            tier     = pos["gain_tier"]

            pl_sign  = "+" if pl >= 0 else ""
            pl_str   = f"{pl_sign}{format_currency(pl)} ({pl_sign}{format_pct(plpc)})"

            if stop_price is not None:
                stop_str = f"{stop_type} @ {format_currency(stop_price)}"
            else:
                stop_str = stop_type  # e.g. "trail_10d_ma"

            # Winner management indicator
            tier_tag = {"breakeven": " [BE]", "partial": " [P⅓]",
                        "big_winner": " [75%]", "normal": ""}.get(tier, "")

            print(f"  {ticker:<6}  {shares:>6}  ${entry:>7.2f}  ${current:>7.2f}  "
                  f"{pl_str:>12}  {days:>4}  {stop_str}{tier_tag}")

            cat_hl = pos.get("catalyst_headline", "")
            cat_sc = pos.get("catalyst_score", "")
            if cat_hl:
                print(f"         catalyst: [{cat_sc}/10] {cat_hl}")
        print()
    else:
        print("  OPEN POSITIONS:  None.")
        print()
        print("  No open paper positions. Run paper-scan + paper-execute to enter trades.")
        print()

    # ---- Recent orders ----
    if recent_orders:
        print("  RECENT ORDERS (last 10)")
        print()
        print(f"  {'Date':<10}  {'Ticker':<6}  {'Type':<14}  {'Action':<10}  "
              f"{'Amount':>14}  Note")
        print("  " + "-" * 72)
        for order in recent_orders[:10]:
            date_str   = str(order.get("date", ""))[:10]
            ticker     = str(order.get("ticker", ""))
            sig_type   = str(order.get("signal_type", ""))
            action     = str(order.get("action", ""))
            notional   = order.get("notional", "")
            qty        = order.get("qty", "")
            fail_reason = str(order.get("fail_reason", ""))

            if notional:
                try:
                    amount_str = format_currency(float(notional))
                except (TypeError, ValueError):
                    amount_str = str(notional)
            elif qty:
                amount_str = f"{qty} shares"
            else:
                amount_str = ""

            note = fail_reason[:28] if action == "BLOCKED" else ""
            print(f"  {date_str:<10}  {ticker:<6}  {sig_type:<14}  "
                  f"{action:<10}  {amount_str:>14}  {note}")
        print()

    # ---- Exit alerts ----
    from src.paper_trader import check_positions_for_exits
    from src.execution_engine import load_state

    state_path = getattr(cfg, "state_file_path", "reports/paper_state.json")
    try:
        state = load_state(state_path)
        hard_exits = check_positions_for_exits(state, cfg)
        if hard_exits:
            print("  EXIT ALERTS (hard stop / breakeven / time limit):")
            print()
            for ex in hard_exits:
                print(f"  *** {ex['ticker']}: {ex['reason']}  "
                      f"(gain: {'+' if ex['gain_pct']>=0 else ''}"
                      f"{format_pct(ex['gain_pct'])})")
            print()
            print("  Run paper-execute to submit these exits, or paper-scan")
            print("  for a full signal check including MA-based exits.")
            print()
    except Exception:
        pass  # best-effort — don't break the report if state is unavailable

    # ---- Report file locations ----
    print("  REPORTS SAVED LOCALLY:")
    print("    reports/paper_state.json   (current portfolio state)")
    print("    reports/orders_log.csv     (full order history)")
    print()
    print("  NEXT STEPS:")
    if positions:
        print("    Run paper-scan after market close to refresh exit signals:")
        print("      python main.py paper-scan")
    else:
        print("    Run paper-scan then paper-execute to open positions:")
        print("      python main.py paper-scan")
        print("      python main.py paper-execute")
    print()
    print("=" * 60)
    print()


# ============================================================
# Command: emergency-close-all  (Stage 5)
# ============================================================

def cmd_emergency_close_all():
    """
    Cancel ALL pending orders and close EVERY open position at market price.

    This is an irreversible action in your paper account.
    Requires typing "CLOSE ALL" to confirm.
    """
    print_banner()
    print("  EMERGENCY CLOSE ALL")
    print("=" * 60)
    print()
    print("  WARNING: This will cancel ALL pending orders and close")
    print("  EVERY open position in your Alpaca Paper account.")
    print()
    print("  This action cannot be undone.")
    print()

    try:
        confirm = input("  Type  CLOSE ALL  to confirm (anything else cancels): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        print("  Cancelled.")
        return

    if confirm != "CLOSE ALL":
        print()
        print("  Cancelled — no orders were submitted.")
        print()
        return

    print()
    print("  Proceeding with emergency close...")
    print()

    try:
        from src.config import load_config, reset_config
        from src.execution_engine import run_emergency_close_all

        reset_config()
        cfg = load_config()

        result = run_emergency_close_all(cfg)

        print()
        print("=" * 60)
        print("  EMERGENCY CLOSE SUMMARY")
        print("=" * 60)
        print()

        cancelled = result.get("orders_cancelled", 0)
        closed    = result.get("positions_closed", 0)
        errors    = result.get("errors", [])

        print(f"  Pending orders cancelled: {cancelled}")
        print(f"  Positions closed:         {closed}")

        if errors:
            print()
            print(f"  Errors ({len(errors)}):")
            for err in errors:
                print(f"    - {err}")

        print()
        print("  Full log: reports/orders_log.csv")
        print()
        print("=" * 60)
        print()

    except RuntimeError as e:
        print_banner()
        print(f"  [BLOCKED] {e}")
        print()

    except ImportError as e:
        print_banner()
        print(f"  [ERROR] Could not import required module: {e}")

    except Exception as e:
        import traceback
        print_banner()
        print(f"  [ERROR] emergency-close-all failed: {e}")
        print()
        traceback.print_exc()


# ============================================================
# Command: run-once-full-cycle  (Stage 7)
# ============================================================

def cmd_run_once_full_cycle():
    """
    Run all six pipeline tasks in logical order, right now.

    Order: discover_universe → paper_scan → paper_execute →
           paper_reconcile → intraday_exit_check → paper_report

    Skips any task that already completed successfully today
    (safe to re-run after a partial failure).

    Does NOT check market hours — runs all tasks regardless of time.
    """
    print_banner()
    print("  RUN ONCE — FULL PIPELINE CYCLE")
    print("=" * 60)
    print()
    print("  PAPER TRADING ONLY — Running all tasks in order...")
    print()

    try:
        from src.config import load_config, reset_config
        from src.scheduler import run_full_cycle

        reset_config()
        cfg = load_config()

        result = run_full_cycle(cfg)

        print()
        print("=" * 60)
        print("  FULL CYCLE COMPLETE")
        print("=" * 60)
        print()
        print(f"  Tasks run:     {result['tasks_run']}")
        print(f"  Tasks skipped: {result['tasks_skipped']}  (already ran today)")
        print(f"  Tasks failed:  {result['tasks_failed']}")
        print()

        if result["results"]:
            print("  Task results:")
            print()
            for r in result["results"]:
                status = r["status"]
                name   = r["task_name"]
                secs   = r["duration"]
                detail = r["detail"]
                if status == "SUCCESS":
                    print(f"    [OK]      {name:<25}  ({secs:.1f}s)  {detail}")
                elif status == "SKIPPED":
                    print(f"    [SKIP]    {name:<25}  already ran today")
                else:
                    print(f"    [FAILED]  {name:<25}  {detail}")
            print()

        if result["tasks_failed"] > 0:
            print("  One or more tasks failed. Check the error messages above.")
            print("  Run again to retry only the failed tasks (successful tasks are skipped).")
            print()

        print("  Log: reports/scheduler_log.csv")
        print()
        print("=" * 60)
        print()

    except RuntimeError as e:
        print_banner()
        print(f"  [BLOCKED] {e}")
        print()
        print("  Fix the issue above, then try again.")
        print()

    except ImportError as e:
        print_banner()
        print(f"  [ERROR] Could not import required module: {e}")
        print()
        print("  Make sure your virtual environment is active:")
        print("    Mac:     source venv/bin/activate")
        print("    Windows: venv\\Scripts\\activate")
        print("  Then:  pip install -r requirements.txt")

    except Exception as e:
        import traceback
        print_banner()
        print(f"  [ERROR] run-once-full-cycle failed: {e}")
        print()
        traceback.print_exc()


# ============================================================
# Command: run-bot  (Stage 7)
# ============================================================

def cmd_run_bot():
    """
    Start the automated paper trading bot.

    Runs indefinitely, waking up every 60 seconds to check whether
    any scheduled tasks are due. Each task runs at most once per day.

    Daily schedule (Eastern time):
      09:32  paper_execute          — submit orders after open
      09:45  paper_reconcile        — sync with Alpaca after fills
      15:45  intraday_exit_check    — check hard stops before close
      16:10  paper_report           — end-of-day summary
      16:20  discover_universe      — find stocks for tomorrow
      16:35  paper_scan             — generate signals for tomorrow

    Press Ctrl+C to stop cleanly.
    """
    print_banner()
    print("  RUN BOT — AUTOMATED PAPER TRADING SCHEDULER")
    print("=" * 60)
    print()
    print("  PAPER TRADING ONLY — Starting continuous scheduler...")
    print("  Press Ctrl+C at any time to stop.")
    print()

    try:
        from src.config import load_config, reset_config
        from src.scheduler import run_bot

        reset_config()
        cfg = load_config()

        run_bot(cfg)

    except KeyboardInterrupt:
        print()
        print("  Stopped by user.")
        print()

    except RuntimeError as e:
        print_banner()
        print(f"  [BLOCKED] {e}")
        print()
        print("  The bot cannot start. Fix the issue above and try again.")
        print()
        print("  Common causes:")
        print("  - ALPACA_PAPER is not set to 'true' in your .env file")
        print("  - Missing or invalid API keys")
        print()

    except ImportError as e:
        print_banner()
        print(f"  [ERROR] Could not import required module: {e}")
        print()
        print("  Make sure your virtual environment is active:")
        print("    Mac:     source venv/bin/activate")
        print("    Windows: venv\\Scripts\\activate")
        print("  Then:  pip install -r requirements.txt")

    except Exception as e:
        import traceback
        print_banner()
        print(f"  [ERROR] run-bot failed unexpectedly: {e}")
        print()
        traceback.print_exc()


# ============================================================
# Command: backtest  (Stage 8)
# ============================================================

def cmd_backtest():
    """
    Run a historical backtest of the AI Catalyst Momentum strategy.

    Required arguments:
      --start YYYY-MM-DD    First day of the backtest period
      --end   YYYY-MM-DD    Last day of the backtest period

    Optional arguments:
      --tickers A,B,C       Comma-separated list of tickers to scan
                            (default: uses data/universe_sample.csv)
      --skip-catalyst       Remove the catalyst filter — test technical
                            conditions only. See the catalyst note below.

    Examples:
      python main.py backtest --start 2024-01-01 --end 2024-12-31
      python main.py backtest --start 2023-01-01 --end 2024-12-31 --skip-catalyst
      python main.py backtest --start 2024-01-01 --end 2024-12-31 --tickers NVDA,AMD,MSFT

    CATALYST NOTE:
      This strategy requires a catalyst (score >= 8) for every entry.
      Without dated historical catalysts in data/catalyst_overrides.csv,
      the backtest will produce 0 trades.

      Options:
        1. Add historical catalyst entries with dates to catalyst_overrides.csv
        2. Use --skip-catalyst to test the technical side only (no catalyst required)

      Results from --skip-catalyst show how often technical conditions aligned,
      but do NOT represent real tradeable setups (which all required catalysts).
    """
    # ---- Parse arguments ----
    args = sys.argv[2:]  # everything after "backtest"

    def _get_arg(flag: str) -> str:
        """Get the value after a flag, e.g. --start 2024-01-01 → '2024-01-01'."""
        if flag in args:
            idx = args.index(flag)
            if idx + 1 < len(args):
                return args[idx + 1]
        return ""

    start_str = _get_arg("--start")
    end_str = _get_arg("--end")
    tickers_str = _get_arg("--tickers")
    skip_catalyst = "--skip-catalyst" in args

    print_banner()
    print("  BACKTEST — AI Catalyst Momentum Strategy")
    print("=" * 60)
    print()

    if not start_str or not end_str:
        print("  [ERROR] Both --start and --end are required.")
        print()
        print("  Example:")
        print("    python main.py backtest --start 2024-01-01 --end 2024-12-31")
        print()
        print("  To bypass the catalyst filter (technical conditions only):")
        print("    python main.py backtest --start 2024-01-01 --end 2024-12-31 --skip-catalyst")
        return

    # Parse dates
    try:
        from datetime import date as date_type
        start_date = date_type.fromisoformat(start_str)
        end_date = date_type.fromisoformat(end_str)
    except ValueError:
        print(f"  [ERROR] Could not parse dates. Use YYYY-MM-DD format.")
        print(f"    --start: {start_str!r}")
        print(f"    --end:   {end_str!r}")
        return

    if start_date >= end_date:
        print("  [ERROR] --start must be before --end.")
        return

    if (end_date - start_date).days < 30:
        print("  [ERROR] Backtest period must be at least 30 days.")
        return

    print(f"  Period:         {start_date}  to  {end_date}")
    print(f"  Catalyst filter: {'OFF (--skip-catalyst)' if skip_catalyst else 'ON'}")
    print()

    try:
        from src.config import load_config, reset_config
        from src.catalyst_scorer import load_catalysts
        from src.backtester import BacktestEngine, save_backtest_results

        reset_config()
        cfg = load_config()

        # ---- Build universe ----
        if tickers_str:
            tickers = [t.strip().upper() for t in tickers_str.split(",") if t.strip()]
            print(f"  Universe:       {len(tickers)} tickers from --tickers flag")
        else:
            universe_df = _load_backtest_universe(cfg)
            if universe_df is None or universe_df.empty:
                print("  [ERROR] Could not load a universe.")
                print("  Use --tickers to specify tickers explicitly:")
                print("    python main.py backtest --start 2024-01-01 --end 2024-12-31 --tickers NVDA,AMD,MSFT")
                return
            col = "ticker" if "ticker" in universe_df.columns else universe_df.columns[0]
            tickers = list(universe_df[col].dropna().unique())
            print(f"  Universe:       {len(tickers)} tickers from universe_sample.csv")

        print(f"  Tickers:        {', '.join(tickers[:8])}{'...' if len(tickers) > 8 else ''}")
        print()

        # ---- Load catalysts ----
        catalyst_path = getattr(cfg, "manual_catalyst_path", "data/catalyst_overrides.csv")
        catalysts = load_catalysts(catalyst_path)

        if not skip_catalyst:
            total_catalysts = sum(len(v) for v in catalysts.values())
            print(f"  Catalysts:      {total_catalysts} entries loaded from {catalyst_path}")
            if total_catalysts == 0:
                print()
                print("  NOTE: No catalyst data found.")
                print("  The backtest will likely produce 0 trades because every entry")
                print("  requires a catalyst with score >= 8.")
                print()
                print("  Options:")
                print("    1. Add historical catalyst entries to data/catalyst_overrides.csv")
                print("       with a 'date' field (e.g. date=2024-03-15)")
                print("    2. Run with --skip-catalyst to test technical conditions only")
                print("       python main.py backtest --start ... --end ... --skip-catalyst")
                print()
        else:
            print("  Catalysts:      SKIPPED (--skip-catalyst mode)")
            print("                  NOTE: Results show technical-only signals.")
            print("                  Real trading required catalysts — interpret with caution.")
            print()

        # ---- Run backtest ----
        print("  Running backtest...")
        print("  (This downloads historical data and may take 1–3 minutes)")
        print()

        engine = BacktestEngine()
        result = engine.run(
            config=cfg,
            start_date=start_date,
            end_date=end_date,
            universe=tickers,
            catalysts=catalysts,
            skip_catalyst=skip_catalyst,
        )

        # ---- Save results ----
        save_backtest_results(result)

        # ---- Print report ----
        _print_backtest_report(result, cfg)

    except RuntimeError as e:
        print(f"  [ERROR] {e}")
        print()

    except ImportError as e:
        print(f"  [ERROR] Could not import required module: {e}")
        print()
        print("  Make sure your virtual environment is active:")
        print("    Mac:     source venv/bin/activate")
        print("    Windows: venv\\Scripts\\activate")
        print("  Then:  pip install -r requirements.txt")

    except Exception as e:
        import traceback
        print(f"  [ERROR] Backtest failed: {e}")
        print()
        traceback.print_exc()


# ============================================================
# Stage 9 commands: full automation
# ============================================================

def cmd_pull_news():
    """
    Pull recent news headlines for all tickers in today's discovered universe.

    Tries Alpaca News API first, then Yahoo Finance fallback.
    Saves results to reports/news_cache.csv.
    Does NOT trade.

    Usage:
        python main.py pull-news
    """
    print_banner()
    print("  PULL NEWS  —  PAPER MODE ONLY")
    print("=" * 60)
    print()

    try:
        from src.config import load_config, reset_config
        from src.paper_trader import run_pull_news

        reset_config()
        cfg = load_config()

        print("  Pulling news for discovered universe...")
        print("  (Uses Alpaca News → Yahoo Finance → cached fallback)")
        print()

        result = run_pull_news(cfg)

        if result.get("error"):
            print(f"  [ERROR] {result['error']}")
            print()
            print("  Fix: run discover-universe first:")
            print("    python main.py discover-universe")
            return

        print(f"  Articles pulled:    {result['news_pulled']}")
        print(f"  Tickers covered:    {result['tickers_covered']}")
        print(f"  Tickers with no news: {result['no_news_tickers']}")
        print()
        print("  Saved to: reports/news_cache.csv")
        print()
        print("  Next step:")
        print("    python main.py score-catalysts")
        print()

    except Exception as e:
        import traceback
        print(f"  [ERROR] {e}")
        traceback.print_exc()


def cmd_score_catalysts():
    """
    Score catalysts for all tickers using the heuristic scorer.

    Reads reports/news_cache.csv, scores every ticker automatically,
    applies manual overrides from data/catalyst_overrides.csv.
    Saves to reports/automated_catalyst_scores.csv.
    Does NOT trade.

    Usage:
        python main.py score-catalysts
    """
    print_banner()
    print("  SCORE CATALYSTS  —  HEURISTIC SCORING")
    print("=" * 60)
    print()

    try:
        from src.config import load_config, reset_config
        from src.paper_trader import run_score_catalysts

        reset_config()
        cfg = load_config()

        print("  Scoring catalysts from news cache...")
        print("  (Heuristic keyword-based scoring — no API key required)")
        print()

        result = run_score_catalysts(cfg)

        if result.get("error"):
            print(f"  [ERROR] {result['error']}")
            print()
            print("  Fix: pull news first:")
            print("    python main.py pull-news")
            return

        print(f"  Tickers scored:     {result['scored']}")
        print(f"  Tradeable (>= 8):   {result['tradeable']}")
        print(f"  Needs review:       {result['needs_review']}")
        print()
        print("  Saved to: reports/automated_catalyst_scores.csv")
        if result["needs_review"] > 0:
            print(f"  Review needed:    reports/needs_catalyst_review.csv")
        print()
        print("  Next step:")
        print("    python main.py paper-scan")
        print()

    except Exception as e:
        import traceback
        print(f"  [ERROR] {e}")
        traceback.print_exc()


def cmd_full_auto_cycle():
    """
    Run the full automated pipeline in order.

    Pipeline (no execution by default):
      1. discover-universe
      2. pull-news
      3. score-catalysts
      4. paper-scan

    With --execute-paper flag, also runs:
      5. paper-execute
      6. paper-reconcile

    Each step is skipped if it already ran successfully today.
    Does NOT execute orders unless --execute-paper is explicitly passed.

    Usage:
        python main.py full-auto-cycle
        python main.py full-auto-cycle --execute-paper
    """
    print_banner()
    execute_paper = "--execute-paper" in sys.argv

    if execute_paper:
        print("  FULL AUTO CYCLE  —  WITH PAPER EXECUTION")
    else:
        print("  FULL AUTO CYCLE  —  DRY RUN (no order execution)")
    print("=" * 60)
    print()

    if execute_paper:
        print("  WARNING: --execute-paper is set.")
        print("  This will submit actual paper orders to Alpaca.")
        print("  All safety checks will run first.")
        print()

    try:
        from src.config import load_config, reset_config
        from src.scheduler import run_full_cycle

        reset_config()
        cfg = load_config()

        # Determine which tasks to skip
        skip = []
        if not execute_paper:
            skip = ["paper_execute", "paper_reconcile"]

        print("  Running pipeline tasks:")
        for task in ["discover_universe", "pull_news", "score_catalysts", "paper_scan"]:
            print(f"    {task}")
        if execute_paper:
            print(f"    paper_execute")
            print(f"    paper_reconcile")
        else:
            print(f"    paper_execute  [SKIPPED — no --execute-paper flag]")
            print(f"    paper_reconcile [SKIPPED]")
        print(f"    intraday_exit_check [SKIPPED — market-hours only]")
        print(f"    paper_report [SKIPPED — use paper-report command]")
        print()

        skip_all_except = ["discover_universe", "pull_news", "score_catalysts", "paper_scan"]
        if execute_paper:
            skip_all_except += ["paper_execute", "paper_reconcile"]
        skip_tasks = [t for t in ["intraday_exit_check", "paper_report"] + ([] if execute_paper else ["paper_execute", "paper_reconcile"])]

        result = run_full_cycle(cfg, skip_tasks=skip_tasks)

        print()
        print(f"  Tasks run:     {result['tasks_run']}")
        print(f"  Tasks skipped: {result['tasks_skipped']}")
        print(f"  Tasks failed:  {result['tasks_failed']}")
        print()
        print("  Task results:")
        print()
        for r in result["results"]:
            icon = "[OK]  " if r["status"] == "SUCCESS" else (
                "[SKIP]" if r["status"] == "SKIPPED" else "[FAIL]"
            )
            dur = f"({r['duration']:.1f}s)" if r["duration"] > 0 else ""
            print(f"    {icon}  {r['task_name']:<25} {dur:<10} {r['detail'][:60]}")

        print()
        if result["tasks_failed"] == 0:
            print("  All tasks completed successfully.")
        else:
            print(f"  WARNING: {result['tasks_failed']} task(s) failed.")
            print("  Check output above for error details.")
        print()

    except RuntimeError as e:
        print(f"  [ERROR] {e}")
    except Exception as e:
        import traceback
        print(f"  [ERROR] {e}")
        traceback.print_exc()


def cmd_automation_status():
    """
    Show the current status of the automated trading system.

    Displays:
      - Whether automation is enabled
      - When each task last ran
      - Latest Alpaca paper account equity (if available)
      - Open positions
      - Latest errors
      - What to do if trading is paused

    Usage:
        python main.py automation-status
    """
    print_banner()
    print("  AUTOMATION STATUS")
    print("=" * 60)
    print()

    try:
        from src.automation_state import get_automation_summary

        state = get_automation_summary()
        enabled = state.get("automation_enabled", True)
        paused = state.get("trading_paused", False)
        critical = state.get("critical_error", False)
        status = state.get("status", "UNKNOWN")

        # Overall status
        print(f"  Overall status:       {status}")
        print(f"  Automation enabled:   {'YES' if enabled else 'NO'}")
        print(f"  Trading paused:       {'YES — ' + state.get('pause_reason', '') if paused else 'NO'}")
        print(f"  Critical error:       {'YES' if critical else 'NO'}")
        if state.get("last_error"):
            print(f"  Last error:           {state['last_error'][:80]}")
        print()

        # Task timestamps
        print("  Last successful run dates:")
        tasks = [
            ("discover_universe", "last_discovery_date"),
            ("pull_news",         "last_news_pull_date"),
            ("score_catalysts",   "last_catalyst_score_date"),
            ("paper_scan",        "last_scan_date"),
            ("paper_execute",     "last_execution_date"),
            ("paper_reconcile",   "last_reconcile_date"),
            ("paper_report",      "last_report_date"),
        ]
        for task_name, field in tasks:
            val = state.get(field, "") or "never"
            print(f"    {task_name:<25} {val}")
        print()

    except Exception as e:
        print(f"  [WARNING] Could not load automation state: {e}")
        print()

    # Alpaca account equity
    try:
        from src.config import load_config, reset_config
        from src.broker_alpaca import AlpacaPaperBroker
        from src.utils import format_currency

        reset_config()
        cfg = load_config()
        cfg.require_paper_mode()

        broker = AlpacaPaperBroker.from_config(cfg)
        account = broker.get_account()

        print("  Alpaca Paper Account:")
        print(f"    Portfolio value:  {format_currency(account['portfolio_value'])}")
        print(f"    Buying power:     {format_currency(account['buying_power'])}")
        print(f"    Cash:             {format_currency(account['cash'])}")
        print()

        positions = broker.get_positions()
        if positions:
            print(f"  Open positions ({len(positions)}):")
            for t, p in positions.items():
                print(
                    f"    {t:<6}  {p.get('qty', '')} shares  "
                    f"current=${p.get('current_price', 0):.2f}  "
                    f"P/L={p.get('unrealized_plpc', 0)*100:+.1f}%"
                )
        else:
            print("  Open positions: none")
        print()

    except Exception as e:
        print(f"  Alpaca connection:  N/A ({e})")
        print()

    # What to do if paused
    try:
        from src.automation_state import load_automation_state
        state = load_automation_state()
        if state.get("trading_paused") or state.get("critical_error"):
            print("  ACTION REQUIRED:")
            print(f"    Reason: {state.get('pause_reason', 'unknown')}")
            print()
            print("  To investigate and re-enable:")
            print("    1. Check the error above")
            print("    2. Fix the underlying problem")
            print("    3. Run:  python main.py setup-check")
            print("    4. Run:  python main.py enable-automation")
            print()
    except Exception:
        pass


def cmd_disable_automation():
    """
    Disable fully automated trading.

    The bot can still run read-only tasks (discovery, news, reporting)
    but will not place any paper orders.

    To re-enable, run: python main.py enable-automation

    Usage:
        python main.py disable-automation
    """
    print_banner()
    print("  DISABLE AUTOMATION")
    print("=" * 60)
    print()

    try:
        from src.automation_state import set_automation_enabled

        set_automation_enabled(False, reason="Manually disabled by user")

        print("  Automation has been DISABLED.")
        print()
        print("  The bot will no longer place paper orders automatically.")
        print("  Discovery, news pulling, and reporting will continue.")
        print()
        print("  To re-enable:")
        print("    python main.py enable-automation")
        print()

    except Exception as e:
        print(f"  [ERROR] {e}")


def cmd_enable_automation():
    """
    Re-enable fully automated trading after it was disabled or paused.

    Runs a safety check before enabling:
      - Verifies ALPACA_PAPER=true
      - Verifies paper mode config flags
      - Confirms paper trading only

    Usage:
        python main.py enable-automation
    """
    print_banner()
    print("  ENABLE AUTOMATION")
    print("=" * 60)
    print()

    try:
        from src.config import load_config, reset_config
        from src.automation_state import set_automation_enabled, mark_paper_mode_verified
        from src.utils import print_check

        reset_config()
        cfg = load_config()

        print("  Running safety checks before enabling automation...")
        print()

        all_ok = True

        # Check paper mode
        paper_ok = getattr(cfg, "paper_only", False)
        print_check("config broker.paper_only", paper_ok, "true" if paper_ok else "MUST be true")
        if not paper_ok:
            all_ok = False

        live_ok = not getattr(cfg, "allow_live_trading", True)
        print_check("config broker.allow_live_trading", live_ok, "false" if live_ok else "MUST be false")
        if not live_ok:
            all_ok = False

        import os
        alpaca_paper_env = os.getenv("ALPACA_PAPER", "").lower()
        env_ok = alpaca_paper_env == "true"
        print_check("ALPACA_PAPER=true in .env", env_ok, alpaca_paper_env or "NOT SET")
        if not env_ok:
            all_ok = False

        # Try Alpaca connection
        alpaca_ok = False
        try:
            from src.broker_alpaca import AlpacaPaperBroker
            broker = AlpacaPaperBroker.from_config(cfg)
            account = broker.get_account()
            alpaca_ok = account.get("status") == "ACTIVE"
            print_check("Alpaca paper account connection", alpaca_ok, f"status={account.get('status', 'unknown')}")
        except Exception as e:
            print_check("Alpaca paper account connection", False, str(e)[:60])
            all_ok = False

        print()

        if not all_ok:
            print("  [FAIL] Safety checks failed. Automation NOT enabled.")
            print()
            print("  Fix the issues above, then run again:")
            print("    python main.py enable-automation")
            return

        set_automation_enabled(True)
        mark_paper_mode_verified(verified=True, alpaca_ok=alpaca_ok)

        print("  All safety checks passed.")
        print()
        print("  Automation has been ENABLED.")
        print("  The bot will trade automatically on the next run-bot cycle.")
        print()
        print("  To start the automated bot:")
        print("    python main.py run-bot")
        print()

    except Exception as e:
        import traceback
        print(f"  [ERROR] {e}")
        traceback.print_exc()


def _load_backtest_universe(cfg):
    """Load the fallback backtest universe from data/universe_sample.csv."""
    import pandas as pd
    sample_path = "data/universe_sample.csv"
    if not os.path.exists(sample_path):
        return None
    try:
        df = pd.read_csv(sample_path)
        return df if not df.empty else None
    except Exception:
        return None


def _print_backtest_report(result, cfg):
    """Print the full backtest results to the terminal."""
    m = result.metrics
    trades = result.trades

    def pct(v):
        sign = "+" if v >= 0 else ""
        return f"{sign}{v * 100:.2f}%"

    def curr(v):
        return f"${v:,.2f}"

    print()
    print("=" * 60)
    print(f"  BACKTEST RESULTS")
    print(f"  Period:  {result.start_date}  to  {result.end_date}")
    print(f"  Universe: {len(result.universe)} tickers  |  "
          f"Starting cash: {curr(result.initial_cash)}")
    print("=" * 60)

    # ---- Performance ----
    print()
    print("  PERFORMANCE SUMMARY")
    print("  " + "-" * 48)
    print(f"  Final portfolio value:  {curr(result.final_value)}")
    print(f"  Total return:           {pct(m['total_return']):<12}  "
          f"SPY: {pct(m['spy_return'])}")
    print(f"  Annualized return:      {pct(m['annualized_return']):<12}  "
          f"SPY: {pct(m['spy_annualized'])}")
    print(f"  Alpha (vs SPY ann.):    {pct(m['alpha'])}")
    print(f"  Max drawdown:           {pct(-m['max_drawdown'])}")
    print(f"  Sharpe ratio:           {m['sharpe_ratio']:.2f}")

    # ---- Trade statistics ----
    print()
    print("  TRADE STATISTICS")
    print("  " + "-" * 48)
    print(f"  Total trades:           {m['total_trades']}")
    if m["total_trades"] > 0:
        print(f"  Win / Loss:             {m['win_count']} / {m['loss_count']}")
        print(f"  Win rate:               {m['win_rate'] * 100:.1f}%")
        print(f"  Average win:            {pct(m['avg_win_pct'])}")
        print(f"  Average loss:           {pct(m['avg_loss_pct'])}")
        pf_str = "∞" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        print(f"  Profit factor:          {pf_str}")
        print(f"  Gross profit:           {curr(m['gross_profit'])}")
        print(f"  Gross loss:             {curr(-m['gross_loss']) if m['gross_loss'] else '$0.00'}")
        print(f"  Total fees paid:        {curr(m['total_fees'])}")
        print(f"  Avg holding period:     {m['avg_holding_days']:.1f} days")

        if m["best_trade_ticker"]:
            print(f"  Best trade:             {m['best_trade_ticker']}  "
                  f"{pct(m['best_trade_pct'])}  ({m['best_trade_days']} days)")
        if m["worst_trade_ticker"]:
            print(f"  Worst trade:            {m['worst_trade_ticker']}  "
                  f"{pct(m['worst_trade_pct'])}  ({m['worst_trade_days']} days)")

    # ---- Exposure ----
    print()
    print("  EXPOSURE")
    print("  " + "-" * 48)
    print(f"  Trading days in period: {m['total_trading_days']}")
    print(f"  Market exposure:        {m['exposure_pct'] * 100:.1f}%"
          "  (approx. fraction of days with open positions)")

    # ---- Exit reasons ----
    if m.get("exit_reasons"):
        print()
        print("  EXIT REASONS")
        print("  " + "-" * 48)
        for reason, count in sorted(m["exit_reasons"].items(),
                                     key=lambda x: -x[1]):
            print(f"  {reason:<30} {count} trade(s)")

    # ---- Trade list ----
    if trades:
        print()
        print(f"  ALL TRADES ({len(trades)})")
        print()
        print(f"  {'Entry':<11} {'Exit':<11} {'Ticker':<6}  "
              f"{'Days':>4}  {'P/L':>10}  Exit reason")
        print("  " + "-" * 62)
        for t in sorted(trades, key=lambda x: x.entry_date):
            sign = "+" if t.pnl_pct >= 0 else ""
            print(f"  {str(t.entry_date):<11} {str(t.exit_date):<11} "
                  f"{t.ticker:<6}  {t.holding_days:>4}  "
                  f"{sign}{t.pnl_pct * 100:>8.2f}%  {t.exit_reason}")
    else:
        print()
        print("  NO TRADES EXECUTED")
        print()
        if not result.skip_catalyst:
            print("  Most likely cause: no dated catalysts in data/catalyst_overrides.csv")
            print("  matched the backtest window.")
            print()
            print("  To test the technical side only (no catalyst required):")
            print("    python main.py backtest "
                  f"--start {result.start_date} --end {result.end_date} --skip-catalyst")

    # ---- Warnings ----
    if result.warnings:
        print()
        print("  WARNINGS")
        for w in result.warnings:
            print(f"  * {w}")

    # ---- Catalyst limitation note ----
    print()
    print("  IMPORTANT: CATALYST LIMITATION")
    print("  " + "-" * 48)
    if result.skip_catalyst:
        print("  This backtest ran with --skip-catalyst.")
        print("  The catalyst filter was DISABLED. Results show how often")
        print("  the 6 technical conditions aligned, but do NOT represent")
        print("  real tradeable setups. Actual results would be lower.")
    else:
        print("  Only catalysts in data/catalyst_overrides.csv with a 'date'")
        print("  field within the backtest window were used.")
        print("  Historical catalyst data is incomplete — interpret results")
        print("  with caution. 0 trades usually means no dated catalysts match.")

    # ---- Saved files ----
    print()
    print("  OUTPUT FILES")
    print("  " + "-" * 48)
    print("  reports/backtest_results.csv         (one row per trade)")
    print("  reports/backtest_equity_curve.csv    (daily portfolio value)")
    print()
    print("=" * 60)
    print()


# ============================================================
# Main entry point — command router
# ============================================================

def main():
    """
    Read the command from the terminal and run the right function.

    sys.argv is the list of words typed in the terminal.
    sys.argv[0] = "main.py"  (always the script name)
    sys.argv[1] = the command, e.g. "setup-check"
    sys.argv[2+] = additional arguments, e.g. "--fallback"
    """
    command = sys.argv[1] if len(sys.argv) > 1 else "help"

    if command == "setup-check":
        cmd_setup_check()

    elif command == "explain":
        cmd_explain()

    elif command == "scan":
        # Stage 2 demo: download data + calculate indicators for one stock
        cmd_scan_stub()

    elif command == "discover-universe":
        cmd_discover_universe()

    elif command == "paper-scan":
        cmd_paper_scan()

    elif command == "paper-execute":
        cmd_paper_execute()

    elif command == "paper-reconcile":
        cmd_paper_reconcile()

    elif command == "paper-report":
        cmd_paper_report()

    elif command == "emergency-close-all":
        cmd_emergency_close_all()

    elif command == "run-once-full-cycle":
        cmd_run_once_full_cycle()

    elif command == "run-bot":
        cmd_run_bot()

    elif command == "backtest":
        cmd_backtest()

    elif command == "pull-news":
        cmd_pull_news()

    elif command == "score-catalysts":
        cmd_score_catalysts()

    elif command == "full-auto-cycle":
        cmd_full_auto_cycle()

    elif command == "automation-status":
        cmd_automation_status()

    elif command == "disable-automation":
        cmd_disable_automation()

    elif command == "enable-automation":
        cmd_enable_automation()

    elif command in ["help", "--help", "-h"]:
        print_banner()
        print("  Available commands:")
        print()
        print("  python main.py setup-check                Check setup and Alpaca connection")
        print("  python main.py explain                    Understand how the algorithm works")
        print("  python main.py scan                       Test indicators (Stage 2 demo)")
        print("  python main.py discover-universe          Discover stocks from the market")
        print("  python main.py discover-universe --fallback  Use universe_sample.csv instead")
        print("  python main.py paper-scan                 Run signal scan (Stage 4)")
        print("  python main.py paper-scan --fallback      Scan with fallback universe")
        print("  python main.py paper-execute              Submit paper orders (Stage 5)")
        print("  python main.py paper-reconcile            Sync state with Alpaca (Stage 5/6)")
        print("  python main.py paper-report               Full portfolio report (Stage 6)")
        print("  python main.py emergency-close-all        Cancel all orders + close all positions")
        print("  python main.py run-once-full-cycle        Run full pipeline once (Stage 7)")
        print("  python main.py run-bot                    Start automated scheduler (Stage 7)")
        print("  python main.py backtest --start YYYY-MM-DD --end YYYY-MM-DD  (Stage 8)")
        print("  python main.py backtest --start 2024-01-01 --end 2024-12-31 --skip-catalyst")
        print("  python main.py backtest --start 2024-01-01 --end 2024-12-31 --tickers NVDA,AMD")
        print()
        print("  --- Stage 9: Full Automation ---")
        print("  python main.py pull-news                  Pull news for discovered universe")
        print("  python main.py score-catalysts            Auto-score catalysts (heuristic)")
        print("  python main.py full-auto-cycle            Full pipeline, no order execution")
        print("  python main.py full-auto-cycle --execute-paper  Full pipeline with execution")
        print("  python main.py automation-status          Show automation health + Alpaca state")
        print("  python main.py disable-automation         Pause all automated trading")
        print("  python main.py enable-automation          Re-enable after safety checks pass")
        print()

    else:
        print_banner()
        print(f"  Unknown command: '{command}'")
        print()
        print("  Run this for help:")
        print("    python main.py --help")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
