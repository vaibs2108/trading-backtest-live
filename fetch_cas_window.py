"""
fetch_cas_window.py — Pull 1-minute BANKNIFTY data for the new 3:15-3:40 PM
Closing Auction Session (CAS) window, for today and yesterday, and save it
to CSV for analysis/plotting.

Run from the project root (same pattern as diag_multi_agent_today.py):
    uv run fetch_cas_window.py

Requires DHAN_CLIENT_CODE / DHAN_ACCESS_TOKEN (or dhan_client_code /
dhan_access_token in .env) to already be set, same as every other script
in this project that talks to Dhan.

Notes:
  - Dhan's historical API tops out at 1-minute granularity (no seconds/tick
    historical data exists), so this pulls the finest data actually
    available — see the earlier discussion on this.
  - Fetches the BANKNIFTY INDEX series (get_historical_data's default,
    use_index=True) since the index value is continuously computed through
    the 3:15-3:40 window even while underlying constituents go through
    their own Closing Auction Session. If you specifically want BANKNIFTY
    FUTURES price action instead (which now also trades until 3:40 PM with
    +/-3% bands from 3:15), rerun with USE_INDEX = False below.
  - Output is plain OHLC + volume, one row per minute — Dhan's historical
    API does not expose CAS-specific fields (indicative equilibrium price,
    imbalance quantity, etc.) that NSE only broadcasts live on the
    exchange terminal, not through the historical candle API.
"""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

import pandas as pd
from datetime import datetime, time, timedelta
import pytz

from config import get_settings
cfg = get_settings()

import broker

INSTRUMENT = "BANKNIFTY"
USE_INDEX = True   # False => BANKNIFTY FUTURES instead of the index value
WINDOW_START = time(15, 15, 0)
WINDOW_END = time(15, 40, 0)

client_code = cfg.dhan_client_code
access_token = cfg.dhan_access_token
if not client_code or not access_token:
    print(f"ERROR: Dhan credentials not found. client_code={'set' if client_code else 'MISSING'}, "
          f"access_token={'set' if access_token else 'MISSING'}")
    sys.exit(1)

ok, msg = broker.connect(client_code, access_token)
if not ok:
    print(f"ERROR: {msg}")
    sys.exit(1)
print(f"Dhan connected: {msg}")

ist = pytz.timezone("Asia/Kolkata")
now_ist = datetime.now(ist)
today_str = now_ist.strftime("%Y-%m-%d")
yesterday_str = (now_ist - timedelta(days=1)).strftime("%Y-%m-%d")
# Dhan's historical API end-date is exclusive, so add a day to actually include "today"
to_date_inclusive = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")

print(f"Fetching 1-min {INSTRUMENT} ({'INDEX' if USE_INDEX else 'FUTURES'}) data: "
      f"{yesterday_str} -> {today_str}")

df = broker.get_historical_data(INSTRUMENT, "1", yesterday_str, to_date_inclusive, use_index=USE_INDEX)

if df is None or df.empty:
    print("ERROR: No data returned. Check Dhan connection/instrument/market hours.")
    sys.exit(1)

df['timestamp'] = pd.to_datetime(df['timestamp'])
df['date'] = df['timestamp'].dt.date.astype(str)
df['time'] = df['timestamp'].dt.time

window = df[
    (df['date'].isin([yesterday_str, today_str])) &
    (df['time'] >= WINDOW_START) &
    (df['time'] <= WINDOW_END)
].copy()

cols = ['timestamp', 'date', 'open', 'high', 'low', 'close']
if 'volume' in window.columns:
    cols.append('volume')
window = window[cols].sort_values('timestamp').reset_index(drop=True)

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cas_window_data.csv")
window.to_csv(out_path, index=False)

print(f"\nSaved {len(window)} rows to {out_path}")
for d in [yesterday_str, today_str]:
    day_rows = window[window['date'] == d]
    if day_rows.empty:
        print(f"  {d}: NO ROWS (market may not have reached/completed this window yet, or was a holiday)")
    else:
        print(f"  {d}: {len(day_rows)} rows, {day_rows['timestamp'].min()} -> {day_rows['timestamp'].max()}, "
              f"close range {day_rows['close'].min():.2f}-{day_rows['close'].max():.2f}")

print("\nFirst 5 rows:")
print(window.head().to_string(index=False))
print("\nLast 5 rows:")
print(window.tail().to_string(index=False))
