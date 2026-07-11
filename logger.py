"""Unified logging for ECN2to1 project.

All modules share a single log file per run: logs/run_YYYYMMDD_HHMMSS.log
"""

import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Shared log file for the entire run — created once at import time
_LOG_FILE = LOG_DIR / f"run_{time.strftime('%Y%m%d_%H%M%S')}.log"


def get_logger(name: str = "") -> "Logger":
    return Logger(name)


class Logger:
    def __init__(self, name: str):
        self.name = name
        self.log_file = _LOG_FILE

    def info(self, msg: str) -> None:
        self._log(msg, "INFO")

    def warning(self, msg: str) -> None:
        self._log(msg, "WARN")

    def error(self, msg: str) -> None:
        self._log(msg, "ERROR")

    def debug(self, msg: str) -> None:
        self._log(msg, "DEBUG")

    def _log(self, msg: str, level: str) -> None:
        ts = time.strftime("%H:%M:%S")
        tag = f"[{self.name}] " if self.name else ""
        line = f"{ts} {tag}{level}  {msg}"
        print(line)
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
