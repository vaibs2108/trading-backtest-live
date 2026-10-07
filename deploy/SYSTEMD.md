# Run the app as a service on the droplet (systemd, no Docker)

Why: auto-trade exits Option B only from the app (no broker stop-loss for Option B), so the app must
come back by itself after a crash, a stray Ctrl+C, a closed console or a droplet reboot.
`run.py` already restarts the backend inside it; systemd adds the layer above it.
It uses no extra memory to speak of and changes nothing in the app.

All commands run on the droplet, in its console.

## 1. Check the two paths in `deploy/algotrader.service`

Go to the app folder and see how you run it today:

```bash
cd /path/to/your/app        # the folder with run.py
pwd                          # -> this is WorkingDirectory
ls .venv/bin/python          # if you use a virtualenv: ExecStart=<pwd>/.venv/bin/python run.py
which python3                # if you run plain "python3 run.py": ExecStart=<that path> run.py
```

Edit the two `EDIT` lines in `deploy/algotrader.service` to match (default: `/opt/trading-app` and
`/opt/trading-app/.venv/bin/python`).

## 2. Stop the copy you started by hand

Only one copy may run (port 8000, and one Dhan account). Stop the one in your console or screen/tmux
(Ctrl+C), and check that nothing listens on 8000:

```bash
ss -ltnp | grep 8000         # should print nothing
```

## 3. Install and start

```bash
sudo cp deploy/algotrader.service /etc/systemd/system/algotrader.service
sudo systemctl daemon-reload
sudo systemctl enable --now algotrader
systemctl status algotrader --no-pager     # "active (running)"
```

Open the app in the browser as usual and check the Live Trading page.

## Everyday use

| What | Command |
|---|---|
| Status | `systemctl status algotrader --no-pager` |
| Live log | `journalctl -u algotrader -f` (the app's own logs stay in `backend/logs/`) |
| Restart (after a deploy) | `sudo systemctl restart algotrader` |
| Stop (it stays stopped until start or reboot) | `sudo systemctl stop algotrader` |
| Start | `sudo systemctl start algotrader` |
| Don't start at boot any more | `sudo systemctl disable algotrader` |

## Deploying new code

```bash
cd /opt/trading-app          # your app folder
git pull
cd frontend && npm run build && cd ..
sudo systemctl restart algotrader
```

Restart between candle closes (e.g. at hh:02 or hh:07), not right at a close.

## Notes

- `Restart=always`: if you stop the app with `systemctl stop`, it stays stopped. If it crashes or is
  killed any other way, it is back within ~10 s.
- Auto-trade ON/OFF is still the app's own switch (Settings / Live Trading). The service only keeps
  the app running; it never turns auto-trade on.
- `TZ=Asia/Kolkata` is set for the app even if the droplet's clock is UTC.
