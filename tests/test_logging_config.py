import logging
from pathlib import Path

import src.utils.logging as logging_utils


def test_configured_library_loggers_receive_their_levels(tmp_path, monkeypatch):
    config_path = Path(tmp_path) / "logging.yaml"
    config_path.write_text(
        """level: WARNING
loggers:
  httpx: ERROR
  httpcore.http11: INFO
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("LOGGING_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(logging_utils, "_CONFIG_CACHE", None)
    monkeypatch.setattr(logging_utils, "_HANDLERS_CONFIGURED", False)

    logging_utils.logging_provider("src.test_logging_config")

    assert logging.getLogger().level == logging.WARNING
    assert logging.getLogger("httpx").getEffectiveLevel() == logging.ERROR
    assert logging.getLogger("httpcore.http11").getEffectiveLevel() == logging.INFO
    assert logging.getLogger("httpcore.http11.connection").getEffectiveLevel() == logging.INFO