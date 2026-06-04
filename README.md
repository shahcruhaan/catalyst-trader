# AI Catalyst Momentum Breakout Paper Trader

**IMPORTANT: This project is for paper trading, education, backtesting, and strategy research only.**
**It does not and cannot place live trades. It uses only Alpaca Paper Trading.**
**Nothing in this project is financial advice.**

---

## Table of Contents

1. [What This Project Does](#1-what-this-project-does)
2. [What Paper Trading Is](#2-what-paper-trading-is)
3. [What Alpaca Is](#3-what-alpaca-is)
4. [What an API Key Is](#4-what-an-api-key-is)
5. [Why the .env File Matters](#5-why-the-env-file-matters)
6. [How to Install Python](#6-how-to-install-python)
7. [How to Install VS Code](#7-how-to-install-vs-code)
8. [How to Open Terminal](#8-how-to-open-terminal)
9. [How to Create the Project Folder](#9-how-to-create-the-project-folder)
10. [How to Install Dependencies](#10-how-to-install-dependencies)
11. [How to Create the .env File](#11-how-to-create-the-env-file)
12. [How to Run setup-check](#12-how-to-run-setup-check)
13. [How Automatic Stock Discovery Works](#13-how-automatic-stock-discovery-works)
14. [Why the Bot Does Not Need a Fixed Watchlist](#14-why-the-bot-does-not-need-a-fixed-watchlist)
15. [Why We Still Use Filters to Avoid Bad Stocks](#15-why-we-still-use-filters-to-avoid-bad-stocks)
16. [What blocked_symbols.csv Does](#16-what-blocked_symbolscsv-does)
17. [What catalyst_overrides.csv Does](#17-what-catalyst_overridescsv-does)
18. [What Happens If a Stock Has No Catalyst Score](#18-what-happens-if-a-stock-has-no-catalyst-score)
19. [What needs_catalyst_review.csv Means](#19-what-needs_catalyst_reviewcsv-means)
20. [How to Run discover-universe](#20-how-to-run-discover-universe)
21. [How to Run paper-scan](#21-how-to-run-paper-scan)
22. [How to Run paper-execute](#22-how-to-run-paper-execute)
23. [How to Check the Alpaca Dashboard](#23-how-to-check-the-alpaca-dashboard)
24. [How to Read Reports](#24-how-to-read-reports)
25. [How Automatic Scheduling Works](#25-how-automatic-scheduling-works)
26. [Manual Mode vs Automatic Mode](#26-manual-mode-vs-automatic-mode)
27. [How to Test run-once-full-cycle Safely](#27-how-to-test-run-once-full-cycle-safely)
28. [How to Start run-bot](#28-how-to-start-run-bot)
29. [How to Stop run-bot](#29-how-to-stop-run-bot)
30. [How to Check If the Bot Is Running](#30-how-to-check-if-the-bot-is-running)
31. [How to Disable Automation](#31-how-to-disable-automation)
32. [How to Emergency Close All Positions](#32-how-to-emergency-close-all-positions)
33. [How to Reset the Paper Account](#33-how-to-reset-the-paper-account)
34. [Common Errors and Fixes](#34-common-errors-and-fixes)
35. [Project File Reference](#35-project-file-reference)
36. [The Strategy Explained Simply](#36-the-strategy-explained-simply)
37. [All Commands Reference](#37-all-commands-reference)
38. [Automated News and Catalyst Scoring](#38-automated-news-and-catalyst-scoring)
39. [LLM Catalyst Scoring Setup](#39-llm-catalyst-scoring-setup)
40. [Alpha Quality Filters](#40-alpha-quality-filters)
41. [Server Deployment on Oracle Cloud](#41-server-deployment-on-oracle-cloud)

---

## 1. What This Project Does

This project is an automated paper trading bot that:

- Searches the entire U.S. stock market every day for stocks showing powerful momentum
- Filters out low-quality, illiquid, and risky stocks automatically
- Requires a real catalyst (a news event or business development) before considering a trade
- Automatically buys and sells stocks using fake paper money on Alpaca Paper Trading
- Runs on a schedule without you needing to watch it
- Generates detailed reports so you can learn what it is doing and why
- Lets you backtest the strategy on historical data

Think of it as a robot investor that runs experiments with fake money so you can study how momentum trading actually works — without ever risking real money.

---

## 2. What Paper Trading Is

Paper trading means trading with fake money in a simulated account.

When you paper trade:
- You see real market prices
- You place real-looking orders
- But no real money ever moves
- It is completely risk-free
- You can reset the account and start over at any time

Paper trading is how professional traders test their strategies before using real money. It is also used for education and research. This entire project uses paper trading only.

**This project will never place a real trade. It uses only Alpaca Paper Trading.**

---

## 3. What Alpaca Is

Alpaca (alpaca.markets) is a commission-free stock broker that offers:
- Free paper trading accounts with $100,000 in simulated money
- An API (a way for code to connect to the broker automatically)
- Real-time market data
- A dashboard to view your paper positions and orders

You do not need to deposit any real money. The paper trading account is completely free.

To create an account:
1. Go to https://alpaca.markets
2. Click "Sign Up" and create a free account
3. After logging in, make sure the toggle in the top left says "Paper Trading"
4. Click "API Keys" to find your paper trading keys

---

## 4. What an API Key Is

An API key is like a password that lets a program (our bot) connect to a service (Alpaca) on your behalf.

Alpaca gives you two keys:
- **API Key**: A public identifier (like a username)
- **Secret Key**: A private password (like a password — never share this)

Together, these two keys let our bot log into your Alpaca paper account and place paper trades automatically.

**Never share your secret key with anyone.**
**Never paste your real keys into chat messages or emails.**

---

## 5. Why the .env File Matters

A `.env` file is a special text file that stores secret information — like your API keys — separately from your code.

Why is this important?
- If you uploaded your code to GitHub with keys inside the code files, anyone could steal your keys
- The `.env` file is listed in `.gitignore`, which means git will never upload it anywhere
- The bot reads the keys from `.env` automatically at runtime
- You keep the `.env` file only on your own computer

The `.env.example` file in this project shows you the format but contains fake placeholder values. You must create your own `.env` file with real values.

---

## 6. How to Install Python

Python is the programming language this project uses.

**On Mac:**

1. Open Terminal (see Section 8)
2. Check if Python is already installed:
   ```
   python3 --version
   ```
3. If you see something like `Python 3.11.x`, you are ready. Skip to Section 7.
4. If not, install it using Homebrew:
   ```
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
   Then:
   ```
   brew install python@3.11
   ```

**On Windows:**

1. Go to https://www.python.org/downloads/
2. Click "Download Python 3.11.x" (use the latest 3.11 version)
3. Run the installer
4. **IMPORTANT**: Check the box that says "Add Python to PATH" before clicking Install
5. Click Install Now
6. Open Command Prompt (search for "cmd" in the Start Menu)
7. Verify:
   ```
   python --version
   ```
   You should see `Python 3.11.x`

---

## 7. How to Install VS Code

VS Code (Visual Studio Code) is a free code editor. You do not need it to run the bot, but it makes editing files much easier.

1. Go to https://code.visualstudio.com/
2. Download the version for your operating system (Mac or Windows)
3. Run the installer
4. Open VS Code
5. To open the project folder in VS Code: File → Open Folder → select the `ai-catalyst-momentum-paper-trader` folder

---

## 8. How to Open Terminal

Terminal is the application where you type commands.

**On Mac:**
- Press `Command + Space` to open Spotlight
- Type "Terminal" and press Enter
- A black or white window will open where you can type commands

**On Windows:**
- Press the Windows key
- Type "Command Prompt" or "PowerShell" and press Enter
- A black window will open

In this README, every command you see in a code block is meant to be typed in Terminal (Mac) or Command Prompt (Windows).

---

## 9. How to Create the Project Folder

The project folder should already exist at:
```
/Users/YOUR_USERNAME/Desktop/ai-catalyst-momentum-paper-trader
```

If it does not exist yet, create it:

**Mac:**
```
mkdir ~/Desktop/ai-catalyst-momentum-paper-trader
```

**Windows:**
```
mkdir %USERPROFILE%\Desktop\ai-catalyst-momentum-paper-trader
```

Then navigate into it:

**Mac:**
```
cd ~/Desktop/ai-catalyst-momentum-paper-trader
```

**Windows:**
```
cd %USERPROFILE%\Desktop\ai-catalyst-momentum-paper-trader
```

All commands in this README assume you are inside this folder.

---

## 10. How to Install Dependencies

Dependencies are the Python packages (libraries) our bot needs to run.

They are all listed in `requirements.txt`.

**Step 1: Create a virtual environment** (an isolated Python workspace for this project)

Mac:
```
python3 -m venv venv
```

Windows:
```
python -m venv venv
```

What this does: Creates a folder called `venv/` inside your project. This folder contains a private copy of Python just for this project, so installing packages here won't affect anything else on your computer.

**Step 2: Activate the virtual environment**

Mac:
```
source venv/bin/activate
```

Windows:
```
venv\Scripts\activate
```

After activation, your terminal prompt will show `(venv)` at the start of each line. This means the virtual environment is active.

**You must activate the virtual environment every time you open a new Terminal window before running the bot.**

**Step 3: Install all packages**
```
pip install -r requirements.txt
```

This reads `requirements.txt` and installs every package listed. It may take 1–3 minutes.

**What you should see:**
A long list of package names being downloaded and installed. At the end you should see no red error messages.

---

## 11. How to Create the .env File

**Step 1: Make sure you have your Alpaca paper trading API keys** (see Section 3 and Section 4)

**Step 2: In Terminal (Mac) or Command Prompt (Windows), navigate to the project folder**

**Step 3: Create the .env file**

Mac (using VS Code):
```
code .env
```

Or create it manually by copying the example:
Mac:
```
cp .env.example .env
```

Windows:
```
copy .env.example .env
```

**Step 4: Open the .env file in any text editor and replace the placeholder values with your real keys**

The file should look like this:
```
ALPACA_API_KEY=PKXXXXXXXXXXXXXXXXXXXXXX
ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALPACA_PAPER=true
```

Replace `PKXXXXXXXXXXXXXXXXXXXXXX` with your actual Alpaca Paper API key.
Replace the secret key similarly.
Leave `ALPACA_PAPER=true` exactly as it is.

**Step 5: Save the file.**

Do not add quotes around the values. Do not add spaces around the `=` sign.

---

## 12. How to Run setup-check

Once you have installed dependencies and created your `.env` file, run the setup check:

```
python main.py setup-check
```

This command:
- Checks that Python is the right version
- Checks that all required packages are installed
- Checks that `.env` exists and has the right keys
- Verifies that `ALPACA_PAPER=true`
- Connects to your Alpaca paper account
- Prints a status report

**What you should see:**
```
============================================================
AI Catalyst Momentum Breakout Paper Trader
SETUP CHECK
============================================================
[OK] Python version: 3.11.x
[OK] All required packages installed
[OK] .env file found
[OK] ALPACA_API_KEY is set
[OK] ALPACA_SECRET_KEY is set
[OK] ALPACA_PAPER = true
[OK] Connected to Alpaca Paper Trading
[OK] Account status: ACTIVE
[OK] Paper buying power: $100,000.00
============================================================
PAPER TRADING MODE ONLY
Setup check passed. You are ready to proceed.
============================================================
```

If any line shows `[FAIL]` instead of `[OK]`, see Section 34 for common errors and fixes.

---

## 13. How Automatic Stock Discovery Works

Instead of using a fixed list of stocks, the bot discovers new candidates from the market every single day.

Here is what it does:

**Step 1: Pull from multiple sources**
- Alpaca's "most active stocks by volume" list
- Alpaca's "most active stocks by trade count" list
- Top gaining stocks of the day
- Stocks with unusual volume spikes (via yfinance)
- Stocks making 20-day price highs (via yfinance)

**Step 2: For each discovered stock, download recent data**
- Last 60 days of price and volume
- Current market cap
- Average daily dollar volume

**Step 3: Apply quality filters** (see Section 15)

**Step 4: Calculate momentum indicators**
- 20-day return (how much the stock went up in 20 days)
- Volume ratio (today's volume vs 20-day average)
- Relative strength vs SPY (did the stock beat the market?)

**Step 5: Save results**
All discovered and filtered stocks are saved to: `reports/daily_discovered_universe.csv`

**Step 6: Pass to the signal engine**
Only stocks from the discovered universe are allowed to be traded.

---

## 14. Why the Bot Does Not Need a Fixed Watchlist

Traditional trading bots use a fixed watchlist — a list of 20–50 stocks you manually pick in advance. The problem is you might miss the biggest opportunity of the week because it was not on your list.

This bot uses market-wide discovery instead. Every day it scans hundreds of stocks automatically and finds the ones showing the strongest signals. You don't need to guess in advance which stocks will move.

The `data/universe_sample.csv` file still exists as a fallback for testing and debugging, but it is not used during normal automatic operation.

---

## 15. Why We Still Use Filters to Avoid Bad Stocks

Just because a stock is "active" or "gaining" does not mean it is safe to trade. Many active stocks are:
- Too cheap (under $5) — highly manipulatable
- Too small (under $1B market cap) — hard to trade fairly
- Not liquid enough — you might not be able to sell quickly
- Leveraged ETFs — designed to decay over time, not hold
- Warrants or preferreds — completely different instruments

The bot applies these filters automatically to protect you from low-quality setups. See `config.yaml` for the exact filter thresholds, which you can adjust.

---

## 16. What blocked_symbols.csv Does

`data/blocked_symbols.csv` is a list of tickers the bot is permanently forbidden from trading.

This is useful for:
- Blocking leveraged and inverse ETFs (like TQQQ, SQQQ)
- Blocking volatility products (like UVXY)
- Blocking any specific stock you personally want to avoid

How to add a blocked symbol:
1. Open `data/blocked_symbols.csv` in any text editor or VS Code
2. Add a new line in the format: `TICKER,reason`
3. Save the file
4. The bot will read the updated list on the next run

---

## 17. What catalyst_overrides.csv Does

`data/catalyst_overrides.csv` is where you manually enter catalyst data.

A "catalyst" is a specific business event that explains why a stock is moving. Examples:
- Earnings beat with raised guidance
- FDA drug approval
- Large government contract win
- Major partnership announcement
- New product launch with strong demand

The bot requires a catalyst score of 8 or higher (out of 10) before it will trade any stock. This prevents it from trading stocks that are moving for unknown or speculative reasons.

**How to add a catalyst:**
1. Open `data/catalyst_overrides.csv`
2. Add a row with: `date,ticker,catalyst_type,headline,summary,catalyst_score,tradeable,confidence`
3. Use today's date in format: `2026-06-02`
4. Set `catalyst_score` between 1–10 (10 is the strongest)
5. Set `tradeable` to `true` or `false`
6. Set `confidence` to `low`, `medium`, or `high`

Example:
```
2026-06-02,NVDA,earnings,Nvidia beats and raises guidance,Strong AI chip demand,9,true,high
```

---

## 18. What Happens If a Stock Has No Catalyst Score

The bot automatically fetches news from Alpaca (free with your Alpaca account) and scores it using keyword heuristics. No manual data entry is required.

If the automatic scoring gives a stock a score below 8, or if no relevant news is found, the bot will NOT trade it. It saves those stocks to the review file (see Section 19) so you can investigate manually if you want.

You can still add entries to `data/catalyst_overrides.csv` to manually override any score. Manual overrides always take priority over automated scores.

See Section 38 for more details on how automated catalyst scoring works.

---

## 19. What needs_catalyst_review.csv Means

`reports/needs_catalyst_review.csv` is a file generated every time you run `paper-scan`.

It contains stocks that:
- Passed all price/volume/momentum filters
- BUT had no catalyst data, or catalyst score below 8

This file is useful because it shows you stocks that might be worth researching manually. If you research one and find a strong catalyst, you can add it to `catalyst_overrides.csv` and it will be picked up on the next scan.

Think of it as a "watch list for follow-up research."

---

## 20. How to Run discover-universe

```
python main.py discover-universe
```

This command:
- Automatically scans the market for candidate stocks
- Applies all quality filters
- Saves results to `reports/daily_discovered_universe.csv`
- Does NOT place any trades

Run this command after market close (4:00 PM Eastern) for best results, as the data represents the full trading day.

**What you should see:**
```
PAPER TRADING MODE ONLY
Starting automatic universe discovery...
Source: Alpaca most active by volume — found 50 candidates
Source: yfinance volume spike scan — found 78 candidates
Applying quality filters...
  Rejected 23: price below $5
  Rejected 14: market cap below $1B
  Rejected 8: average dollar volume below $50M
  Rejected 5: blocked symbol list
Final universe: 142 stocks passed all filters
Saved to: reports/daily_discovered_universe.csv
```

---

## 21. How to Run paper-scan

```
python main.py paper-scan
```

This command:
- Uses today's discovered universe (runs discover-universe first if needed)
- Downloads recent price and volume data for each stock
- Calculates all technical indicators
- Reads catalyst_overrides.csv
- Generates buy signals for qualifying stocks
- Generates sell signals for stocks you already hold
- Saves buy/sell signals to `reports/paper_signals.csv`
- Saves no-catalyst stocks to `reports/needs_catalyst_review.csv`
- Does NOT place any trades

Run this after market close before running paper-execute.

---

## 22. How to Run paper-execute

```
python main.py paper-execute
```

This command:
- Connects to Alpaca Paper Trading
- Reads the signals from `reports/paper_signals.csv`
- Checks your existing paper positions
- Checks open orders
- Checks buying power
- Submits paper buy orders for qualifying stocks
- Submits paper sell orders for exit signals
- Logs every action to `reports/orders_log.csv`

**This places real orders on Alpaca Paper Trading. No real money is ever involved.**

Run this shortly after market open (9:31 AM or later).

Before running this, always run `paper-scan` first.

---

## 23. How to Check the Alpaca Dashboard

1. Go to https://app.alpaca.markets
2. Log in to your Alpaca account
3. In the top-left corner, make sure the toggle shows "Paper Trading"
4. Click "Portfolio" to see your current paper positions
5. Click "Orders" to see pending and filled orders
6. Click "Account" to see your buying power and equity

If you see orders placed by the bot, they will show up here with the stock symbol, quantity, and status (open, filled, cancelled).

---

## 24. How to Read Reports

All reports are saved in the `reports/` folder.

| File | What it Contains |
|------|-----------------|
| `daily_discovered_universe.csv` | All stocks discovered today with their filter results |
| `paper_signals.csv` | Today's buy and sell signals |
| `needs_catalyst_review.csv` | Stocks needing catalyst research |
| `orders_log.csv` | Every order the bot submitted with timestamps |
| `paper_state.json` | Current state: positions, equity, open orders |
| `performance_report.csv` | Running P&L and performance metrics |
| `backtest_results.csv` | Historical backtest results |
| `options_overlay_recommendations.csv` | Educational options call spread recommendations |
| `scheduler_log.csv` | Log of every scheduled task the bot ran |
| `news_cache.csv` | Raw news headlines pulled from Alpaca/Yahoo |
| `automated_catalyst_scores.csv` | Scores computed by the auto-scoring engine |
| `alerts_log.csv` | All alerts sent by the bot (trades, errors, pauses) |
| `automation_state.json` | Automation on/off state and drawdown tracking |
| `tomorrow_orders.csv` | Proposed orders for review (if human review is enabled) |

You can open any `.csv` file in Excel, Google Sheets, or VS Code.

---

## 25. How Automatic Scheduling Works

When you run `python main.py run-bot`, the bot runs continuously and executes tasks automatically based on the U.S. market schedule.

The daily schedule (all times U.S. Eastern):

| Time | Task |
|------|------|
| 4:10 PM | Generate daily report |
| 4:15 PM | Pull news for discovered stocks |
| 4:15 PM | Score catalysts automatically |
| 4:15 PM | Scan: discover universe and generate signals |
| 9:32 AM | Execute: place paper buy/sell orders |
| 9:45 AM | Reconcile: sync local records with Alpaca |
| 3:45 PM | Intraday exit check |

The scheduler:
- Only runs on U.S. market trading days (skips weekends and holidays)
- Never runs the same task twice on the same day
- Logs every action to `reports/scheduler_log.csv`
- Prints status updates to the terminal

---

## 26. Manual Mode vs Automatic Mode

**Manual Mode** — You run each command yourself:
```
python main.py discover-universe
python main.py pull-news
python main.py score-catalysts
python main.py paper-scan
python main.py paper-execute
python main.py paper-reconcile
python main.py paper-report
```

Use this when:
- Learning how the system works
- Debugging a problem
- Testing a new configuration
- Running a specific step in isolation

**Automatic Mode** — The bot runs everything itself:
```
python main.py run-bot
```

Use this when:
- You want the bot to trade on its own schedule
- You want to monitor it passively
- You have tested it in manual mode first and trust it

**Always test in manual mode before enabling automatic mode.**

---

## 27. How to Test run-once-full-cycle Safely

Before running the full automatic bot, test the complete pipeline in one shot without actually placing orders:

```
python main.py run-once-full-cycle
```

This runs:
1. discover-universe
2. paper-scan
3. Generates all reports

But it does NOT execute trades unless you add the `--execute-paper` flag.

Run this command a few times until you are satisfied the pipeline works correctly.
Then, when you are ready:
```
python main.py run-once-full-cycle --execute-paper
```

---

## 28. How to Start run-bot

Make sure you have:
1. Your `.env` file set up with paper API keys
2. Run `setup-check` and confirmed it passes
3. Run `run-once-full-cycle` at least once and confirmed it works
4. Added at least one row to `catalyst_overrides.csv`

Then start the bot:
```
python main.py run-bot
```

You will see:
```
============================================================
AUTOMATED PAPER TRADING BOT RUNNING — PAPER MODE ONLY
============================================================
Next task: scan at 16:15 Eastern
Press Ctrl+C to stop the bot safely.
```

Leave this Terminal window open. The bot runs as long as the window is open.

---

## 29. How to Stop run-bot

Press `Ctrl + C` in the Terminal window where `run-bot` is running.

You will see:
```
Bot stopping... shutting down scheduler.
Goodbye.
```

The bot will stop cleanly. No orders will be cancelled — existing paper positions stay open until you close them or the exit rules trigger.

---

## 30. How to Check If the Bot Is Running

Look at the Terminal window where you ran `python main.py run-bot`.

If it is running, you will see periodic status messages like:
```
[10:32:15] Scheduler heartbeat — next task: scan at 16:15
[10:32:15] Market status: OPEN
```

You can also check `reports/scheduler_log.csv` — if the bot is running, it will be writing new rows to that file regularly.

---

## 31. How to Disable Automation

To disable automatic scheduling without stopping the program entirely, set this in `config.yaml`:

```yaml
scheduler:
  enabled: false
```

Then restart the bot. It will run but not execute any scheduled tasks.

Alternatively, simply press Ctrl+C to stop it completely.

---

## 32. How to Emergency Close All Positions

If something goes wrong and you need to immediately close all paper positions:

```
python main.py emergency-close-all
```

This will:
1. Connect to Alpaca Paper Trading
2. Cancel all open paper orders
3. Close all open paper positions (sell everything at market)
4. Log every action

**This only affects your paper trading account. No real money is involved.**

After running this, verify in the Alpaca dashboard that all positions show as closed and no orders are pending.

---

## 33. How to Reset the Paper Account

If you want to start over with a fresh $100,000 paper account:

1. Go to https://app.alpaca.markets
2. Make sure you are on the Paper Trading account
3. Click your account name or profile icon
4. Look for "Reset Paper Account" or go to Account Settings
5. Confirm the reset

This will close all positions and restore your balance to $100,000.

After resetting, delete `reports/paper_state.json` so the bot's local records match:
```
rm reports/paper_state.json
```

---

## 34. Common Errors and Fixes

### Error: `python: command not found`
**Mac:** Use `python3` instead of `python`
**Windows:** Python is not in your PATH. Re-run the Python installer and check "Add Python to PATH"

### Error: `No module named 'alpaca'`
Your virtual environment is not active. Run:
Mac: `source venv/bin/activate`
Windows: `venv\Scripts\activate`
Then try again.

### Error: `ModuleNotFoundError: No module named 'yaml'`
Install dependencies:
```
pip install -r requirements.txt
```

### Error: `ALPACA_PAPER is not set to true`
Open your `.env` file and make sure this line exists exactly:
```
ALPACA_PAPER=true
```
No quotes. No spaces around `=`.

### Error: `API key not found` or `Authentication failed`
- Open your `.env` file
- Make sure both `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` are set
- Make sure you are using Paper Trading keys, not Live Trading keys
- Make sure there are no extra spaces or quote marks around the keys

### Error: `.env file not found`
Create it by copying the example:
Mac: `cp .env.example .env`
Windows: `copy .env.example .env`
Then fill in your real keys.

### Error: `No signals found in paper_signals.csv`
This usually means:
- No stocks passed all the filters today
- No catalyst data exists in `catalyst_overrides.csv`
- The market was closed today
Add at least one entry to `catalyst_overrides.csv` and re-run `paper-scan`.

### Error: `Permission denied` when creating files
Mac: Add `sudo` before the command, or change the folder permissions.
Windows: Run Command Prompt as Administrator.

### Error: `yfinance: no data found`
This can happen on weekends or for symbols that no longer exist.
Try running again on a weekday when the market has recently been open.

### The bot ran but no orders were placed
Check these things in order:
1. Did `paper-scan` generate any signals? Check `reports/paper_signals.csv`
2. Do the signals have catalyst scores >= 8?
3. Is `ALPACA_PAPER=true` in your `.env`?
4. Are there enough stocks in `catalyst_overrides.csv`?
5. Run `python main.py paper-report` to see what the bot thinks the current state is.

---

## 35. Project File Reference

```
ai-catalyst-momentum-paper-trader/
│
├── README.md                   # This file. Beginner guide.
├── requirements.txt            # Python packages to install
├── .env.example                # Template for your secret API keys
├── .env                        # YOUR REAL KEYS (never share this)
├── config.yaml                 # All bot settings
├── main.py                     # Main entry point for all commands
│
├── data/
│   ├── blocked_symbols.csv     # Stocks the bot is never allowed to trade
│   ├── catalyst_overrides.csv  # Your manual catalyst scores and notes
│   └── universe_sample.csv     # Fallback stock list for testing only
│
├── reports/                    # Auto-generated by the bot (never committed to git)
│   ├── daily_discovered_universe.csv
│   ├── needs_catalyst_review.csv
│   ├── paper_signals.csv
│   ├── orders_log.csv
│   ├── paper_state.json
│   ├── performance_report.csv
│   ├── backtest_results.csv
│   ├── options_overlay_recommendations.csv
│   ├── scheduler_log.csv
│   ├── news_cache.csv          # Raw news headlines (auto-pulled)
│   ├── automated_catalyst_scores.csv  # Automated scoring results
│   ├── alerts_log.csv          # Trade/error alerts log
│   ├── automation_state.json   # Automation on/off state
│   └── tomorrow_orders.csv     # Proposed orders for human review
│
├── deploy/
│   └── papertrader.service     # systemd service file for Linux server
│
├── src/
│   ├── __init__.py
│   ├── config.py               # Loads config.yaml and .env
│   ├── data_loader.py          # Downloads price/volume data
│   ├── indicators.py           # Calculates technical indicators
│   ├── universe_discovery.py   # Auto-discovers stocks from market
│   ├── news_discovery.py       # Pulls news from Alpaca/Yahoo
│   ├── heuristic_scorer.py     # Scores catalysts (heuristic + LLM)
│   ├── catalyst_scorer.py      # Catalyst scoring orchestrator
│   ├── signal_engine.py        # Generates buy/sell signals
│   ├── ranking.py              # Ranks candidates by score
│   ├── portfolio.py            # Tracks positions and sizing
│   ├── backtester.py           # Historical backtesting
│   ├── broker_alpaca.py        # Alpaca Paper Trading connection
│   ├── execution_engine.py     # Order submission logic
│   ├── paper_trader.py         # Full paper trading workflow
│   ├── options_overlay.py      # Options recommendation generator
│   ├── reporting.py            # Report generation
│   ├── market_calendar.py      # U.S. market hours and holidays
│   ├── scheduler.py            # Automated scheduling
│   └── utils.py                # Shared helper functions
│
└── tests/
    ├── __init__.py
    ├── test_indicators.py
    ├── test_signal_engine.py
    ├── test_ranking.py
    ├── test_portfolio.py
    ├── test_broker_safety.py
    ├── test_broker_alpaca.py
    ├── test_universe_discovery.py
    └── test_scheduler.py
```

---

## 36. The Strategy Explained Simply

The strategy is called **AI Catalyst Momentum Breakout**.

The idea in plain English:

Occasionally, something real happens to a company — they beat earnings, win a huge contract, get FDA approval. When this happens, big institutional investors (hedge funds, mutual funds) start buying the stock. This creates a surge in volume and price.

The bot is trying to find these situations and ride the momentum.

**The strategy only trades when ALL of these are true at the same time:**

1. **Price breakout** — The stock is making a new 20-day high. This means buyers are more aggressive than they have been in the past month.

2. **Above trend** — The stock is above its 50-day moving average. This means the longer-term trend is positive.

3. **Strong recent return** — The stock is up more than 10% over the last 20 trading days.

4. **Volume surge** — Today's trading volume is more than 2.5 times the 20-day average. Unusual volume often signals that big money is moving in.

5. **Outperforming the market** — The stock has beaten the S&P 500 by more than 8% over 20 days. If the whole market is going up and this stock is going up faster, something specific is driving it.

6. **Real catalyst** — There is an identifiable news event or business development with a score of 8 or higher out of 10.

**Why all six conditions?**
Each condition alone might just be noise. All six together means something real is probably happening. The strategy is aggressive — it goes for powerful moves — but it is disciplined about only trading when everything lines up.

---

## 37. All Commands Reference

| Command | What It Does |
|---------|-------------|
| `python main.py setup-check` | Verifies your setup and Alpaca connection |
| `python main.py explain` | Explains how the algorithm works |
| `python main.py discover-universe` | Scans market for candidate stocks |
| `python main.py pull-news` | Fetches latest news for discovered stocks |
| `python main.py score-catalysts` | Scores catalysts (heuristic or LLM) |
| `python main.py scan` | Local scan (uses fixed universe fallback) |
| `python main.py paper-scan` | Full scan using discovered universe |
| `python main.py paper-execute` | Places paper orders based on signals |
| `python main.py paper-reconcile` | Syncs local records with Alpaca |
| `python main.py paper-report` | Prints status report |
| `python main.py automation-status` | Shows automation state and drawdown |
| `python main.py run-once-full-cycle` | Runs full pipeline without executing trades |
| `python main.py run-once-full-cycle --execute-paper` | Full pipeline WITH execution |
| `python main.py run-bot` | Starts always-on automated scheduler |
| `python main.py emergency-close-all` | Closes all paper positions immediately |
| `python main.py backtest --start 2020-01-01 --end 2024-12-31` | Runs historical backtest |

---

---

## 38. Automated News and Catalyst Scoring

The bot automatically fetches news and scores catalysts — no manual data entry required.

**How it works:**

1. **Pull news** — The bot calls the Alpaca News API (free with your Alpaca account) to fetch recent headlines for every stock in the discovered universe. If Alpaca news fails for a symbol, it falls back to Yahoo Finance as a backup.

2. **Score catalysts** — The bot reads those headlines and scores each stock using keyword-based heuristics:
   - High-value keywords: "FDA approval", "earnings beat", "raised guidance", "major contract", "breakthrough" → score higher
   - Negative keywords: "fraud", "investigation", "class action", "downgrade", "misses estimates" → score lower or blocked entirely
   - Hype-only keywords: "short squeeze", "reddit", "wallstreetbets" → rejected (never traded on social media)

3. **Score threshold** — A stock must reach a score of **8 or higher** (out of 10) to be eligible for a trade. This is set by `catalyst_scoring.min_score_to_trade` in `config.yaml`.

4. **Manual overrides** — If you want to manually force or block a catalyst, add a row to `data/catalyst_overrides.csv`. Manual entries always take priority over automated scores.

**Config controls:**
```yaml
news:
  enabled: true
  primary_provider: alpaca
  fallback_provider: yahoo
  days_back: 7
  max_news_age_days: 7

catalyst_scoring:
  mode: heuristic           # "heuristic" = free, no API key needed
  use_llm_if_available: false  # set to true + add API key to enable LLM
  min_score_to_trade: 8
  reject_negative_flags: true
  reject_hype_only: true
```

**To run manually:**
```
python main.py pull-news
python main.py score-catalysts
```

Results are saved to:
- `reports/news_cache.csv` — raw headlines
- `reports/automated_catalyst_scores.csv` — final scores

---

## 39. LLM Catalyst Scoring Setup

By default, the bot uses free keyword-based heuristic scoring. You can optionally enable LLM scoring for borderline stocks (those scoring 5–8 on heuristics) to get more accurate decisions.

**Which LLM does it use?**
- **Primary: OpenAI GPT-4o-mini** — fastest, cheapest (~$0.01–0.05/day typical)
- **Fallback: Anthropic Claude Haiku** — used only if no OpenAI key is set

**Step 1: Enable LLM scoring in config.yaml**
```yaml
catalyst_scoring:
  use_llm_if_available: true
```

**Step 2: Add your API key to .env**

For OpenAI (recommended):
```
OPENAI_API_KEY=sk-...your-key-here...
```

For Anthropic (fallback):
```
ANTHROPIC_API_KEY=sk-ant-...your-key-here...
```

You only need one. If both are set, OpenAI is used.

**Step 3: Install the optional package**
```
pip install openai
```
Or for Anthropic:
```
pip install anthropic
```

**How to get an OpenAI API key:**
1. Go to platform.openai.com and create an account
2. Click "API Keys" in the left sidebar
3. Click "Create new secret key"
4. Copy and paste into your `.env` file
5. Add a small amount of credit ($5 is more than enough for months of use)

**LLM scoring is optional.** The bot works perfectly well with heuristic scoring only. LLM scoring mainly helps for nuanced headlines where keyword matching is ambiguous.

---

## 40. Alpha Quality Filters

Beyond the 6 core conditions (see Section 36), the bot applies additional quality filters to reduce bad trades and improve entry timing. All thresholds are in `config.yaml` under `alpha_filters:`.

| Filter | What It Checks | Config Setting |
|--------|---------------|----------------|
| **Market regime** | Skip all buys if SPY is below its 50-day MA | Automatic |
| **Earnings avoidance** | Skip if earnings report is within 5 calendar days | `earnings_avoidance_days: 5` |
| **Sector cap** | Never hold more than 2 stocks in the same GICS sector | `sector_max_positions: 2` |
| **Extension filter** | Skip if price is >30% above its 20-day MA (overextended) | `max_extension_pct: 0.30` |
| **Gap-up filter** | At execution, skip if stock already gapped up >5% | `gap_up_max_pct: 0.05` |
| **ATR adaptive stop** | Stop loss adapts to each stock's volatility (1.5×ATR) | `atr_stop_multiplier: 1.5` |
| **52-week high proximity** | Skip if price is >15% below its 52-week high | `max_pct_below_52w_high: 0.15` |
| **Multi-day volume** | Require elevated volume on at least 1 of prior 2 days | `require_multi_day_volume: true` |
| **Daily entry cap** | Take at most 3 new positions per day | `max_daily_entries: 3` |
| **ADX trend strength** | Skip if ADX < 25 (choppy, no clear trend) | `min_adx: 25` |
| **Pre-breakout tightness** | Skip if 5-day range before breakout > 8% (choppy consolidation) | `max_pre_breakout_range_pct: 0.08` |
| **Sector ETF trend** | Skip if the stock's sector ETF is below its 50-day MA | `require_sector_etf_uptrend: true` |
| **Conviction sizing** | Score 8→18%, Score 9→21.5%, Score 10→25% of portfolio | Automatic |
| **Multi-timeframe momentum** | Both 5-day and 10-day returns must be non-negative | Automatic |

These filters all default to `true` or their stated thresholds. You can loosen or tighten them in `config.yaml` without touching any code.

**To loosen filters for more trades** (useful for testing):
```yaml
alpha_filters:
  min_adx: 0
  require_sector_etf_uptrend: false
  max_extension_pct: 0.50
```

**To tighten for higher quality** (fewer, better trades):
```yaml
alpha_filters:
  min_adx: 30
  max_pre_breakout_range_pct: 0.05
  max_pct_below_52w_high: 0.10
```

---

## 41. Server Deployment on Oracle Cloud

Oracle Cloud offers a free-tier ARM instance (4 CPUs, 24 GB RAM) that is more than enough to run this bot 24/7.

### Server Setup

**Step 1: Create a free Oracle Cloud account**
1. Go to oracle.com/cloud/free
2. Create an account (requires a credit card for verification, but the free tier is genuinely free)
3. Create an "Always Free" ARM VM instance (Ampere A1 shape)
4. Choose Ubuntu 22.04 as the operating system
5. Download the SSH key they give you

**Step 2: Connect to your server**
```
ssh -i ~/Downloads/your-key.key ubuntu@YOUR_SERVER_IP
```

**Step 3: Install Python and git**
```
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git
```

**Step 4: Upload your project**
From your local machine:
```
scp -i ~/Downloads/your-key.key -r ~/Desktop/ai-catalyst-momentum-paper-trader ubuntu@YOUR_SERVER_IP:~/
```

Or use git if you have pushed to GitHub:
```
git clone https://github.com/YOUR_USERNAME/ai-catalyst-momentum-paper-trader.git
```

**Step 5: Set up the project on the server**
```
cd ~/ai-catalyst-momentum-paper-trader
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env    # paste your real Alpaca API keys
python3 main.py setup-check
```

### Running as a System Service (systemd)

Instead of leaving a terminal window open, use systemd to run the bot as a background service that starts automatically when the server reboots.

**Step 1: Edit the service file**
```
nano deploy/papertrader.service
```
Change the three lines marked "EDIT THIS":
- `User=ubuntu` — should match your server username (usually `ubuntu` on Oracle Cloud)
- `WorkingDirectory=` — full path to the project (e.g. `/home/ubuntu/ai-catalyst-momentum-paper-trader`)
- `ExecStart=` — full path to Python inside venv + `main.py run-bot`

**Step 2: Install and start the service**
```
sudo cp deploy/papertrader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable papertrader
sudo systemctl start papertrader
```

**Step 3: Verify it is running**
```
sudo systemctl status papertrader
```
You should see `Active: active (running)`.

**Step 4: View live logs**
```
sudo journalctl -u papertrader -f
```
Press `Ctrl+C` to stop watching logs. The bot keeps running.

### Managing the Service

| Action | Command |
|--------|---------|
| Check status | `sudo systemctl status papertrader` |
| View logs | `sudo journalctl -u papertrader -f` |
| Stop the bot | `sudo systemctl stop papertrader` |
| Restart the bot | `sudo systemctl restart papertrader` |
| Disable auto-start | `sudo systemctl disable papertrader` |

### Updating the Bot

When you make changes to the code:
```
# Upload new files from your local machine:
scp -i ~/Downloads/your-key.key -r ~/Desktop/ai-catalyst-momentum-paper-trader ubuntu@YOUR_SERVER_IP:~/

# On the server, restart the service:
sudo systemctl restart papertrader
```

### Important Notes for Server Deployment

- **The `.env` file must exist on the server** with your real Alpaca paper API keys. It is never committed to git.
- **The `reports/` folder** is created automatically. It is excluded from git — reports stay only on the server.
- **The `WorkingDirectory` line** in the service file is critical. All file paths in the bot are relative to this directory.
- **SIGTERM handling** — When you run `sudo systemctl stop papertrader`, the bot catches the signal and finishes the current task before stopping cleanly. It will not be killed mid-order.
- **Automatic restart** — If the bot crashes (e.g. network timeout), systemd restarts it automatically after 30 seconds.
- **Log rotation** — The bot automatically trims `orders_log.csv` and `alerts_log.csv` to the last 90 days when they exceed 1000 rows. Systemd journal logs rotate automatically.

---

*This project is for paper trading, education, and strategy research only.*
*It is not financial advice. Past paper trading performance does not predict real trading results.*
