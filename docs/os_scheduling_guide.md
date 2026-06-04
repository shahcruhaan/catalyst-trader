# Automated Scheduling Guide — Running the Bot Automatically

This guide shows how to make the bot start automatically each day without you having to type a command.

---

## Option A: Run the bot manually (simplest)

The easiest approach: open Terminal, run the bot, leave it running all day.

```
source venv/bin/activate
python main.py run-bot
```

The bot will run until you close the Terminal window or press Ctrl+C.

---

## Option B: Mac — cron (runs a command on a schedule)

### What is cron?
cron is a built-in Mac/Linux tool that runs commands on a schedule — like a personal alarm clock for your computer.

### Step 1: Find your project path

Open Terminal and run:
```
pwd
```
It will show something like `/Users/yourname/Desktop/ai-catalyst-momentum-paper-trader`.

Note this path — you'll need it below.

### Step 2: Find your Python path

With your virtual environment active:
```
source venv/bin/activate
which python3
```
It will show something like `/Users/yourname/Desktop/ai-catalyst-momentum-paper-trader/venv/bin/python3`.

### Step 3: Open the cron editor

```
crontab -e
```

This opens a text editor (usually `vi` or `nano`).

### Step 4: Add a cron entry

Add this line (replace the path with your actual project path):

```
0 9 * * 1-5 cd /Users/yourname/Desktop/ai-catalyst-momentum-paper-trader && /Users/yourname/Desktop/ai-catalyst-momentum-paper-trader/venv/bin/python3 main.py run-bot >> logs/bot.log 2>&1
```

**What this does:**
- `0 9 * * 1-5` — run at 9:00 AM, Monday through Friday
- `cd /path/to/project` — change to your project folder first
- `>> logs/bot.log 2>&1` — save all output to a log file

**Save and exit:**
- In `vi`: press `Esc`, type `:wq`, press Enter
- In `nano`: press Ctrl+X, then Y, then Enter

### Step 5: Create the logs folder

```
mkdir -p /Users/yourname/Desktop/ai-catalyst-momentum-paper-trader/logs
```

### Step 6: Verify the cron entry was saved

```
crontab -l
```

You should see the line you added.

### Important notes for Mac cron

**Mac may block Terminal from running scripts automatically.**
If the bot doesn't start, you need to give Terminal "Full Disk Access":
1. Open System Settings → Privacy & Security → Full Disk Access
2. Click the + button and add Terminal (or iTerm2 if you use that)

**The bot must find its .env file.** Since cron uses a minimal environment,
always use the full absolute path to the project in your cron command (as shown above).

---

## Option C: Mac — launchd (more reliable than cron)

`launchd` is the recommended way to schedule jobs on modern Macs. It's more reliable than cron and restarts jobs if they crash.

### Step 1: Create a plist file

Create the file `~/Library/LaunchAgents/com.papertrader.runbot.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.papertrader.runbot</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/yourname/Desktop/ai-catalyst-momentum-paper-trader/venv/bin/python3</string>
        <string>/Users/yourname/Desktop/ai-catalyst-momentum-paper-trader/main.py</string>
        <string>run-bot</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/yourname/Desktop/ai-catalyst-momentum-paper-trader</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
        <key>Weekday</key>
        <integer>1</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/yourname/Desktop/ai-catalyst-momentum-paper-trader/logs/bot_stdout.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/yourname/Desktop/ai-catalyst-momentum-paper-trader/logs/bot_stderr.log</string>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

**Replace `/Users/yourname/Desktop/...` with your actual path everywhere.**

Note: `Weekday` values are 0=Sunday, 1=Monday, ..., 5=Friday, 6=Saturday.
The above example starts at 9:00 AM on Mondays only. If you want it to run
Monday-Friday, you'll need 5 separate `StartCalendarInterval` blocks, one per weekday.

### Step 2: Load the job

```
launchctl load ~/Library/LaunchAgents/com.papertrader.runbot.plist
```

### Step 3: Check it loaded

```
launchctl list | grep papertrader
```

### Useful launchctl commands

```
# Unload (stop scheduling):
launchctl unload ~/Library/LaunchAgents/com.papertrader.runbot.plist

# Reload after editing the plist:
launchctl unload ~/Library/LaunchAgents/com.papertrader.runbot.plist
launchctl load ~/Library/LaunchAgents/com.papertrader.runbot.plist

# Run the job right now (for testing):
launchctl start com.papertrader.runbot
```

---

## Option D: Windows — Task Scheduler

### Step 1: Open Task Scheduler

Press Win+S, search for "Task Scheduler", open it.

### Step 2: Create a new task

1. In the right panel, click **"Create Task..."** (not "Create Basic Task")
2. Give it a name: `PaperTraderBot`
3. Check **"Run only when user is logged on"**

### Step 3: Set the trigger

1. Click the **Triggers** tab
2. Click **New...**
3. Set **Begin the task:** "On a schedule"
4. Set **Settings:** Weekly
5. Set **Start:** 9:00 AM, check Mon, Tue, Wed, Thu, Fri
6. Click OK

### Step 4: Set the action

1. Click the **Actions** tab
2. Click **New...**
3. Set **Action:** "Start a program"
4. **Program/script:**
   ```
   C:\Users\yourname\Desktop\ai-catalyst-momentum-paper-trader\venv\Scripts\python.exe
   ```
5. **Add arguments:**
   ```
   main.py run-bot
   ```
6. **Start in:**
   ```
   C:\Users\yourname\Desktop\ai-catalyst-momentum-paper-trader
   ```
7. Click OK

### Step 5: Set conditions

1. Click the **Conditions** tab
2. Uncheck "Start the task only if the computer is on AC power" (if you're on a laptop)

### Step 6: Save

Click OK. Windows may ask for your password to save the task.

### Step 7: Test it

Right-click your task in the list → **Run**. A command window should briefly appear.

---

## Checking the bot is working

After the bot runs automatically, check these files:

```
reports/scheduler_log.csv   — every task the bot ran, with timestamps
logs/bot.log               — full output (if you set up cron/launchd logging)
```

The scheduler log shows something like:
```
2025-06-03 09:32:05, 2025-06-03, paper_execute,  SUCCESS, 12.3, 2 buys 0 sells
2025-06-03 09:45:11, 2025-06-03, paper_reconcile, SUCCESS,  8.1, 2 positions portfolio=$101200
2025-06-03 15:45:02, 2025-06-03, intraday_exit_check, SUCCESS, 3.2, 0 exits found
2025-06-03 16:10:08, 2025-06-03, paper_report,   SUCCESS,  9.4, portfolio=$101200 2 positions
2025-06-03 16:20:15, 2025-06-03, discover_universe, SUCCESS, 22.1, 48 tickers discovered
2025-06-03 16:35:44, 2025-06-03, paper_scan,     SUCCESS, 35.8, 1 buy 0 exit 48 scanned
```

If a task shows ERROR, check the `detail` column for the error message.

---

## Recommendation for beginners

The simplest setup: just run `python main.py run-bot` manually each morning
at 9:00 AM Eastern and leave it running. You don't need cron or Task Scheduler
to trade with this system — the scheduler is just a convenience so you don't
have to remember to start it.
