import logging
from logging import getLogger
from typing import Optional, Dict, Any
from colorama import Fore, Style, init
import sys
import os
import copy
import yaml

_CONFIG_ENV_VAR = "LOGGING_CONFIG_PATH"
_DEFAULT_CONFIG = {
    "level": "INFO",
    "file": "grpc_server.log",
    "loggers": {},
}

_CONFIG_CACHE: Optional[Dict[str, Any]] = None
_HANDLERS_CONFIGURED = False


class ColoredFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.MAGENTA,
    }

    def format(self, record):
        levelname = record.levelname[0]  # First letter only
        color = self.COLORS.get(record.levelname, "")

        if record.levelname == "DEBUG":
            record.levelname = f"{Style.BRIGHT}{color}{levelname}{Style.RESET_ALL}"
            formatted = super().format(record)
            parts = formatted.split(": ", 1)
            if len(parts) == 2:
                return (
                    Style.BRIGHT
                    + parts[0]
                    + Style.RESET_ALL
                    + ": "
                    + Style.DIM
                    + Fore.WHITE
                    + parts[1]
                    + Style.RESET_ALL
                )
            return formatted

        record.levelname = f"{Style.BRIGHT}{color}{levelname}{Style.RESET_ALL}"
        formatted = super().format(record)
        parts = formatted.split(": ", 1)
        if len(parts) == 2:
            return (
                Style.BRIGHT
                + parts[0]
                + Style.RESET_ALL
                + ": "
                + Fore.LIGHTBLACK_EX
                + parts[1]
                + Style.RESET_ALL
            )
        return formatted


def _normalize_level(level: Optional[str]) -> int:
    if not level:
        return logging.INFO
    level_value = logging.getLevelName(str(level).upper())
    return level_value if isinstance(level_value, int) else logging.INFO


def _apply_logger_levels(config: Dict[str, Any]) -> None:
    """Apply configured logger levels to all named loggers (including third-party ones).
    
    This must be called as soon as the config is available, before other code
    has a chance to emit logs. It applies levels to loggers that already exist
    and will be inherited by child loggers.
    """
    for logger_name, logger_level in config.get("loggers", {}).items():
        logging.getLogger(logger_name).setLevel(logger_level)


def _load_config() -> Dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    config = copy.deepcopy(_DEFAULT_CONFIG)
    config_path = os.getenv(_CONFIG_ENV_VAR, "logging.yaml")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
            if isinstance(raw, dict) and "logging" in raw:
                raw = raw["logging"]
            if isinstance(raw, dict):
                config.update(raw)
        except Exception as exc:
            print(f"Failed to read logging config {config_path}: {exc}", file=sys.stderr)

    config["level"] = _normalize_level(config.get("level"))
    loggers = config.get("loggers") or {}
    normalized_loggers: Dict[str, int] = {}
    if isinstance(loggers, dict):
        for name, level in loggers.items():
            if not isinstance(name, str):
                continue
            normalized_loggers[name] = _normalize_level(level)
    config["loggers"] = normalized_loggers

    # Apply logger levels immediately so third-party loggers (like httpx)
    # are configured before they emit their first log
    _apply_logger_levels(config)

    _CONFIG_CACHE = config
    return _CONFIG_CACHE


def _configure_handlers() -> None:
    global _HANDLERS_CONFIGURED
    if _HANDLERS_CONFIGURED:
        return

    init(autoreset=False)
    config = _load_config()

    root = logging.getLogger()
    root.setLevel(config.get("level", logging.INFO))

    formatter = logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s")

    log_file = config.get("file") or "grpc_server.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    stdout_formatter = ColoredFormatter(
        "%(levelname)s %(asctime)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.DEBUG)
    stdout_handler.setFormatter(stdout_formatter)

    root.addHandler(file_handler)
    root.addHandler(stdout_handler)

    _HANDLERS_CONFIGURED = True


def _get_effective_level(logger_name: str) -> int:
    config = _load_config()
    loggers = config.get("loggers", {})

    best_match = None
    for name, level in loggers.items():
        if logger_name == name or logger_name.startswith(f"{name}."):
            if best_match is None or len(name) > len(best_match[0]):
                best_match = (name, level)

    if best_match is not None:
        return best_match[1]
    return config.get("level", logging.INFO)


def logging_provider(
    file: str,
    cls_instance: Optional[object] = None,
    *,
    prefix: Optional[str] = None,
) -> logging.Logger:
    """provides a logger for the given file and class name

    Args:
        file: typically the module ``__name__``; becomes the leading
            segment of the logger name.
        cls_instance: when given, the class qualname is appended to the
            logger name (e.g. ``"SharingService"``).
        prefix: optional short tag wrapped in ``[ ]`` and prepended
            to the logger name.  Useful for tagging logs from a
            sub-component -- e.g. ``prefix="sharing facade"`` yields
            ``"[sharing facade] src.services.sharing.SharingService"``.
    """
    _configure_handlers()

    logger_name = f"{file}"
    if cls_instance:
        # what i wanted with .__name__ is just the name. but this also has the prefix. 
        # hence I tried __qualname__ but that gives the full path as well. Now I just split
        # and take the last part
        logger_name += f".{cls_instance.__class__.__name__.split('.')[-1]}"
    if prefix:
        logger_name = f"[{prefix}] {logger_name}"

    log = getLogger(logger_name)
    log.setLevel(_get_effective_level(logger_name))
    return log