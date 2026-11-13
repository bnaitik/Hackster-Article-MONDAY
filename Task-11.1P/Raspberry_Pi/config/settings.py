from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"

APP_NAME = "Smart Desk Bot"
DEBUG = True

DATA_DIR.mkdir(parents=True, exist_ok=True)
