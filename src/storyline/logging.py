import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler

_run_id: str | None = None
_log_dir: Path = Path("logs")


class _RunIdFilter(logging.Filter):
    def filter(self, record):
        record.run_id = _run_id or "-"
        return True


def init(run_id: str | None = None, log_dir: str | Path = "logs") -> str:
    global _run_id, _log_dir
    _run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("storyline")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s run_id=%(run_id)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Console: INFO+ only
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    console.addFilter(_RunIdFilter())
    logger.addHandler(console)

    # File: DEBUG+ with rotation
    log_path = _log_dir / f"server_{_run_id}.log"
    file_handler = RotatingFileHandler(log_path, maxBytes=10_000_000, backupCount=5)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_RunIdFilter())
    logger.addHandler(file_handler)

    # Symlink for convenience: logs/server.log -> latest run
    latest = _log_dir / "server.log"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(log_path.name)

    return _run_id


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"storyline.{name}")