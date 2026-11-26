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
CREATED = (
    201,
    {
        "job": {
            "job_id": "J1",
            "status": "pending",
            "forwarders": OSL,
            "agents_expected": 1,
        },
        "estimate": {"forwarders": 1, "bytes": 412000000, "over_limit": []},
        "warnings": [],
    },
)


def test_promote_sends_resolved_guids(api):
    client, rec = api({("GET", "forwarders"): FWD, ("POST", "jobs"): CREATED})
    out = h.promote(
        client,
        sourcetype="splunkd",
        forwarders="hf-osl-0001",
        earliest="2026-11-25T10:00:00Z",
        latest=None,
    )
    assert rec.bodies() == [
        {
            "sourcetype": "splunkd",
            "forwarders": [OSL],
            "earliest": "1795600800",
            "latest": "now",
            "dry_run": False,
            "reject_over_limit": False,
        }
    ]
    assert out["job_id"] == "J1"
    assert out["dry_run"] is False
    assert out["estimate_bytes"] == 412000000
    assert out["estimate_forwarders"] == 1
    assert out["resolved"] == [
        {"value": "hf-osl-0001", "guid": OSL, "hostname": "hf-osl-0001"}
    ]
    assert out["job"]["search"] == "promote_job=J1"
    assert out["message"] == "Created job J1 from 1 forwarder(s)"


def test_promote_optional_fields(api):
    client, rec = api({("GET", "forwarders"): FWD, ("POST", "jobs"): CREATED})
    h.promote(
        client,
        sourcetype="splunkd",
        forwarders="hf-osl-0001",
        earliest="-24h@h",
        latest="-1h@h",
        index_filter="fw_*",
        target_index="promoted",
        reject_over_limit=True,
    )
    body = rec.bodies()[0]
    assert body["index_filter"] == "fw_*"
    assert body["target_index"] == "promoted"
    assert body["latest"] == "-1h@h"
    assert body["reject_over_limit"] is True


def test_promote_star_sends_star(api):
    client, rec = api({("POST", "jobs"): CREATED})
    h.promote(client, sourcetype="splunkd", forwarders="*", earliest="-1h")
    assert rec.bodies()[0]["forwarders"] == "*"
    assert [r.method for r in rec.requests] == ["POST"]


def test_promote_unknown_host_never_posts(api):
    client, rec = api({("GET", "forwarders"): FWD, ("POST", "jobs"): CREATED})
    with pytest.raises(ActionFailure, match="hf-nope"):
        h.promote(client, sourcetype="splunkd", forwarders="hf-nope", earliest="-1h")
    assert all(r.method == "GET" for r in rec.requests)


def test_promote_requires_earliest(api):
    client, rec = api({("GET", "forwarders"): FWD})
    with pytest.raises(ActionFailure, match="earliest"):
        h.promote(client, sourcetype="splunkd", forwarders="hf-osl-0001", earliest=" ")
    assert rec.requests == []


def test_promote_dry_run(api):
    answer = (
        200,
        {
            "job": {"job_id": "J2", "status": "pending"},
            "dry_run": True,
            "estimate": {"forwarders": 2, "bytes": 2048, "over_limit": []},
            "warnings": [],
        },
    )
    client, rec = api({("GET", "forwarders"): FWD, ("POST", "jobs"): answer})
    out = h.promote(
        client,
        sourcetype="splunkd",
        forwarders="hf-osl-0001 hf-ber-0001",
        earliest="-1h",
        dry_run=True,
    )
    assert rec.bodies()[0]["dry_run"] is True
    assert out["dry_run"] is True
    assert "nothing created" in out["message"]
    assert "2 forwarder(s)" in out["message"]


def test_promote_warnings_surface(api):
    answer = (
        201,
        {
            **CREATED[1],
            "warnings": ["hf-osl-0001 would read 58 GB, over its 50 GB limit"],
        },
    )
    client, _ = api({("GET", "forwarders"): FWD, ("POST", "jobs"): answer})
    out = h.promote(
        client, sourcetype="splunkd", forwarders="hf-osl-0001", earliest="-1h"
    )
    assert out["warnings"] == ["hf-osl-0001 would read 58 GB, over its 50 GB limit"]
    assert out["message"].endswith("; 1 warning(s)")


def test_estimate_query(api):
    answer = (
        200,
        {
            "earliest": 1,
            "latest": 2,
            "seconds": 1,
            "forwarders": 2,
            "bytes": 61200000000,
            "over_limit": [{"guid": OSL, "name": "hf-osl-0001", "bytes": 58000000000}],
        },
    )
    client, rec = api({("GET", "forwarders"): FWD, ("GET", "estimate"): answer})
    out = h.estimate(
        client,
        sourcetype="splunkd",
        forwarders="hf-osl-0001, hf-ber-0001",
        earliest="2026-11-25T10:00:00Z",
        latest=None,
    )
    params = dict(rec.requests[-1].url.params)
    assert params == {
        "sourcetype": "splunkd",
        "forwarders": f"{OSL},{BER}",
        "earliest": "1795600800",
        "latest": "now",
    }
    assert out["over_limit_count"] == 1
    assert out["over_limit_names"] == ["hf-osl-0001"]
    assert out["bytes"] == 61200000000
    assert out["forwarders"] == 2
    assert len(out["resolved"]) == 2


def test_estimate_star_omits_forwarders(api):
    client, rec = api(
        {("GET", "estimate"): (200, {"forwarders": 3, "bytes": 1, "over_limit": []})}
    )
    h.estimate(client, sourcetype="splunkd", forwarders="*", earliest="-1h")
    assert "forwarders" not in rec.requests[0].url.params


def test_cancel(api):
    client, _ = api(
        {
            ("POST", "jobs/J/cancel"): (
                200,
                {"job_id": "J", "status": "cancel_requested"},
            )
        }
    )
    job, message = h.cancel_job(client, "J")
    assert job["status"] == "cancel_requested"
    assert job["is_final"] is False
    assert message.startswith("Job J is cancel_requested")


def test_cancel_conflict(api):
    client, _ = api(
        {("POST", "jobs/J/cancel"): (409, {"error": "job is already completed"})}
    )
    with pytest.raises(ActionFailure, match="409 job is already completed"):
        h.cancel_job(client, "J")
