# Running the app on the droplet (Docker)

Use `deploy/docker-redeploy.sh` for every deploy, in place of the manual `docker build` + `docker run`:

```bash
cd /opt/trading-app
git pull                          # gets the script itself the first time
bash deploy/docker-redeploy.sh
```

It does the same build and run as before, plus two things:

1. **`-d --restart unless-stopped`.** The app runs in the background, so closing the console or pressing
   Ctrl+C no longer stops it. It also comes back by itself after a crash or a droplet reboot. This matters
   because Option B has no broker stop-loss: only the running app exits it.
2. **State kept outside the container**, in `/opt/trading-app/state/`. The app keeps its positions,
   journal, capital, settings and logs in `/app/backend/data`, `/app/backend/logs` and
   `/app/backend/settings.json`. The old `-v /opt/trading-app/data:/app/data` did not cover these, so
   every `docker rm` + `docker run` started the app empty (default settings, no record of its own open
   position, no logs).
   - On the first run, the script copies them out of the current container before replacing it.

## Everyday commands

| What | Command |
|---|---|
| Is it running? | `docker ps --filter name=trading-app` |
| Live log | `docker logs -f trading-app` (Ctrl+C only stops watching) |
| App's own logs | `/opt/trading-app/state/logs/app.log`, `latency.log`, `watchdog.log` |
| Restart | `docker restart trading-app` |
| Stop (stays stopped until started) | `docker stop trading-app` |
| Start | `docker start trading-app` |

Restart or redeploy between candle closes (e.g. hh:02 or hh:07), not right at a close.
After a redeploy, check Settings in the app (strategy, trade mode, auto-trade switch) before market hours.
