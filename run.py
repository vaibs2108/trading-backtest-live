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
    if not model_path.exists():
        print("⚠️  Warning: ML model not found at backend/models/ml_model.pkl")
        print("   System will run in rule-only mode.")
        print("   To add ML: copy your model.pkl to backend/models/ml_model.pkl\n")
    else:
        print(f"✓  ML model found: {model_path}")


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


def main():
    print("=" * 55)
    print("  DhanML — Multi-Agent Algo Trading Engine")
    print("=" * 55)
    ensure_dirs()
    check_model()
    check_frontend()

    print("\n🚀 Starting server at http://localhost:8000\n")
    os.chdir(BASE)
    # Add backend to path
    sys.path.insert(0, str(BASE / "backend"))

    try:
        import uvicorn
        uvicorn.run(
            "backend.main:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            log_level="info",
        )
    except ImportError:
        print("ERROR: uvicorn not installed. Run: uv pip install -r requirements.txt")
        sys.exit(1)


if __name__ == "__main__":
    main()
