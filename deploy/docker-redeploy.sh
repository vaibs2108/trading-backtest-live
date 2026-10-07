#!/usr/bin/env bash
# Redeploy the trading app on the droplet (Docker, same image/ports/.env as before).
#
# What it adds to the old `docker run`:
#   -d + --restart unless-stopped : runs in the background and comes back by itself after a crash
#                                   or a droplet reboot (closing the console / Ctrl+C no longer stops it)
#   state/ volumes                : positions, journal, capital, settings and logs survive a redeploy
#                                   (they lived inside the container and were wiped on every docker rm)
#
# Usage (on the droplet):   cd /opt/trading-app && bash deploy/docker-redeploy.sh
# Restart between candle closes (e.g. hh:02 or hh:07), not right at a close.
set -euo pipefail

APP=/opt/trading-app
STATE=$APP/state
NAME=trading-app

cd "$APP"
git pull
docker build --no-cache -t "$NAME" .

# One time: move the running container's state out to the host before it is removed.
mkdir -p "$STATE/logs"
if [ ! -d "$STATE/data" ]; then
  if docker container inspect "$NAME" >/dev/null 2>&1; then
    docker cp "$NAME:/app/backend/data" "$STATE/data" || mkdir -p "$STATE/data"
  else
    mkdir -p "$STATE/data"
  fi
fi
if [ ! -f "$STATE/settings.json" ]; then
  if docker container inspect "$NAME" >/dev/null 2>&1; then
    docker cp "$NAME:/app/backend/settings.json" "$STATE/settings.json" 2>/dev/null || echo '{}' > "$STATE/settings.json"
  else
    echo '{}' > "$STATE/settings.json"
  fi
fi
if [ -d "$STATE/settings.json" ]; then        # a bind mount of a missing file creates a folder
  rm -rf "$STATE/settings.json"; echo '{}' > "$STATE/settings.json"
fi
# The container runs as a non-root user (appuser): it must own the state folder, or the app cannot
# write its logs and crash-loops (found 07 Oct). Read its uid/gid from the image, don't assume.
APP_UID=$(docker run --rm --entrypoint id "$NAME" -u)
APP_GID=$(docker run --rm --entrypoint id "$NAME" -g)
chown -R "$APP_UID:$APP_GID" "$STATE"
# .env is MOUNTED, not passed with --env-file: a Dhan token refreshed on the Settings page is written
# to this file, so it survives an app restart or redeploy. With --env-file the container kept the
# token from container creation, and every restart went back to it (DH-901).
chown "$APP_UID:$APP_GID" "$APP/.env" && chmod 600 "$APP/.env"

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --restart unless-stopped \
  --name "$NAME" \
  -v "$APP/.env:/app/.env" \
  -p 8000:8000 \
  -v "$APP/data:/app/data" \
  -v "$STATE/data:/app/backend/data" \
  -v "$STATE/logs:/app/backend/logs" \
  -v "$STATE/settings.json:/app/backend/settings.json" \
  "$NAME"

echo
echo "Started. Follow the log with:  docker logs -f $NAME   (Ctrl+C there only stops watching)"
docker ps --filter "name=$NAME" --format "{{.Names}}  {{.Status}}"
