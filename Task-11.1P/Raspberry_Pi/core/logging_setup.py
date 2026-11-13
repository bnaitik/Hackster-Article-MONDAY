import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config.settings import DATA_DIR

def setup_logging():
    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "smart_desk_bot.log"

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )

    console_handler = logging.StreamHandler()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler],
        force=True,
    )

    return logging.getLogger("smart_desk_bot")
