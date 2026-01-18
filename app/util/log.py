import json
import logging
import logging.handlers
import pathlib
from typing import Any

import structlog


def setup_logging(
    log_level: str = "INFO",
    log_format: str = "text",
    log_file: str | None = None,
    config_dir: str = "/config",
) -> None:
    """
    Configure structured logging with optional JSON output and file rotation.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_format: Log format ("text" or "json")
        log_file: Optional path to log file (relative to config_dir)
        config_dir: Base configuration directory
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    # Build list of processors based on format
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    
    if log_format.lower() == "json":
        processors.append(structlog.processors.JSONRenderer())
        logger_factory = structlog.PrintLoggerFactory()
    else:
        processors.append(structlog.dev.ConsoleRenderer())
        logger_factory = structlog.PrintLoggerFactory()
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=logger_factory,
        cache_logger_on_first_use=False,
    )
    
    # Configure root logger with optional file handler
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers
    root_logger.handlers = []
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)
    
    # File handler (optional)
    if log_file:
        log_path = pathlib.Path(config_dir) / "logs"
        log_path.mkdir(parents=True, exist_ok=True)
        
        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_path / log_file,
            maxBytes=100 * 1024 * 1024,  # 100MB
            backupCount=5,
        )
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)


def get_logger() -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    return structlog.stdlib.get_logger()


logger = get_logger()
