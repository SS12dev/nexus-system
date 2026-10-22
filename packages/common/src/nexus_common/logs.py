"""Logging for every Nexus service: plain text for a terminal, or one JSON object per line for a
log collector (LOG_FORMAT=json). Each line carries the id of the request it belongs to, so the
lines for one failing request can be found together; the API returns the same id to the browser
in the X-Request-ID header.

Pass structured fields with `extra`, e.g. `log.info("access.requested", extra={"email": e})`;
JSON output keeps them as keys and text output appends them as key=value.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from nexus_common.settings import Settings

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else on a record came from `extra`.
_STANDARD = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def _extras(record: logging.LogRecord) -> dict:
    return {k: v for k, v in vars(record).items() if k not in _STANDARD and not k.startswith("_")}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if rid := request_id.get():
            out["request_id"] = rid
        out.update(_extras(record))
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = _extras(record)
        if rid := request_id.get():
            extras = {"request_id": rid, **extras}
        if extras:
            line += " " + " ".join(f"{k}={v}" for k, v in extras.items())
        return line


def configure_logging(settings: Settings, service: str) -> None:
    """Install Nexus's handler on the root logger, replacing one installed earlier. Other
    handlers (pytest's, for instance) are left alone."""
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_nexus", False)]:
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler._nexus = True  # type: ignore[attr-defined]
    json_logs = settings.log_format == "json"
    handler.setFormatter(JsonFormatter(service) if json_logs else TextFormatter())
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
