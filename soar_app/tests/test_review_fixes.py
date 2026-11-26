import pytest

from src import handlers as h
from src.app import app
from src.client import PromotionClient
from src.times import normalize_time

OSL = "5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8"
FWD = (
    200,
    {"items": [{"guid": OSL, "hostname": "hf-osl-0001", "label": "hf-osl-0001"}]},
)


def test_every_action_declares_its_summary():
    missing = [
        m.action
        for m in app.actions_manager.get_actions_meta_list()
        if m.action != "test connectivity" and m.summary_type is None
    ]
    assert missing == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1795601700000", "1795601700"),
        (1795601700123, "1795601700"),
        ("1795601700", "1795601700"),
    ],
)
def test_epoch_milliseconds_become_seconds(value, expected):
    assert normalize_time(value) == expected


def test_dry_run_has_no_job_id(api):
    answer = (
        200,
        {
            "job": {"job_id": "J2", "status": "pending"},
            "dry_run": True,
            "estimate": {"forwarders": 1, "bytes": 5, "over_limit": []},
            "warnings": [],
        },
    )
    client, _ = api({("GET", "forwarders"): FWD, ("POST", "jobs"): answer})
    out = h.promote(
        client,
        sourcetype="splunkd",
        forwarders="hf-osl-0001",
        earliest="-1h",
        dry_run=True,
    )
    assert out["job_id"] is None


@pytest.mark.parametrize(
    "base",
    [
        "https://sh1:8089/services/data_promotion",
        "https://sh1:8089/services/data_promotion/",
    ],
)
def test_base_url_with_api_path_is_tolerated(base):
    assert PromotionClient(base, "tok").base_url == "https://sh1:8089"
