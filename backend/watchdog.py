"""
watchdog.py — System reliability: heartbeat, graceful shutdown, crash detection.

Run this INSTEAD of main.py directly. It:
1. Starts main.py as a subprocess
2. Monitors heartbeat file for liveness
3. Auto-restarts on crash
4. Handles graceful shutdown on Ctrl+C / SIGTERM
5. Logs all events to watchdog.log

Usage:
  python watchdog.py              # starts and monitors the app
  python watchdog.py --install    # (Windows) creates a scheduled task for auto-start on login
"""
import sys
import os
import time
import signal
import subprocess
import logging
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
HEARTBEAT_FILE = BASE_DIR / "data" / "heartbeat.json"
WATCHDOG_LOG = BASE_DIR / "logs" / "watchdog.log"
STATE_FILE = BASE_DIR / "data" / "app_state.json"

MAX_RESTARTS = 10          # max restarts before giving up
RESTART_WINDOW = 3600      # reset restart count after this many seconds
HEARTBEAT_TIMEOUT = 120    # seconds before considering app dead
CHECK_INTERVAL = 15        # seconds between health checks

# Setup logging
Path(BASE_DIR / "logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(message)s",
    handlers=[
        logging.FileHandler(str(WATCHDOG_LOG), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("watchdog")


import pytz
_IST = pytz.timezone("Asia/Kolkata")

def write_heartbeat():
    """Called by main.py periodically to signal it's alive."""
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEARTBEAT_FILE.write_text(json.dumps({
        "pid": os.getpid(),
        "timestamp": time.time(),
        "time": datetime.now(_IST).isoformat(),
    }), encoding="utf-8")


def check_heartbeat() -> bool:
    """Check if the app has written a heartbeat recently."""
    if not HEARTBEAT_FILE.exists():
        return True  # Allow time on fresh startup before first heartbeat is written
    try:
        data = json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
        ts = data.get("timestamp")
        if ts is not None:
            age = time.time() - float(ts)
            return age < HEARTBEAT_TIMEOUT
        
        last_beat = datetime.fromisoformat(data["time"])
        if last_beat.tzinfo is None:
            last_beat = _IST.localize(last_beat)
        now = datetime.now(_IST)
        age = abs((now - last_beat).total_seconds())
        return age < HEARTBEAT_TIMEOUT
    except Exception as ex:
        log.warning(f"Transient error reading heartbeat file: {ex}")
        return True  # Prevent false positive kill on file read lock


def save_app_state(state: str, details: str = ""):
    """Save application state for crash detection on next startup."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({
        "state": state,  # RUNNING, STOPPED, CRASHED
        "time": datetime.now(_IST).isoformat(),
        "pid": os.getpid(),
        "details": details,
    }), encoding="utf-8")


def check_last_state() -> dict:
    """Check how the app last exited."""
    if not STATE_FILE.exists():
        return {"state": "FRESH", "details": "First run"}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"state": "UNKNOWN", "details": "Could not read state file"}


def free_port(port=8000):
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', int(port))) == 0:
                log.warning(f"Port {port} is busy. Clearing existing process...")
                if sys.platform == 'win32':
                    ps_cmd = (
                        f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | "
                        f"Select-Object -ExpandProperty OwningProcess | "
                        f"Where-Object {{ $_ -gt 0 }} | "
                        f"ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }}"
                    )
                    subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)
                time.sleep(1.5)
    except Exception as ex:
        log.warning(f"Error freeing port {port}: {ex}")


def run_watchdog():
    """Main watchdog loop: start app, monitor, restart on crash."""
    log.info("=" * 60)
    log.info("Watchdog starting")

    # Check last state
    last = check_last_state()
    if last["state"] == "RUNNING":
        log.warning(f"Last shutdown was unclean (state=RUNNING at {last.get('time', '?')})")
        log.warning("App likely crashed. Position recovery will happen via broker sync on startup.")

    restart_count = 0
    restart_window_start = time.time()
    process = None

    def shutdown(signum=None, frame=None):
        nonlocal process
        log.info("Shutdown signal received")
        save_app_state("STOPPED", "Graceful shutdown via watchdog")
        if process and process.poll() is None:
            log.info("Terminating app process...")
            if sys.platform == "win32":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
                log.info("App process terminated gracefully")
            except subprocess.TimeoutExpired:
                process.kill()
                log.warning("App process killed after timeout")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, shutdown)

    while True:
        # Reset restart count if window has passed
        if time.time() - restart_window_start > RESTART_WINDOW:
            restart_count = 0
            restart_window_start = time.time()

        if restart_count >= MAX_RESTARTS:
            log.error(f"Max restarts ({MAX_RESTARTS}) reached in {RESTART_WINDOW}s window. Giving up.")
            save_app_state("STOPPED", f"Max restarts reached: {restart_count}")
            break

        # Start the app
        log.info(f"Starting app (attempt #{restart_count + 1})")
        save_app_state("RUNNING", f"Started by watchdog, attempt #{restart_count + 1}")
        if HEARTBEAT_FILE.exists():
            try:
                HEARTBEAT_FILE.unlink()
            except Exception:
                pass

        try:
            port = os.environ.get("PORT", "8000")
            free_port(port)
            cmd = [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(port)]
            creation_flags = 0
            if sys.platform == "win32":
                creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
            process = subprocess.Popen(
                cmd,
                cwd=str(BASE_DIR),
                creationflags=creation_flags,
            )
        except Exception as e:
            log.error(f"Failed to start app: {e}")
            restart_count += 1
            time.sleep(5)
            continue

        log.info(f"App started with PID {process.pid}")

        # Monitor loop
        while True:
            time.sleep(CHECK_INTERVAL)

            # Check if process is still running
            retcode = process.poll()
            if retcode is not None:
                log.error(f"App process exited with code {retcode}")
                save_app_state("CRASHED", f"Exit code: {retcode}")
                restart_count += 1
                break

            # Check heartbeat to detect freezes
            if not check_heartbeat():
                log.error("App heartbeat timeout - process appears frozen! Forcing restart.")
                save_app_state("CRASHED", "Heartbeat timeout (frozen)")
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
                restart_count += 1
                break

        # Brief pause before restart
        log.info("Restarting in 5 seconds...")
        time.sleep(5)

    log.info("Watchdog stopped")


def install_windows_task():
    """Create a Windows Task Scheduler entry to auto-start watchdog on login."""
    import getpass
    python_exe = sys.executable
    script_path = str(Path(__file__).resolve())
    task_name = "DhanML_Watchdog"

    cmd = (
        f'schtasks /create /tn "{task_name}" '
        f'/tr "\\\"{python_exe}\\\" \\\"{script_path}\\\"" '
        f'/sc ONLOGON /rl HIGHEST /f'
    )
    log.info(f"Creating scheduled task: {task_name}")
    os.system(cmd)
    log.info(f"Task '{task_name}' created. App will auto-start on login.")


def create_systemd_service():
    """Generate a systemd service file for DigitalOcean/Linux deployment."""
    python_exe = sys.executable
    working_dir = str(BASE_DIR.resolve())
    user = os.environ.get("USER", "root")

    service = f"""[Unit]
Description=DhanML Trading Engine
After=network.target

[Service]
Type=simple
User={user}
WorkingDirectory={working_dir}
ExecStart={python_exe} {str(Path(__file__).resolve())}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""
    service_path = BASE_DIR / "dhanml.service"
    service_path.write_text(service)
    log.info(f"Systemd service file written to: {service_path}")
    log.info("To install:")
    log.info(f"  sudo cp {service_path} /etc/systemd/system/")
    log.info("  sudo systemctl daemon-reload")
    log.info("  sudo systemctl enable dhanml")
    log.info("  sudo systemctl start dhanml")


if __name__ == "__main__":
    if "--install" in sys.argv:
        if sys.platform == "win32":
            install_windows_task()
        else:
            create_systemd_service()
    else:
        run_watchdog()
