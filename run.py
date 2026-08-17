"""
run.py — Single-command launcher for the DhanML Trading Engine.
Run: python run.py
"""
import os
import sys
import subprocess
from pathlib import Path

# Ensure UTF-8 output on Windows terminals to support emojis
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE = Path(__file__).parent


def check_model():
    model_path = BASE / "backend" / "models" / "ml_model.pkl"
    if model_path.exists():
        print(f"ℹ️  ML model file present at: {model_path}")
    print("✓  Strategy Engine active: Rule-Based Regime & Reversal Engine\n")


def check_frontend():
    dist = BASE / "frontend" / "dist" / "index.html"
    if not dist.exists():
        print("⚠️  Frontend not built. Building now...")
        fe_dir = BASE / "frontend"
        if not (fe_dir / "node_modules").exists():
            print("   Installing npm packages...")
            subprocess.run(["npm", "install"], cwd=str(fe_dir), check=True)
        subprocess.run(["npm", "run", "build"], cwd=str(fe_dir), check=True)
        print("✓  Frontend built successfully\n")
    else:
        print("✓  Frontend build found")


def ensure_dirs():
    for d in ["models", "data", "logs"]:
        (BASE / d).mkdir(exist_ok=True)
    print("✓  Directories ready")


def free_port(port=8000):
    import socket
    import time
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('127.0.0.1', port)) == 0:
                print(f"⚠️  Port {port} is busy. Clearing existing process...")
                if sys.platform == 'win32':
                    ps_cmd = (
                        f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | "
                        f"Select-Object -ExpandProperty OwningProcess | "
                        f"Where-Object {{ $_ -gt 0 }} | "
                        f"ForEach-Object {{ Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }}"
                    )
                    subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)
                time.sleep(1.5)
    except Exception:
        pass


def main():
    print("=" * 55)
    print("  DhanML — Multi-Agent Algo Trading Engine")
    print("=" * 55)
    ensure_dirs()
    check_model()
    check_frontend()
    free_port(8000)

    print("\n🚀 Starting server at http://localhost:8000 (supervised by watchdog)\n")
    os.chdir(BASE)
    # Add backend to path
    sys.path.insert(0, str(BASE / "backend"))

    try:
        import watchdog
        # watchdog.run_watchdog() launches backend/main.py as a supervised
        # child subprocess (`uvicorn main:app`), monitors its heartbeat file,
        # and auto-restarts it on crash or freeze (including recovering from
        # a genuine OS-level sleep/suspend). This is the same watchdog used
        # by `python backend/watchdog.py` directly — `uv run run.py` now
        # does the setup checks above AND gives you that same crash/freeze
        # recovery, from a single command, instead of running uvicorn
        # in-process with no supervision.
        watchdog.run_watchdog()
    except (KeyboardInterrupt, SystemExit):
        pass
    except ImportError as e:
        print(f"ERROR: could not import watchdog ({e}). Run: uv pip install -r requirements.txt")
        sys.exit(1)
    finally:
        print("\n👋 DhanML trading engine stopped cleanly.")
        os._exit(0)


if __name__ == "__main__":
    main()
