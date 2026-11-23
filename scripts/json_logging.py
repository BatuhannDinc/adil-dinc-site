"""One JSON object per log line, shared by every script so log shippers can parse them uniformly."""
import json
import logging


class JsonFormatter(logging.Formatter):
    def __init__(self, component: str):
        super().__init__()
        self.component = component

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": self.formatTime(record),
            "severity": record.levelname,
            "component": self.component,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        # json.dumps escapes quotes and newlines that a printf-style format string would let through.
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(component: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(component))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
