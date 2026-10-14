import json
import logging

from py_telemetry import JsonFormatter


def test_formats_record_as_json_with_extra_fields() -> None:
    record = logging.LogRecord("agent.consumer", logging.INFO, __file__, 1, "got %s", ("x",), None)
    record.investigation_id = "inv-1"
    entry = json.loads(JsonFormatter("agent").format(record))
    assert entry["service"] == "agent"
    assert entry["level"] == "info"
    assert entry["msg"] == "got x"
    assert entry["investigation_id"] == "inv-1"
