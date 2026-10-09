"""Settings. Values can be overridden in .env (KEY=value lines) or the environment."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

DATA_DIR = Path(os.environ.get("RACE_COACH_DATA",
                               Path.home() / "Library" / "Application Support" / "RaceCoach"))
DB_PATH = DATA_DIR / "coach.db"
BACKUP_DIR = DATA_DIR / "backups"
BACKUPS_KEPT = 30

HOST = "127.0.0.1"
PORT = int(os.environ.get("RACE_COACH_PORT", "8765"))

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("RACE_COACH_MODEL", "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M")
MODEL_OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 1500}

RULES_PATH = ROOT / "rules" / "brief_v1.yaml"
MIGRATIONS_DIR = ROOT / "migrations"

BIG_DAY_HOURS = 3


def prank_silenced() -> bool:
    return os.environ.get("RACE_COACH_PRANK", "").lower() == "off"
