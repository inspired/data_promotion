import pytest
from soar_sdk.exceptions import ActionFailure

from src import handlers as h

OSL = "5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8"
BER = "B2000000-0000-0000-0000-000000000002"
FWD = (
    200,
    {
        "items": [
            {"guid": OSL, "hostname": "hf-osl-0001", "label": "hf-osl-0001"},
            {"guid": BER, "hostname": "hf-ber-0001", "label": "hf-ber-0001"},
        ]
    },
)
ROW = {
    "guid": OSL,
    "hostname": "hf-osl-0001",
    "label": "hf-osl-0001",
    "status": "reporting",
    "sourcetype": "splunkd",
    "agents": [OSL],
    "shared_store": False,
    "days": 2.0,
    "files": 48.0,
    "bytes": 900.0,
    "earliest": 1795564800.0,
    "latest": 1795651199.0,
    "updated_at": 5.0,
}


def test_asks_for_rows_per_forwarder(api):
    client, rec = api({("GET", "catalog"): (200, {"items": [ROW]})})
    rows = h.list_data(client, "splunkd", None)
    assert dict(rec.requests[0].url.params) == {
        "by": "forwarder",
        "sourcetype": "splunkd",
    }
    assert rows[0]["hostname"] == "hf-osl-0001"
    assert rows[0]["guid"] == OSL
    assert (rows[0]["days"], rows[0]["files"], rows[0]["bytes"]) == (2, 48, 900)
    assert rows[0]["agents"] == [OSL]
    assert rows[0]["shared_store"] is False


def test_forwarders_resolved_to_guids(api):
    client, rec = api(
        {("GET", "forwarders"): FWD, ("GET", "catalog"): (200, {"items": [ROW]})}
    )
    h.list_data(client, None, "hf-osl-0001, HF-BER-0001")
    assert dict(rec.requests[-1].url.params) == {
        "by": "forwarder",
        "forwarder": f"{OSL},{BER}",
    }


def test_star_lists_every_forwarder(api):
    client, rec = api({("GET", "catalog"): (200, {"items": []})})
    assert h.list_data(client, None, "*") == []
    assert dict(rec.requests[0].url.params) == {"by": "forwarder"}


def test_search_head_before_3_1_is_named(api):
    old = {
        "items": [
            {
                "sourcetype": "splunkd",
                "day": "2026-11-25",
                "files": "24",
                "forwarders": "3",
            }
        ]
    }
    client, _ = api({("GET", "catalog"): (200, old)})
    with pytest.raises(ActionFailure, match=r"promotion_control 3\.1\.0"):
        h.list_data(client, None, None)
