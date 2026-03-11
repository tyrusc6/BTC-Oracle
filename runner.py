"""
BTC Oracle - Railway Runner
Runs both the collector and scheduler in one process for Railway deployment.
"""

import threading
import time
from datetime import datetime, timezone, timedelta
from collector import run_collector
from bot import run_signal_cycle


def wait_for_next_kalshi_window():
    """Wait until 1 minute before the next :00, :15, :30, or :45 mark."""
    now = datetime.now(timezone.utc)
    minute = now.minute
    # Find next 15-min mark
    next_mark = ((minute // 15) + 1) * 15
    if next_mark >= 60:
        next_mark = 0
        target = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        target = now.replace(minute=next_mark, second=0, microsecond=0)
    # Fire 1 minute before the window opens so signal is ready
    target = target - timedelta(minutes=1)
    if target <= now:
        target = target + timedelta(minutes=15)
    wait_seconds = (target - now).total_seconds()
    print(f"[SCHEDULER] Next signal at {target.strftime('%H:%M:%S')} UTC ({wait_seconds:.0f}s from now)")
    time.sleep(wait_seconds)


def scheduler_loop():
    """Run signal cycles aligned to Kalshi's 15-minute windows."""
    print("[SCHEDULER] Starting... waiting 60s for collector to gather data...")
    time.sleep(60)

    # Run first cycle immediately
    try:
        print(f"\n[SCHEDULER] Running initial signal cycle...")
        run_signal_cycle()
    except Exception as e:
        print(f"[SCHEDULER ERROR] {e}")

    # Then align to Kalshi windows
    while True:
        wait_for_next_kalshi_window()
        try:
            print(f"\n[SCHEDULER] Running signal cycle (Kalshi-aligned)...")
            run_signal_cycle()
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}")


def main():
    print("=" * 60)
    print("BTC ORACLE - RAILWAY DEPLOYMENT")
    print("=" * 60)
    print(f"Started: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("Running collector + scheduler in one process")
    print("=" * 60)

    # Start scheduler in background thread
    sched_thread = threading.Thread(target=scheduler_loop, daemon=True)
    sched_thread.start()

    # Run collector in main thread (keeps process alive)
    run_collector()


if __name__ == "__main__":
    main()
