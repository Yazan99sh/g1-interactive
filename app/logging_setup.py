"""Logging configuration.

Goal (per requirements): *every* error lands in a log file so any issue can be
reviewed after the fact. We configure three sinks:

* console            — INFO+ (what you watch live)
* logs/g1.log        — DEBUG+ rotating (full detail, the firehose)
* logs/errors.log    — ERROR+ rotating (just the problems, with tracebacks)

Use ``from app.logging_setup import get_logger`` in every module and log freely.
``log_exception(logger, msg)`` is a helper that always records the traceback.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_CONFIGURED = False

_FMT = "%(asctime)s  %(levelname)-7s  %(name)-22s  %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    """Initialise root logging. Safe to call once at startup."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers do the filtering
    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

    # Console — what the operator watches.
    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(formatter)
    root.addHandler(console)

    # Full detail log (rotating, 5 x 5 MB).
    full = logging.handlers.RotatingFileHandler(
        log_dir / "g1.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    full.setLevel(logging.DEBUG)
    full.setFormatter(formatter)
    root.addHandler(full)

    # Errors-only log (rotating) — the file you check first when something breaks.
    errors = logging.handlers.RotatingFileHandler(
        log_dir / "errors.log", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    errors.setLevel(logging.ERROR)
    errors.setFormatter(formatter)
    root.addHandler(errors)

    # Tame noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Make sure uncaught exceptions reach the error log too.
    def _excepthook(exc_type, exc_value, exc_tb):
        logging.getLogger("uncaught").critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_exception(logger: logging.Logger, message: str) -> None:
    """Log ``message`` at ERROR with the active exception's full traceback."""
    logger.error(message, exc_info=True)
