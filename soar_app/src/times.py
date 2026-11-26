"""Times for the API: epoch seconds and Splunk time modifiers pass through, ISO 8601 becomes epoch."""

import re
from datetime import UTC, datetime

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_EPOCH = re.compile(r"^\d+(\.\d+)?$")
# Epoch values above this are milliseconds (CEF end/rt); in seconds it is the year 5138.
MS_THRESHOLD = 100_000_000_000


def _epoch(seconds: float) -> str:
    if seconds > MS_THRESHOLD:
        seconds /= 1000
    return str(int(seconds))


def normalize_time(value: str | float | None, default: str | None = None) -> str | None:
    """Return the time as the API takes it. A naive ISO timestamp is taken as UTC; epoch
    milliseconds become seconds."""
    if value is None:
        return default
    if isinstance(value, int | float):
        return _epoch(value)
    text = value.strip()
    if not text:
        return default
    if _EPOCH.match(text):
        return _epoch(float(text))
    if _ISO.match(text):
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return str(int(parsed.timestamp()))
    return text
