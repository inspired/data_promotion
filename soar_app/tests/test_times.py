import pytest

from src.times import normalize_time


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-25T10:15:00Z", "1795601700"),
        ("2026-11-25T10:15:00+01:00", "1795598100"),
        ("2026-11-25T10:15:00", "1795601700"),  # naive: UTC
        ("2026-11-25T10:15:00.250Z", "1795601700"),
        ("1795601700", "1795601700"),
        (1795601700, "1795601700"),
        (1795601700.0, "1795601700"),
        ("-24h@h", "-24h@h"),
        ("  now ", "now"),
    ],
)
def test_normalize(value, expected):
    assert normalize_time(value) == expected


@pytest.mark.parametrize("value", ["", "  ", None])
def test_empty_uses_default(value):
    assert normalize_time(value, default="now") == "now"
    assert normalize_time(value) is None
