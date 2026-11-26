import pytest
from soar_sdk.exceptions import ActionFailure

from src import handlers as h

OSL = "5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8"
FWD = {"items": [{"guid": OSL, "hostname": "hf-osl-0001", "label": "hf-osl-0001"}]}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("120344", 120344),
        (1.0, 1),
        (7, 7),
        ("", None),
        (None, None),
        ("x", None),
        (True, None),
    ],
)
def test_to_int(value, expected):
    assert h.to_int(value) == expected


def test_clean_job_id_strips():
    assert h.clean_job_id("  20261125-101500-3fa9c2 \n") == "20261125-101500-3fa9c2"


@pytest.mark.parametrize("value", ["a/b", " ", "", None, "../jobs", "a?b"])
def test_clean_job_id_rejects(value):
    with pytest.raises(ActionFailure, match="job_id"):
        h.clean_job_id(value)


def test_enrich_job_final_and_search():
    job = h.enrich_job(
        {"job_id": "J", "status": "incomplete", "events_indexed": "12", "earliest": 1.0}
    )
    assert job["is_final"] is True
    assert job["search"] == "promote_job=J"
    assert job["events_indexed"] == 12
    assert job["earliest"] == 1
    assert h.enrich_job({"job_id": "J", "status": "running"})["is_final"] is False


def test_get_job(api):
    client, rec = api(
        {
            ("GET", "jobs/J1"): (
                200,
                {
                    "job_id": "J1",
                    "status": "running",
                    "agents_expected": 10,
                    "agents_reported": 3,
                },
            )
        }
    )
    job, message = h.get_job(client, " J1 ")
    assert job["is_final"] is False
    assert job["search"] == "promote_job=J1"
    assert message == "Job J1 is running (3/10 forwarders reported)"
    assert rec.requests[0].url.path.endswith("/jobs/J1")


def test_get_job_not_found(api):
    client, _ = api({})
    with pytest.raises(ActionFailure, match="404"):
        h.get_job(client, "nope")


def test_verify_job_sums_events(api):
    client, _ = api(
        {
            ("GET", "jobs/J/verify"): (
                200,
                {
                    "items": [
                        {
                            "index": "promoted",
                            "sourcetype": "splunkd",
                            "host": "a",
                            "count": "100",
                        },
                        {
                            "index": "promoted",
                            "sourcetype": "splunkd",
                            "host": "b",
                            "count": "20",
                        },
                    ],
                    "events": 120,
                },
            )
        }
    )
    rows, events = h.verify_job(client, "J")
    assert events == 120
    assert [r["count"] for r in rows] == [100, 20]


def test_list_jobs_caps_limit(api):
    client, rec = api(
        {("GET", "jobs"): (200, {"items": [{"job_id": "J", "status": "completed"}]})}
    )
    jobs = h.list_jobs(client, "completed", 5000)
    assert dict(rec.requests[0].url.params) == {"status": "completed", "limit": "1000"}
    assert jobs[0]["is_final"] is True


def test_list_forwarders_filters_sourcetype(api):
    client, rec = api(
        {
            ("GET", "forwarders"): (
                200,
                {"items": [{"guid": OSL, "bytes": "5", "sourcetypes": ["splunkd"]}]},
            )
        }
    )
    rows = h.list_forwarders(client, "splunkd")
    assert rec.requests[0].url.params["sourcetype"] == "splunkd"
    assert rows[0]["bytes"] == 5


def test_get_job_tasks(api):
    client, _ = api(
        {
            ("GET", "jobs/J/tasks"): (
                200,
                {
                    "items": [
                        {
                            "agent_host": "hf-osl-0001",
                            "status": "done",
                            "files_done": "3",
                        }
                    ]
                },
            )
        }
    )
    assert h.get_job_tasks(client, "J")[0]["files_done"] == 3
