from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dgov.core.config import settings

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def setup_logging() -> None:
    """Configure stdout plus a size-bounded rotating application log file.

    ``RotatingFileHandler`` caps each file at ``LOG_FILE_MAX_BYTES`` and keeps a
    fixed number of backups, so a runaway log cannot fill the disk.  Operational
    events additionally live in MySQL and are pruned by ``services.logs``.
    """

    root = logging.getLogger()
    if any(
        isinstance(handler, RotatingFileHandler) for handler in root.handlers
    ):
        return

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    file_handler = RotatingFileHandler(
        log_dir / "dgov.log",
        maxBytes=settings.log_file_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if not any(type(handler) is logging.StreamHandler for handler in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)
    root.setLevel(logging.INFO)
