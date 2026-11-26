"""What each action does, over a PromotionClient. Returns plain dicts; app.py maps them to outputs."""

import re
from dataclasses import asdict
from typing import Any

from soar_sdk.exceptions import ActionFailure

from .client import PromotionClient
from .forwarders import resolve_with_client, split_values
from .times import normalize_time

FINAL_STATUSES = frozenset({"completed", "incomplete", "failed", "cancelled"})
MAX_LIMIT = 1000

# Fields the API returns as numbers: KV store rows give floats, searches give strings.
NUMERIC = frozenset(
    {
        "earliest",
        "latest",
        "seconds",
        "files",
        "bytes",
        "days",
        "last_seen",
        "max_bytes_per_job",
        "forwarders_count",
        "agents_expected",
        "agents_reported",
        "agents_running",
        "agents_failed",
        "agents_cancelled",
        "files_total",
        "files_done",
        "bytes_read",
        "events_staged",
        "events_skipped",
        "events_bad",
        "events_indexed",
        "submitted_at",
        "started_at",
        "finished_at",
        "completed_at",
        "updated_at",
        "count",
        "limit",
    }
)
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def numbers(row: dict, extra: frozenset[str] = frozenset()) -> dict:
    """The row with its numeric fields as ints."""
    return {k: to_int(v) if k in NUMERIC or k in extra else v for k, v in row.items()}


def clean_job_id(value: str | None) -> str:
    job_id = (value or "").strip()
    if not _JOB_ID.match(job_id):
        raise ActionFailure(f"job_id is not a valid job ID: {value!r}")
    return job_id


def enrich_job(job: dict) -> dict:
    job = numbers(job)
    job["is_final"] = job.get("status") in FINAL_STATUSES
    job["search"] = f"promote_job={job.get('job_id')}"
    return job


def job_message(job: dict) -> str:
    return (
        f"Job {job.get('job_id')} is {job.get('status')} "
        f"({job.get('agents_reported') or 0}/{job.get('agents_expected') or 0} forwarders reported)"
    )


def _items(answer: dict) -> list[dict]:
    return answer.get("items") or []


# ---- read ----


def list_forwarders(client: PromotionClient, sourcetype: str | None) -> list[dict]:
    params = {"sourcetype": sourcetype} if sourcetype else None
    return [numbers(r) for r in _items(client.get("forwarders", params))]


def list_data(
    client: PromotionClient, sourcetype: str | None, forwarder: str | None
) -> list[dict]:
    """What each forwarder holds, per sourcetype (promotion_control 3.1.0: catalog?by=forwarder)."""
    params: dict[str, Any] = {"by": "forwarder"}
    if sourcetype:
        params["sourcetype"] = sourcetype
    if split_values(forwarder) not in ([], ["*"]):
        guids, _ = resolve_with_client(client, forwarder)
        params["forwarder"] = ",".join(guids)
    rows = _items(client.get("catalog", params))
    if any("guid" not in r for r in rows):
        # Before 3.1.0 the API ignores "by" and adds up all forwarders per day.
        raise ActionFailure(
            "list data needs promotion_control 3.1.0 or later on the Search Head"
        )
    return [numbers(r) for r in rows]


def list_jobs(
    client: PromotionClient, status: str | None, limit: int | None
) -> list[dict]:
    params: dict[str, Any] = {"limit": max(1, min(limit or 50, MAX_LIMIT))}
    if status:
        params = {"status": status, **params}
    return [enrich_job(j) for j in _items(client.get("jobs", params))]


def get_job(client: PromotionClient, job_id: str) -> tuple[dict, str]:
    job = enrich_job(client.get(f"jobs/{clean_job_id(job_id)}"))
    return job, job_message(job)


def get_job_tasks(client: PromotionClient, job_id: str) -> list[dict]:
    return [
        numbers(r) for r in _items(client.get(f"jobs/{clean_job_id(job_id)}/tasks"))
    ]


def verify_job(client: PromotionClient, job_id: str) -> tuple[list[dict], int]:
    answer = client.get(f"jobs/{clean_job_id(job_id)}/verify")
    rows = [numbers(r) for r in _items(answer)]
    events = to_int(answer.get("events"))
    if events is None:
        events = sum(r.get("count") or 0 for r in rows)
    return rows, events


# ---- write ----


def _selection(
    client: PromotionClient, forwarders: str | None, earliest: Any, latest: Any
) -> tuple[list[str], list[dict], str, str]:
    """Resolve forwarders and times before anything is sent; fail without calling the API."""
    start = normalize_time(earliest)
    if not start:
        raise ActionFailure(
            "earliest is required: epoch seconds, ISO 8601 or a Splunk time such as -24h@h"
        )
    end = normalize_time(latest, default="now")
    guids, resolved = resolve_with_client(client, forwarders)
    return guids, [asdict(r) for r in resolved], start, end


def estimate(
    client: PromotionClient,
    sourcetype: str,
    forwarders: str | None,
    earliest: Any,
    latest: Any = None,
) -> dict:
    guids, resolved, start, end = _selection(client, forwarders, earliest, latest)
    params: dict[str, Any] = {"sourcetype": sourcetype}
    if guids != ["*"]:
        params["forwarders"] = ",".join(guids)
    params.update(earliest=start, latest=end)
    est = numbers(client.get("estimate", params), frozenset({"forwarders"}))
    over = est.get("over_limit") or []
    return {
        **est,
        "over_limit_count": len(over),
        "over_limit_names": [o.get("name") or o.get("guid") for o in over],
        "resolved": resolved,
    }


def promote(
    client: PromotionClient,
    sourcetype: str,
    forwarders: str | None,
    earliest: Any,
    latest: Any = None,
    index_filter: str | None = None,
    target_index: str | None = None,
    dry_run: bool = False,
    reject_over_limit: bool = False,
) -> dict:
    guids, resolved, start, end = _selection(client, forwarders, earliest, latest)
    body: dict[str, Any] = {
        "sourcetype": sourcetype,
        "forwarders": "*" if guids == ["*"] else guids,
        "earliest": start,
        "latest": end,
    }
    if index_filter:
        body["index_filter"] = index_filter
    if target_index:
        body["target_index"] = target_index
    body.update(dry_run=bool(dry_run), reject_over_limit=bool(reject_over_limit))

    answer = client.post("jobs", body)
    job = enrich_job(answer.get("job") or {})
    est = numbers(answer.get("estimate") or {}, frozenset({"forwarders"}))
    warnings = [str(w) for w in answer.get("warnings") or []]
    is_dry = bool(answer.get("dry_run", dry_run))
    n = est.get("forwarders") or job.get("agents_expected") or len(guids)
    if is_dry:
        message = f"Dry run: job would read {est.get('bytes') or 0} bytes from {n} forwarder(s); nothing created"
    else:
        message = f"Created job {job.get('job_id')} from {n} forwarder(s)"
    if warnings:
        message += f"; {len(warnings)} warning(s)"
    # A dry run's job ID was never stored: polling it would answer 404.
    return {
        "job_id": None if is_dry else job.get("job_id"),
        "status": job.get("status"),
        "dry_run": is_dry,
        "warnings": warnings,
        "estimate_bytes": est.get("bytes"),
        "estimate_forwarders": est.get("forwarders"),
        "resolved": resolved,
        "job": job,
        "message": message,
    }


def cancel_job(client: PromotionClient, job_id: str) -> tuple[dict, str]:
    job = enrich_job(client.post(f"jobs/{clean_job_id(job_id)}/cancel"))
    return job, job_message(job)
