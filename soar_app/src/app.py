"""Splunk SOAR app for Data Promotion: promote edge archive data into Splunk from playbooks."""

from soar_sdk.abstract import SOARClient
from soar_sdk.action_results import ActionOutput, OutputField
from soar_sdk.app import App
from soar_sdk.asset import AssetField, BaseAsset
from soar_sdk.logging import getLogger
from soar_sdk.params import Param, Params

from . import handlers
from .client import PromotionClient

logger = getLogger()


class Asset(BaseAsset):
    base_url: str = AssetField(
        description="Search Head management URL, e.g. https://sh1.example.com:8089",
    )
    token: str = AssetField(
        sensitive=True,
        description="Splunk token of a service account with the promotion_user role",
    )
    verify_tls: bool = AssetField(
        default=True, description="Verify the Search Head's TLS certificate"
    )
    timeout: int = AssetField(
        default=60, description="Seconds to wait for each request"
    )


app = App(
    name="data_promotion_soar",
    app_type="siem",
    logo="logo.svg",
    logo_dark="logo_dark.svg",
    product_vendor="Splunk Inc.",
    product_name="Data Promotion",
    publisher="Mikael Bjerkeland",
    appid="9ee4338d-3381-4c5a-940d-8d0584bc07c2",
    fips_compliant=False,
    asset_cls=Asset,
)


def client_for(asset: Asset) -> PromotionClient:
    return PromotionClient(
        asset.base_url, asset.token, verify=asset.verify_tls, timeout=asset.timeout
    )


@app.test_connectivity()
def test_connectivity(soar: SOARClient, asset: Asset) -> None:
    """Check that the Search Head answers and the token may use Data Promotion."""
    with client_for(asset) as client:
        client.get("jobs", {"limit": 1})
    logger.info(f"Connected to Data Promotion on {asset.base_url}")


# ---- outputs ----

JOB_ID_CEF = ["data promotion job id"]


class TotalSummary(ActionOutput):
    total: int


class JobSummary(ActionOutput):
    job_id: str = OutputField(cef_types=JOB_ID_CEF)
    status: str
    is_final: bool


class VerifySummary(ActionOutput):
    events: int


class ForwarderOutput(ActionOutput):
    guid: str
    hostname: str | None = OutputField(cef_types=["host name"])
    label: str | None
    status: str | None
    last_seen: int | None
    version: str | None
    max_bytes_per_job: int | None
    sourcetypes: list[str] | None
    days: int | None
    files: int | None
    bytes: int | None
    earliest: int | None
    latest: int | None


class CatalogOutput(ActionOutput):
    guid: str
    hostname: str | None = OutputField(cef_types=["host name"])
    label: str | None
    status: str | None
    sourcetype: str
    days: int | None
    files: int | None
    bytes: int | None
    earliest: int | None
    latest: int | None
    shared_store: bool | None
    agents: list[str] | None
    updated_at: int | None


class JobOutput(ActionOutput):
    job_id: str = OutputField(cef_types=JOB_ID_CEF)
    status: str
    is_final: bool
    search: str
    message: str | None
    sourcetype: str | None
    earliest: int | None
    latest: int | None
    forwarders: str | None
    forwarders_count: int | None
    agents_expected: int | None
    agents_reported: int | None
    agents_failed: int | None
    files_total: int | None
    files_done: int | None
    bytes_read: int | None
    events_staged: int | None
    events_indexed: int | None
    index_filter: str | None
    target_index: str | None
    submitted_by: str | None
    submitted_at: int | None


class TaskOutput(ActionOutput):
    job_id: str | None = OutputField(cef_types=JOB_ID_CEF)
    agent: str | None
    agent_host: str | None = OutputField(cef_types=["host name"])
    status: str | None
    files_total: int | None
    files_done: int | None
    bytes_read: int | None
    events_staged: int | None
    events_skipped: int | None
    events_bad: int | None
    started_at: int | None
    finished_at: int | None
    message: str | None


class VerifyRowOutput(ActionOutput):
    index: str
    sourcetype: str
    host: str = OutputField(cef_types=["host name"])
    count: int


def rows_to(output: type[ActionOutput], rows: list[dict]) -> list:
    """Outputs from API rows; fields the API leaves out become None."""
    names = output.model_fields
    return [output.model_validate({k: r.get(k) for k in names}) for r in rows]


# ---- params ----


class SourcetypeFilter(Params):
    sourcetype: str | None = Param(description="Only this sourcetype")


class ListDataParams(Params):
    sourcetype: str | None = Param(description="Only this sourcetype")
    forwarder: str | None = Param(
        description="Only these forwarders: GUIDs or hostnames", cef_types=["host name"]
    )


class ListJobsParams(Params):
    status: str | None = Param(
        description="Only jobs with this status",
        value_list=[
            "pending",
            "running",
            "staged",
            "indexing",
            "cancel_requested",
            "completed",
            "incomplete",
            "failed",
            "cancelled",
        ],
    )
    limit: int = Param(
        description="Most jobs to return (newest first, at most 1000)", default=50
    )


class JobIdParams(Params):
    job_id: str = Param(
        description="Data Promotion job ID", primary=True, cef_types=JOB_ID_CEF
    )


# ---- read actions ----


@app.action(action_type="investigate", read_only=True, summary_type=TotalSummary)
def list_forwarders(
    params: SourcetypeFilter, soar: SOARClient, asset: Asset
) -> list[ForwarderOutput]:
    """List the forwarders, what their edge archives hold and whether they report."""
    with client_for(asset) as client:
        rows = handlers.list_forwarders(client, params.sourcetype)
    soar.set_summary(TotalSummary(total=len(rows)))
    soar.set_message(f"{len(rows)} forwarder(s)")
    return rows_to(ForwarderOutput, rows)


@app.action(action_type="investigate", read_only=True, summary_type=TotalSummary)
def list_data(
    params: ListDataParams, soar: SOARClient, asset: Asset
) -> list[CatalogOutput]:
    """List which forwarders hold which sourcetypes, with days, size and time span."""
    with client_for(asset) as client:
        rows = handlers.list_data(client, params.sourcetype, params.forwarder)
    soar.set_summary(TotalSummary(total=len(rows)))
    hosts = len({r["guid"] for r in rows})
    soar.set_message(
        f"{hosts} forwarder(s), {len(rows)} forwarder and sourcetype row(s)"
    )
    return rows_to(CatalogOutput, rows)


@app.action(action_type="investigate", read_only=True, summary_type=TotalSummary)
def list_jobs(
    params: ListJobsParams, soar: SOARClient, asset: Asset
) -> list[JobOutput]:
    """List promotion jobs, newest first."""
    with client_for(asset) as client:
        jobs = handlers.list_jobs(client, params.status, params.limit)
    soar.set_summary(TotalSummary(total=len(jobs)))
    soar.set_message(f"{len(jobs)} job(s)")
    return rows_to(JobOutput, jobs)


@app.action(
    action_type="investigate",
    read_only=True,
    verbose="Poll this every 30 to 60 seconds after promote data until is_final is true.",
    summary_type=JobSummary,
)
def get_job(params: JobIdParams, soar: SOARClient, asset: Asset) -> JobOutput:
    """Get a promotion job's status and progress."""
    with client_for(asset) as client:
        job, message = handlers.get_job(client, params.job_id)
    soar.set_summary(
        JobSummary(job_id=job["job_id"], status=job["status"], is_final=job["is_final"])
    )
    soar.set_message(message)
    return rows_to(JobOutput, [job])[0]


@app.action(action_type="investigate", read_only=True, summary_type=TotalSummary)
def get_job_tasks(
    params: JobIdParams, soar: SOARClient, asset: Asset
) -> list[TaskOutput]:
    """Get a promotion job's progress on each forwarder."""
    with client_for(asset) as client:
        rows = handlers.get_job_tasks(client, params.job_id)
    soar.set_summary(TotalSummary(total=len(rows)))
    soar.set_message(f"{len(rows)} forwarder task(s)")
    return rows_to(TaskOutput, rows)


@app.action(action_type="investigate", read_only=True, summary_type=VerifySummary)
def verify_job(
    params: JobIdParams, soar: SOARClient, asset: Asset
) -> list[VerifyRowOutput]:
    """Count a job's indexed events by index, sourcetype and host."""
    with client_for(asset) as client:
        rows, events = handlers.verify_job(client, params.job_id)
    soar.set_summary(VerifySummary(events=events))
    soar.set_message(
        f"{events} event(s) indexed; search promote_job={params.job_id.strip()}"
    )
    return rows_to(VerifyRowOutput, rows)


# ---- estimate, promote, cancel ----

FORWARDERS_HELP = (
    "Forwarders to promote from: GUIDs or hostnames, separated by commas or spaces. "
    "* for every forwarder that holds the sourcetype."
)
TIME_HELP = (
    "Epoch seconds, ISO 8601 (2026-11-25T10:00:00Z) or a Splunk time such as -24h@h"
)


class SelectionParams(Params):
    sourcetype: str = Param(description="Sourcetype, as it appears in list data")
    forwarders: str = Param(
        description=FORWARDERS_HELP,
        primary=True,
        cef_types=["host name"],
        allow_list=True,
    )
    earliest: str = Param(description="Start of the range. " + TIME_HELP)
    latest: str | None = Param(
        description="End of the range (default now). " + TIME_HELP, default="now"
    )


class PromoteParams(SelectionParams):
    index_filter: str | None = Param(
        description="Original indexes to promote; * wildcards allowed (default *)"
    )
    target_index: str | None = Param(
        description="Destination index (default: each event's original index)"
    )
    dry_run: bool = Param(
        description="Check everything and estimate, but create no job", default=False
    )
    reject_over_limit: bool = Param(
        description="Refuse the job when a forwarder would read more than its max_bytes_per_job",
        default=False,
    )


class ResolvedOutput(ActionOutput):
    value: str
    guid: str
    hostname: str = OutputField(cef_types=["host name"])


class EstimateOutput(ActionOutput):
    earliest: int | None
    latest: int | None
    seconds: int | None
    forwarders: int | None
    bytes: int | None
    over_limit_count: int
    over_limit_names: list[str]
    resolved: list[ResolvedOutput]


class EstimateSummary(ActionOutput):
    bytes: int
    forwarders: int
    over_limit_count: int


class PromoteOutput(ActionOutput):
    job_id: str | None = OutputField(cef_types=JOB_ID_CEF)
    status: str
    dry_run: bool
    warnings: list[str]
    estimate_bytes: int | None
    estimate_forwarders: int | None
    resolved: list[ResolvedOutput]
    job: JobOutput


class PromoteSummary(ActionOutput):
    job_id: str = OutputField(cef_types=JOB_ID_CEF)
    dry_run: bool
    warnings: int


@app.action(action_type="investigate", read_only=True, summary_type=EstimateSummary)
def estimate_promotion(
    params: SelectionParams, soar: SOARClient, asset: Asset
) -> EstimateOutput:
    """Estimate how much a promotion would read, per the catalog, before creating it."""
    with client_for(asset) as client:
        est = handlers.estimate(
            client, params.sourcetype, params.forwarders, params.earliest, params.latest
        )
    soar.set_summary(
        EstimateSummary(
            bytes=est.get("bytes") or 0,
            forwarders=est.get("forwarders") or 0,
            over_limit_count=est["over_limit_count"],
        )
    )
    soar.set_message(
        f"{est.get('bytes') or 0} bytes from {est.get('forwarders') or 0} forwarder(s); "
        f"{est['over_limit_count']} over their limit"
    )
    return EstimateOutput.model_validate(
        {k: est.get(k) for k in EstimateOutput.model_fields}
    )


@app.action(
    action_type="correct",
    verbose=(
        "Creates a promotion job and returns its job_id at once. Follow it with get job "
        "every 30 to 60 seconds until is_final is true, then verify job."
    ),
    summary_type=PromoteSummary,
)
def promote_data(
    params: PromoteParams, soar: SOARClient, asset: Asset
) -> PromoteOutput:
    """Promote a sourcetype's events in a time range from edge archives into Splunk."""
    with client_for(asset) as client:
        out = handlers.promote(
            client,
            params.sourcetype,
            params.forwarders,
            params.earliest,
            params.latest,
            index_filter=params.index_filter,
            target_index=params.target_index,
            dry_run=params.dry_run,
            reject_over_limit=params.reject_over_limit,
        )
    for warning in out["warnings"]:
        logger.warning(warning)
    soar.set_summary(
        PromoteSummary(
            job_id=out["job_id"] or "",
            dry_run=out["dry_run"],
            warnings=len(out["warnings"]),
        )
    )
    soar.set_message(out["message"])
    data = {k: out.get(k) for k in PromoteOutput.model_fields}
    data["job"] = rows_to(JobOutput, [out["job"]])[0]
    return PromoteOutput.model_validate(data)


@app.action(action_type="correct", summary_type=JobSummary)
def cancel_job(params: JobIdParams, soar: SOARClient, asset: Asset) -> JobOutput:
    """Cancel a pending or running promotion job."""
    with client_for(asset) as client:
        job, message = handlers.cancel_job(client, params.job_id)
    soar.set_summary(
        JobSummary(job_id=job["job_id"], status=job["status"], is_final=job["is_final"])
    )
    soar.set_message(message)
    return rows_to(JobOutput, [job])[0]


if __name__ == "__main__":
    app.cli()
