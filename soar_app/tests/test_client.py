import httpx
import pytest

from src.client import ApiError, PromotionClient


def test_sends_bearer_and_builds_url(api):
    client, rec = api({("GET", "jobs"): (200, {"items": []})}, base="https://sh1:8089/")
    assert client.get("jobs", {"limit": 1}) == {"items": []}
    req = rec.requests[0]
    assert str(req.url) == "https://sh1:8089/services/data_promotion/jobs?limit=1"
    assert req.headers["Authorization"] == "Bearer tok"


def test_post_sends_json(api):
    client, rec = api({("POST", "jobs"): (201, {"job": {"job_id": "J"}})})
    assert client.post("jobs", {"sourcetype": "splunkd"}) == {"job": {"job_id": "J"}}
    assert rec.requests[0].headers["Content-Type"] == "application/json"
    assert rec.bodies() == [{"sourcetype": "splunkd"}]


def test_api_error_carries_status_and_field(api):
    client, _ = api(
        {
            ("POST", "jobs"): (
                400,
                {
                    "error": "no forwarder holds data for this sourcetype",
                    "field": "sourcetype",
                },
            )
        }
    )
    with pytest.raises(ApiError) as e:
        client.post("jobs", {})
    assert e.value.status == 400
    assert e.value.field == "sourcetype"
    msg = e.value.message
    assert "400" in msg
    assert "no forwarder holds data" in msg
    assert "sourcetype" in msg


def test_non_json_error_body(api):
    html = "<html>" + "x" * 1000 + "</html>"
    client, _ = api({("GET", "jobs"): (502, html)})
    with pytest.raises(ApiError) as e:
        client.get("jobs")
    assert e.value.status == 502
    assert "502" in e.value.message
    assert len(e.value.message) < 260


def test_connection_error():
    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    client = PromotionClient(
        "https://sh1:8089", "tok", transport=httpx.MockTransport(boom)
    )
    with pytest.raises(ApiError) as e:
        client.get("jobs")
    assert e.value.status is None
    assert "Cannot reach" in e.value.message
    assert "https://sh1:8089" in e.value.message


@pytest.mark.parametrize(("status", "hint"), [(401, "token"), (403, "promote_data")])
def test_auth_errors_explained(api, status, hint):
    client, _ = api({("GET", "jobs"): (status, {"error": "denied"})})
    with pytest.raises(ApiError) as e:
        client.get("jobs")
    assert hint in e.value.message
