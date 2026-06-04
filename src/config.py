"""
src/config.py — Configuration loader

This module does three things:
  1. Reads your .env file (loads your Alpaca API keys into memory)
  2. Reads config.yaml (loads all strategy settings)
  3. Validates that paper trading mode is correctly configured

Every other module imports load_config() from here to get settings.

IMPORTANT:
  The require_paper_mode() method MUST be called before any broker action.
  It refuses to proceed if ALPACA_PAPER is not true, or if API keys are missing.
"""

import os
import yaml
from dotenv import load_dotenv


class AppConfig:
    """
    Holds all configuration for the bot.

    Usage:
        from src.config import load_config
        cfg = load_config()
        print(cfg.max_positions)       # -> 8
        print(cfg.alpaca_paper)        # -> True
        cfg.require_paper_mode()       # Raises if paper mode not valid
    """

    def __init__(self, config_path: str = "config.yaml", env_path: str = ".env"):

        # -------------------------------------------------------
        # Step 1: Load .env file
        # This reads ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_PAPER
        # from the .env file and makes them available via os.getenv().
        # If .env does not exist, load_dotenv silently does nothing.
        # -------------------------------------------------------
        load_dotenv(env_path)

        # -------------------------------------------------------
        # Step 2: Load config.yaml
        # -------------------------------------------------------
        if not os.path.exists(config_path):
            raise FileNotFoundError(
                f"\n[ERROR] config.yaml not found at: {config_path}\n"
                "Make sure you are running commands from the project root folder:\n"
                "  cd ~/Desktop/ai-catalyst-momentum-paper-trader\n"
                "  python main.py setup-check"
            )

        with open(config_path, "r") as f:
            raw = yaml.safe_load(f)

        if raw is None:
            raise ValueError(
                "\n[ERROR] config.yaml is empty. "
                "Please restore it from the project template."
            )

        # -------------------------------------------------------
        # Step 3: Read Alpaca credentials from environment
        # -------------------------------------------------------
        self.alpaca_api_key: str = os.getenv("ALPACA_API_KEY", "").strip()
        self.alpaca_secret_key: str = os.getenv("ALPACA_SECRET_KEY", "").strip()

        # ALPACA_PAPER must be exactly the string "true" (case-insensitive)
        alpaca_paper_raw = os.getenv("ALPACA_PAPER", "false").strip().lower()
        self.alpaca_paper: bool = (alpaca_paper_raw == "true")

        # -------------------------------------------------------
        # Portfolio settings
        # -------------------------------------------------------
        portfolio = raw.get("portfolio", {})
        self.starting_cash: float = portfolio.get("starting_cash", 100_000)
        self.max_positions: int = portfolio.get("max_positions", 8)
        self.target_position_size: float = portfolio.get("target_position_size", 0.18)
        self.max_position_size: float = portfolio.get("max_position_size", 0.25)
        self.transaction_cost_bps: float = portfolio.get("transaction_cost_bps", 5)
        self.slippage_bps: float = portfolio.get("slippage_bps", 10)

        # -------------------------------------------------------
        # Universe quality filters
        # -------------------------------------------------------
        filters = raw.get("filters", {})
        self.min_price: float = filters.get("min_price", 5.0)
        self.min_market_cap: float = filters.get("min_market_cap", 1_000_000_000)
        self.min_avg_dollar_volume: float = filters.get("min_avg_dollar_volume", 50_000_000)
        self.min_avg_share_volume: float = filters.get("min_avg_share_volume", 1_000_000)
        self.min_volume_ratio: float = filters.get("min_volume_ratio", 2.5)
        self.min_20d_return: float = filters.get("min_20d_return", 0.10)
        self.min_relative_strength_20d: float = filters.get("min_relative_strength_20d", 0.08)
        self.min_catalyst_score: int = filters.get("min_catalyst_score", 8)

        # -------------------------------------------------------
        # Technical indicator settings
        # -------------------------------------------------------
        indicators = raw.get("indicators", {})
        self.breakout_lookback: int = indicators.get("breakout_lookback", 20)
        self.trend_ma: int = indicators.get("trend_ma", 50)
        self.exit_ma_fast: int = indicators.get("exit_ma_fast", 10)
        self.exit_ma_slow: int = indicators.get("exit_ma_slow", 20)
        self.relative_strength_lookback: int = indicators.get("relative_strength_lookback", 20)

        # -------------------------------------------------------
        # Exit and stop-loss settings
        # -------------------------------------------------------
        exits = raw.get("exits", {})
        self.stop_loss_pct: float = exits.get("stop_loss_pct", 0.08)
        self.breakeven_trigger_pct: float = exits.get("breakeven_trigger_pct", 0.20)
        self.partial_take_profit_trigger_pct: float = exits.get("partial_take_profit_trigger_pct", 0.40)
        self.big_winner_trigger_pct: float = exits.get("big_winner_trigger_pct", 0.75)
        self.max_holding_days: int = exits.get("max_holding_days", 20)
        self.stall_days: int = exits.get("stall_days", 5)
        self.rs_weakness_days: int = exits.get("rs_weakness_days", 5)

        # -------------------------------------------------------
        # Broker settings
        # -------------------------------------------------------
        broker = raw.get("broker", {})
        self.broker_provider: str = broker.get("provider", "alpaca")
        self.paper_only: bool = broker.get("paper_only", True)
        self.allow_live_trading: bool = broker.get("allow_live_trading", False)
        self.allow_shorting: bool = broker.get("allow_shorting", False)
        self.allow_margin: bool = broker.get("allow_margin", False)
        self.allow_options_trading: bool = broker.get("allow_options_trading", False)
        self.use_market_orders: bool = broker.get("use_market_orders", True)
        self.max_orders_per_day: int = broker.get("max_orders_per_day", 10)

        # -------------------------------------------------------
        # Order execution settings
        # -------------------------------------------------------
        execution = raw.get("execution", {})
        self.buy_timing: str = execution.get("buy_timing", "next_market_open")
        self.sell_timing: str = execution.get("sell_timing", "next_market_open")
        self.duplicate_order_protection: bool = execution.get("duplicate_order_protection", True)
        self.cancel_stale_open_orders: bool = execution.get("cancel_stale_open_orders", True)
        self.stale_order_minutes: int = execution.get("stale_order_minutes", 60)
        self.order_log_path: str = execution.get("order_log_path", "reports/orders_log.csv")
        self.signal_file_path: str = execution.get("signal_file_path", "reports/paper_signals.csv")
        self.state_file_path: str = execution.get("state_file_path", "reports/paper_state.json")

        # -------------------------------------------------------
        # Universe discovery settings
        # -------------------------------------------------------
        ud = raw.get("universe_discovery", {})
        self.universe_discovery_enabled: bool = ud.get("enabled", True)
        self.use_fixed_universe_fallback: bool = ud.get("use_fixed_universe_fallback", True)
        self.max_discovered_symbols: int = ud.get("max_discovered_symbols", 300)
        self.blocked_symbols_path: str = ud.get("blocked_symbols_path", "data/blocked_symbols.csv")
        self.discovered_universe_path: str = ud.get(
            "discovered_universe_path", "reports/daily_discovered_universe.csv"
        )

        # Discovery sources (which data sources to use)
        ds = raw.get("discovery_sources", {})
        self.discovery_alpaca_volume: bool = ds.get("alpaca_most_active_by_volume", True)
        self.discovery_alpaca_trades: bool = ds.get("alpaca_most_active_by_trades", True)
        self.discovery_top_gainers: bool = ds.get("top_gainers", True)
        self.discovery_yfinance_volume: bool = ds.get("yfinance_volume_spike_scan", True)
        self.discovery_yfinance_breakout: bool = ds.get("yfinance_breakout_scan", True)
        self.discovery_news: bool = ds.get("news_discovery", False)
        self.discovery_polygon: bool = ds.get("polygon_provider_enabled", False)

        # -------------------------------------------------------
        # Catalyst settings
        # -------------------------------------------------------
        cat = raw.get("catalysts", {})
        self.require_catalyst_for_trade: bool = cat.get("require_catalyst_for_trade", True)
        self.manual_catalyst_path: str = cat.get("manual_catalyst_path", "data/catalyst_overrides.csv")
        self.needs_review_path: str = cat.get("needs_review_path", "reports/needs_catalyst_review.csv")
        self.allow_price_only_trades: bool = cat.get("allow_price_only_trades", False)

        # -------------------------------------------------------
        # Scheduler settings
        # -------------------------------------------------------
        sched = raw.get("scheduler", {})
        self.scheduler_enabled: bool = sched.get("enabled", True)
        self.scheduler_timezone: str = sched.get("timezone", "America/New_York")
        self.scan_time: str = sched.get("scan_time", "16:15")
        self.execute_time: str = sched.get("execute_time", "09:32")
        self.reconcile_time: str = sched.get("reconcile_time", "09:45")
        self.intraday_exit_check_time: str = sched.get("intraday_exit_check_time", "15:45")
        self.report_time: str = sched.get("report_time", "16:10")
        self.prevent_duplicate_daily_runs: bool = sched.get("prevent_duplicate_daily_runs", True)
        self.scheduler_log_path: str = sched.get("scheduler_log_path", "reports/scheduler_log.csv")

        # -------------------------------------------------------
        # Options overlay settings (recommendations only, no trading)
        # -------------------------------------------------------
        opts = raw.get("options_overlay", {})
        self.options_overlay_enabled: bool = opts.get("enabled", True)
        self.options_max_recommendations: int = opts.get("max_recommendations", 3)
        self.options_target_dte: int = opts.get("target_dte", 75)
        self.options_long_call_itm_pct: float = opts.get("long_call_itm_pct", 0.07)
        self.options_short_call_otm_pct: float = opts.get("short_call_otm_pct", 0.20)

        # -------------------------------------------------------
        # Alpha filters
        # -------------------------------------------------------
        af = raw.get("alpha_filters", {})
        self.earnings_avoidance_days: int = af.get("earnings_avoidance_days", 5)
        self.sector_max_positions: int = af.get("sector_max_positions", 2)
        self.max_extension_pct: float = af.get("max_extension_pct", 0.30)
        self.gap_up_max_pct: float = af.get("gap_up_max_pct", 0.05)
        self.atr_stop_multiplier: float = af.get("atr_stop_multiplier", 1.5)
        self.atr_max_stop_pct: float = af.get("atr_max_stop_pct", 0.12)
        self.max_pct_below_52w_high: float = af.get("max_pct_below_52w_high", 0.15)
        self.require_multi_day_volume: bool = af.get("require_multi_day_volume", True)
        self.max_daily_entries: int = af.get("max_daily_entries", 3)
        self.min_adx: int = af.get("min_adx", 25)
        self.max_pre_breakout_range_pct: float = af.get("max_pre_breakout_range_pct", 0.08)
        self.require_sector_etf_uptrend: bool = af.get("require_sector_etf_uptrend", True)

        # -------------------------------------------------------
        # Stage 9: News discovery settings
        # -------------------------------------------------------
        news = raw.get("news", {})
        self.news_enabled: bool = news.get("enabled", True)
        self.news_primary_provider: str = news.get("primary_provider", "alpaca")
        self.news_fallback_provider: str = news.get("fallback_provider", "yahoo")
        self.news_days_back: int = news.get("days_back", 7)
        self.news_max_headlines_per_symbol: int = news.get("max_headlines_per_symbol", 10)
        self.news_cache_path: str = news.get("news_cache_path", "reports/news_cache.csv")
        self.news_max_news_age_days: int = news.get("max_news_age_days", 7)

        # -------------------------------------------------------
        # Stage 9: Catalyst scoring settings
        # -------------------------------------------------------
        cs = raw.get("catalyst_scoring", {})
        self.catalyst_scoring_mode: str = cs.get("mode", "heuristic")
        self.use_llm_if_available: bool = cs.get("use_llm_if_available", False)
        self.auto_catalyst_scores_path: str = cs.get(
            "auto_catalyst_scores_path", "reports/automated_catalyst_scores.csv"
        )

        # -------------------------------------------------------
        # Stage 9: Automation control settings
        # -------------------------------------------------------
        auto = raw.get("automation", {})
        self.fully_automated: bool = auto.get("fully_automated", True)
        self.require_human_review_before_orders: bool = auto.get(
            "require_human_review_before_orders", False
        )
        self.pause_on_low_confidence_catalysts: bool = auto.get(
            "pause_on_low_confidence_catalysts", True
        )
        self.pause_on_news_provider_failure: bool = auto.get(
            "pause_on_news_provider_failure", False
        )
        self.max_automated_trades_per_day: int = auto.get("max_automated_trades_per_day", 5)
        self.max_symbols_to_score_per_day: int = auto.get("max_symbols_to_score_per_day", 300)
        self.max_drawdown_pct: float = auto.get("max_drawdown_pct", 0.20)
        self.automation_state_path: str = auto.get(
            "automation_state_path", "reports/automation_state.json"
        )
        self.alerts_log_path: str = auto.get("alerts_log_path", "reports/alerts_log.csv")
        self.order_approval_path: str = auto.get(
            "order_approval_path", "reports/order_approval.csv"
        )

    # -----------------------------------------------------------
    # Paper mode safety validation
    # -----------------------------------------------------------

    def require_paper_mode(self) -> None:
        """
        Call this before ANY broker action.

        Checks every safety condition and raises a clear RuntimeError
        if anything is not correctly configured for paper trading.

        This is the primary safety gate that prevents accidental live trading.
        """
        errors = []

        if not self.alpaca_paper:
            errors.append(
                "ALPACA_PAPER is not set to 'true' in your .env file.\n"
                "     Fix: Open your .env file and add the line:\n"
                "          ALPACA_PAPER=true"
            )

        placeholder_key = "your_paper_api_key_here"
        placeholder_secret = "your_paper_secret_key_here"

        if not self.alpaca_api_key or self.alpaca_api_key == placeholder_key:
            errors.append(
                "ALPACA_API_KEY is missing or still shows the placeholder value.\n"
                "     Fix: Open your .env file and set ALPACA_API_KEY to your real paper API key."
            )

        if not self.alpaca_secret_key or self.alpaca_secret_key == placeholder_secret:
            errors.append(
                "ALPACA_SECRET_KEY is missing or still shows the placeholder value.\n"
                "     Fix: Open your .env file and set ALPACA_SECRET_KEY to your real paper secret key."
            )

        if self.allow_live_trading:
            errors.append(
                "allow_live_trading is set to true in config.yaml.\n"
                "     Fix: This must always be false. Open config.yaml and set:\n"
                "          allow_live_trading: false"
            )

        if not self.paper_only:
            errors.append(
                "paper_only is set to false in config.yaml.\n"
                "     Fix: Open config.yaml and set:\n"
                "          paper_only: true"
            )

        if errors:
            msg = "\n\n  PAPER MODE SAFETY CHECK FAILED\n"
            msg += "  " + "=" * 56 + "\n"
            for i, e in enumerate(errors, 1):
                msg += f"\n  Problem {i}: {e}\n"
            msg += "\n  The bot refused to run until these are fixed.\n"
            raise RuntimeError(msg)

    def is_paper_mode_valid(self) -> bool:
        """Returns True if paper mode is correctly configured, False otherwise."""
        try:
            self.require_paper_mode()
            return True
        except RuntimeError:
            return False

    def get_env_status(self) -> dict:
        """
        Return a dict describing the status of all environment variables.
        Used by setup-check to print a friendly report.
        """
        placeholder_key = "your_paper_api_key_here"
        placeholder_secret = "your_paper_secret_key_here"

        return {
            "env_file_exists": os.path.exists(".env"),
            "api_key_set": bool(self.alpaca_api_key and self.alpaca_api_key != placeholder_key),
            "secret_key_set": bool(self.alpaca_secret_key and self.alpaca_secret_key != placeholder_secret),
            "paper_mode_true": self.alpaca_paper,
            "live_trading_disabled": not self.allow_live_trading,
            "paper_only_true": self.paper_only,
        }


# -------------------------------------------------------
# Module-level singleton — loaded once, reused everywhere
# -------------------------------------------------------
_config_instance: AppConfig | None = None


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> AppConfig:
    """
    Load and return the application configuration.

    First call: reads config.yaml and .env from disk.
    Subsequent calls: returns the already-loaded instance (faster).

    Args:
        config_path: Path to config.yaml (default: "config.yaml")
        env_path:    Path to .env file   (default: ".env")

    Returns:
        AppConfig object with all settings loaded.
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = AppConfig(config_path=config_path, env_path=env_path)
    return _config_instance


def reset_config() -> None:
    """
    Clear the cached config instance.
    Call this in tests so each test starts fresh.
    """
    global _config_instance
    _config_instance = None
