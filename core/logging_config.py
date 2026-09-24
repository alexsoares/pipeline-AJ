"""Configuração de logs compartilhada pela CLI e pela interface web."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from core.settings import PROJECT_ROOT, LoggingSettings


def setup_logging(settings: LoggingSettings) -> None:
    log_file = Path(settings.file)
    if not log_file.is_absolute():
        log_file = PROJECT_ROOT / log_file
    log_file.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logging.basicConfig(level=settings.level.upper(), handlers=[file_handler, console_handler])
