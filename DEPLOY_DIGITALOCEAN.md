# Deploy to DigitalOcean Droplet — Step by Step

## Step 0: Trim requirements.txt (Do This First, On Your Local Machine)

Your current strategies (regime_trend_range, multi_agent) don't use xgboost, scikit-learn, or faiss at runtime.
Removing them cuts the Docker image from ~1.5GB to ~800MB and lets the app run on 1GB RAM.

Open `requirements.txt` and **comment out or delete these 4 lines**:

```
scikit-learn>=1.4.0
xgboost>=2.0.0
faiss-cpu>=1.7.0
matplotlib>=3.8.0
```

Save the file, commit, and push to GitHub.

---

## Step 1: Create a DigitalOcean Account & Droplet

1. Go to https://cloud.digitalocean.com and sign up (or log in)
2. Click **Create** → **Droplets**
3. Configure:
   - **Region**: Bangalore (BLR1) — closest to Indian markets
   - **Image**: Ubuntu 24.04 LTS
   - **Plan**: Basic (Shared CPU) → **Regular SSD** → **$6/mo** (1 vCPU, 1 GB RAM, 25 GB SSD, 1 TB transfer)
   - **Authentication**: Choose **Password** (simpler for first time) or **SSH Key** (more secure)
   - **Hostname**: `trading-app` (or whatever you like)
4. Click **Create Droplet**
5. Wait ~60 seconds. Note down the **IP address** shown (e.g. `164.90.xxx.xxx`)

---

## Step 2: Connect to Your Droplet via SSH

### From Windows (PowerShell or Command Prompt):
```bash
ssh root@YOUR_DROPLET_IP
```
It will ask for the password you set in Step 1. Type it and press Enter.

### If SSH is not available:
- Use the **Droplet Console** in the DigitalOcean dashboard (click your droplet → Access → Launch Droplet Console)

You should now see a terminal prompt like: `root@trading-app:~#`

---

## Step 3: Install Docker & Git on the Droplet

```bash
curl -fsSL https://get.docker.com | sh
apt install -y git
```

Verify:
```bash
docker --version
git --version
```

---

## Step 4: Clone Your GitHub Repo

```bash
git clone https://github.com/YOUR_USER/YOUR_REPO.git /opt/trading-app
cd /opt/trading-app
```

If the repo is **private**, you'll need a GitHub Personal Access Token (PAT):
1. Go to https://github.com/settings/tokens → Generate new token (classic)
2. Select `repo` scope, generate, and copy the token
3. Clone using:
```bash
git clone https://YOUR_GITHUB_USERNAME:YOUR_TOKEN@github.com/YOUR_USER/YOUR_REPO.git /opt/trading-app
```

**What Docker handles for you**: It installs all Python packages and builds the React frontend from scratch inside the container. You don't need `.venv/` or `node_modules/` on the droplet.

---

## Step 5: Build the Docker Image

```bash
cd /opt/trading-app
docker build -t trading-app .
```

This will take **5-10 minutes** the first time. It:
1. Downloads Node.js and builds your React frontend
2. Downloads Python and installs all packages from requirements.txt
3. Packages everything into a single Docker image

Wait for: `naming to docker.io/library/trading-app:latest`

**Note**: You may see a yellow warning about "JSONArgsRecommended" — this is harmless, ignore it.

---

## Step 6: Create Your Credentials File

Your Dhan and Telegram credentials go in a `.env` file on the droplet (NOT inside the Docker image):

```bash
nano /opt/trading-app/.env
```

Paste these lines (replace with your actual values):

```
DHAN_CLIENT_CODE=1234567890
DHAN_ACCESS_TOKEN=eyJhbGciOi...your_actual_token...
TELEGRAM_BOT_TOKEN=7123456789:AAH...your_bot_token...
TELEGRAM_CHAT_ID=123456789
```

Save: press `Ctrl+O`, then `Enter`, then `Ctrl+X` to exit nano.

**Port note**: The Dockerfile defaults to port 8000. The docker run command maps `-p 8000:8000`. These match — no PORT override needed in `.env`.

---

## Step 7: Start the App (Foreground Mode)

For initial setup and daily use, run in **foreground** so you can see all logs live:

```bash
cd /opt/trading-app
docker run \
  --name trading-app \
  --env-file /opt/trading-app/.env \
  -p 8000:8000 \
  -v /opt/trading-app/data:/app/data \
  trading-app
```

You'll see logs streaming in real time. Press `Ctrl+C` to stop the app.

What each flag does:
- `--env-file` → loads your Dhan/Telegram credentials
- `-p 8000:8000` → maps droplet port 8000 to container port 8000
- `-v /opt/trading-app/data:/app/data` → saves trade logs to the droplet's disk (survives container restarts)

**Verify in the logs that you see**: `Uvicorn running on http://0.0.0.0:8000` — if it says 7860 instead, your Dockerfile hasn't been updated. Pull the latest code and rebuild.

---

## Step 8: Verify It's Working

Open a **second SSH terminal** to the same droplet (keep the first one running the app):

```bash
ssh root@YOUR_DROPLET_IP
```

Then test:
```bash
# Check the container is running
docker ps

# Test the API
curl http://localhost:8000/api/status
```

Now open your browser and go to:
```
http://YOUR_DROPLET_IP:8000
```

You should see your trading dashboard.

---

## Step 9: Set Up Firewall

By default the droplet is open to all traffic. Lock it down:

```bash
ufw allow 22      # SSH access
ufw allow 8000    # Your app
ufw enable
```

Type `y` when it asks to confirm.

---

## Step 10: (Optional) Custom Domain with HTTPS

If you have a domain (e.g. `trade.yourdomain.com`):

1. In your domain registrar (GoDaddy, Namecheap, etc.), add a DNS **A record**:
   - Name: `trade` (or whatever subdomain)
   - Value: `YOUR_DROPLET_IP`
   - TTL: 300

2. Wait 5-10 minutes for DNS to propagate

3. Install Caddy (auto-SSL reverse proxy):
```bash
apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install -y caddy
```

4. Configure Caddy:
```bash
nano /etc/caddy/Caddyfile
```

Replace contents with:
```
trade.yourdomain.com {
    reverse_proxy localhost:8000
}
```

Save and restart:
```bash
systemctl restart caddy
ufw allow 443    # HTTPS
ufw allow 80     # HTTP (for SSL certificate renewal)
```

Your app is now at `https://trade.yourdomain.com` with auto-renewed SSL.

---

## Daily Usage Guide

### Morning Routine (Before Market Opens)

```bash
# 1. SSH into your droplet
ssh root@YOUR_DROPLET_IP

# 2. Update Dhan access token (it expires daily)
nano /opt/trading-app/.env
# Change the DHAN_ACCESS_TOKEN line, save with Ctrl+O, Enter, Ctrl+X

# 3. Start the app
cd /opt/trading-app
docker run \
  --name trading-app \
  --env-file /opt/trading-app/.env \
  -p 8000:8000 \
  -v /opt/trading-app/data:/app/data \
  trading-app

# You'll see live logs. Confirm you see:
#   "Dhan connected. Balance: Rs XXXXX"
#   "Application startup complete"
#   "Uvicorn running on http://0.0.0.0:8000"
```

### After Market Closes (Evening)

```bash
# Press Ctrl+C in the terminal where the app is running
# That stops the container

# Clean up the stopped container (required before next start)
docker rm trading-app
```

### If You Need to Restart Mid-Day

```bash
# Press Ctrl+C to stop
docker rm trading-app

# Start again (no need to rebuild unless code changed)
docker run \
  --name trading-app \
  --env-file /opt/trading-app/.env \
  -p 8000:8000 \
  -v /opt/trading-app/data:/app/data \
  trading-app
```

### Check Status (From a Second Terminal)

```bash
ssh root@YOUR_DROPLET_IP

# Is the container running?
docker ps

# Check CPU/memory usage
docker stats trading-app

# Test API
curl http://localhost:8000/api/status

# View last 50 log lines
docker logs trading-app --tail 50
```

### Quick Command Reference

| Task | Command |
|------|---------|
| SSH into droplet | `ssh root@YOUR_DROPLET_IP` |
| Edit access token | `nano /opt/trading-app/.env` |
| Start app (foreground) | `docker run --name trading-app --env-file /opt/trading-app/.env -p 8000:8000 -v /opt/trading-app/data:/app/data trading-app` |
| Stop app | `Ctrl+C` in the running terminal |
| Remove stopped container | `docker rm trading-app` |
| Check if running | `docker ps` |
| View live logs (2nd terminal) | `docker logs -f trading-app` |
| View last N logs | `docker logs trading-app --tail 100` |
| Check memory/CPU | `docker stats trading-app` |
| Enter container shell | `docker exec -it trading-app bash` |
| Open dashboard | `http://YOUR_DROPLET_IP:8000` in browser |

---

## Updating Your Code

When you push changes to GitHub and want to update the droplet:

```bash
# 1. Stop the running app (Ctrl+C) and remove the container
docker rm trading-app

# 2. Pull latest code
cd /opt/trading-app
git pull

# 3. Rebuild the Docker image
docker build -t trading-app .

# 4. Start with fresh image
docker run \
  --name trading-app \
  --env-file /opt/trading-app/.env \
  -p 8000:8000 \
  -v /opt/trading-app/data:/app/data \
  trading-app
```

---

## Switching to Background Mode (When Stable)

Once you're confident the app runs reliably and you don't need to watch logs daily:

```bash
docker run -d \
  --name trading-app \
  --restart unless-stopped \
  --env-file /opt/trading-app/.env \
  -p 8000:8000 \
  -v /opt/trading-app/data:/app/data \
  trading-app
```

The `-d` flag runs it in background. `--restart unless-stopped` auto-restarts on crash or server reboot. You can close the SSH terminal and it keeps running.

In background mode, to update the token:
```bash
nano /opt/trading-app/.env
docker stop trading-app && docker rm trading-app
# Then run the docker run -d command above again
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `"name already in use"` when starting | Run `docker rm trading-app` first |
| `curl: Connection reset by peer` | Port mismatch — check logs show `running on 0.0.0.0:8000` not 7860. If 7860, pull latest Dockerfile and rebuild. |
| `"Dhan connected"` not showing in logs | Check DHAN_ACCESS_TOKEN in `.env` — token may be expired or wrong |
| App crashes / container exits | Run `docker logs trading-app --tail 100` to see the error |
| Can't connect from browser | Check firewall: `ufw status` — port 8000 must be allowed |
| Disk full | Run `docker system prune -a` to remove old images |

---

## Important Notes

- **Dhan access token expires daily** — update it in `.env` each morning before starting the app.
- **Market hours**: The app only trades during 9:15-15:30 IST. IST timezone is baked into the Docker image.
- **Cost**: $6/month for the BLR1 Regular SSD droplet (1 vCPU, 1 GB RAM, 25 GB SSD). Includes 1 TB outbound transfer.
- **Backup**: Your `day_stats.json` and trade logs persist in `/opt/trading-app/data/` on the droplet thanks to the `-v` volume mount.
- **Port**: The Dockerfile defaults to port 8000. The docker run maps `-p 8000:8000`. These match out of the box.
