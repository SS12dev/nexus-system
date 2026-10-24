import json
import logging

from nexus_common.logs import JsonFormatter, TextFormatter, configure_logging, request_id


def record(**extra) -> logging.LogRecord:
    rec = logging.makeLogRecord({"name": "nexus.test", "levelname": "INFO", "msg": "access.%s"})
    rec.args = ("approved",)
    rec.__dict__.update(extra)
    return rec


def test_json_lines_carry_request_id_and_fields():
    token = request_id.set("abc123")
    try:
        line = JsonFormatter("api").format(record(access_request_id="r1", attempt=2))
    finally:
        request_id.reset(token)
    out = json.loads(line)
    assert out["msg"] == "access.approved" and out["service"] == "api"
    assert out["request_id"] == "abc123"
    assert out["access_request_id"] == "r1" and out["attempt"] == 2


def test_text_lines_append_fields():
    line = TextFormatter().format(record(kind="requested"))
    assert line.endswith("access.approved kind=requested")


def test_configure_replaces_only_its_own_handler(settings):
    root = logging.getLogger()
    before = [h for h in root.handlers if not getattr(h, "_nexus", False)]
    configure_logging(settings.model_copy(update={"log_format": "json"}), "api")
    configure_logging(settings, "api")
    ours = [h for h in root.handlers if getattr(h, "_nexus", False)]
    assert len(ours) == 1 and isinstance(ours[0].formatter, TextFormatter)
    assert [h for h in root.handlers if not getattr(h, "_nexus", False)] == before
