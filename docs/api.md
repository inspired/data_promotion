# REST API

The Search Head app (`promotion_control`) has a REST API under `/services/data_promotion`. The Data Promotion page uses it, and so can Splunk SOAR, scripts and other tools: list what the edge archives hold, estimate a promotion, create jobs, follow them and count their indexed events.

- [Access](#access)
- [Endpoints](#endpoints)
- [Create a job](#create-a-job)
- [Follow a job](#follow-a-job)
- [Data and estimates](#data-and-estimates)
- [Errors](#errors)
- [Splunk SOAR](#splunk-soar)

## Access

Call the Search Head's management port with a Splunk authentication token, or a session key:

```sh
curl -k -H "Authorization: Bearer $TOKEN" https://sh1.example.com:8089/services/data_promotion/jobs
```

The caller needs the `promote_data` capability. The `promotion_user` role has it, and so does `admin`. For SOAR or another system, create a service account with the `promotion_user` role and a token for it (**Settings → Tokens**).

The API reads and writes the KV store itself, so callers don't need access to the app's collections. Two things run as the caller: relative times such as `-7d@d` are resolved in the caller's time zone, and [verify](#follow-a-job) only counts events in indexes the caller can search.

Request bodies are JSON (`Content-Type: application/json`); form-encoded bodies also work. Responses are always JSON.

## Endpoints

| Method | Path | Does |
|---|---|---|
| `GET` | `catalog` | What the edge archives hold, per sourcetype and UTC day. `?sourcetype=` for one; `?forwarder=<GUID>` for one forwarder's rows; `?by=forwarder` for one row per forwarder and sourcetype. |
| `GET` | `forwarders` | Every forwarder: what it holds, status, add-on version and size limit. `?sourcetype=` for those that hold it. |
| `GET` | `fields?sourcetype=<st>` | Indexes and indexed fields seen in sampled events, one row per forwarder. |
| `GET` | `estimate` | Size of a promotion before you create it. |
| `GET` | `jobs` | Jobs, newest first. `?limit=` (default 200, at most 1000), `?status=`. |
| `POST` | `jobs` | Create a job, or check one with `"dry_run": true`. |
| `GET` | `jobs/<job id>` | One job. |
| `GET` | `jobs/<job id>/tasks` | Progress on each forwarder. |
| `POST` | `jobs/<job id>/cancel` | Cancel a pending or running job. |
| `GET` | `jobs/<job id>/verify` | Indexed events of the job by index, sourcetype and host. |

Lists come back as `{"items": [...]}`.

## Create a job

```sh
curl -k -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
     https://sh1.example.com:8089/services/data_promotion/jobs \
     -d '{"sourcetype": "pan:traffic", "earliest": "-24h@h", "latest": "now",
          "forwarders": ["5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8"], "target_index": "promoted"}'
```

| Field | Required | Value |
|---|---|---|
| `sourcetype` | yes | As it appears in the catalog. |
| `earliest` | yes | Epoch seconds or a Splunk time modifier (`-7d@d`). All time and real-time ranges are refused. |
| `latest` | no | Epoch or modifier. Default `now`; a later time is cut back to now. |
| `forwarders` | no | Server GUIDs, as a list or separated by spaces or commas. Empty or `*` means every forwarder that holds the sourcetype. |
| `index_filter` | no | Original indexes to promote; `*` wildcards allowed. Default `*`. |
| `target_index` | no | Destination index. Empty means each event's original index. The index must exist on the indexers. |
| `dry_run` | no | `true`: check everything and return the job and estimate, but don't create it. |
| `reject_over_limit` | no | `true`: refuse the job when a forwarder would read more than its `max_bytes_per_job`. |

The answer is `201 Created`:

```json
{
  "job": {
    "job_id": "20261125-101500-3fa9c2", "status": "pending", "sourcetype": "pan:traffic",
    "earliest": 1795996800, "latest": 1796083200, "forwarders": "5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8",
    "forwarders_count": 1, "agents_expected": 1, "index_filter": "*", "target_index": "promoted",
    "submitted_by": "soar_svc", "submitted_at": 1796083215, "message": "waiting for forwarders"
  },
  "estimate": { "forwarders": 1, "bytes": 412000000, "over_limit": [], "...": "..." },
  "warnings": []
}
```

`warnings` names every forwarder that would read more than its limit: it fails and promotes nothing. Promote a shorter range, or set `reject_over_limit`, to avoid that.

## Follow a job

Poll `GET jobs/<job id>` until the status is final:

| Status | Final | Means |
|---|---|---|
| `pending` | | No forwarder has picked it up yet. |
| `running` | | Forwarders are promoting. |
| `staged`, `indexing` | | Every forwarder finished; waiting until every promoted event is searchable. |
| `cancel_requested` | | Every forwarder stops after its current file. |
| `completed` | yes | Every promoted event is searchable and every forwarder succeeded. |
| `incomplete` | yes | Some forwarders failed or didn't report, or events were still missing after 30 minutes. It is checked again for 24 hours. |
| `failed` | yes | No forwarder picked it up within 30 minutes, or every forwarder failed. |
| `cancelled` | yes | Cancelled. |

The job also carries progress: `files_total`, `files_done`, `bytes_read`, `events_staged`, `events_indexed`, `agents_expected`, `agents_reported`, `agents_failed`, and a `message` saying what is going on. Jobs move on once a minute (the **Promotion - Track indexing** search), so polling every 30 to 60 seconds is enough.

`GET jobs/<job id>/verify` counts the job's events with `tstats` over its time range:

```json
{ "items": [ { "index": "promoted", "sourcetype": "pan:traffic", "host": "fw01", "count": "120344" } ], "events": 120344 }
```

## Data and estimates

`GET estimate?sourcetype=<st>&earliest=<t>&latest=<t>&forwarders=<GUIDs>` returns what a job would read, from the catalog:

```json
{
  "earliest": 1795996800, "latest": 1796083200, "seconds": 86400,
  "forwarders": 2, "bytes": 61200000000,
  "largest": { "guid": "5A8D…", "name": "hf-osl-0001", "bytes": 58000000000, "limit": 53687091200, "limit_reported": true },
  "over_limit": [ { "guid": "5A8D…", "name": "hf-osl-0001", "bytes": 58000000000, "limit": 53687091200, "limit_reported": true } ],
  "per_forwarder": [ "…" ]
}
```

`limit_reported` is false for forwarders whose add-on is older than 2.2.0; the default limit (50 GB) is assumed for them.

A row of `GET forwarders`:

```json
{
  "guid": "5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8", "hostname": "hf-osl-0001", "label": "hf-osl-0001",
  "status": "reporting", "last_seen": 1796083190, "version": "2.2.0", "max_bytes_per_job": 53687091200,
  "agents": ["5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8"], "sourcetypes": ["pan:traffic", "splunkd"],
  "days": 30, "files": 1440, "bytes": 812000000000, "earliest": 1793491200, "latest": 1796083200
}
```

`status` is `reporting`, `stale` (no heartbeat for 15 minutes) or `unregistered`.

`GET catalog?by=forwarder` says which forwarders hold which sourcetypes: one row per forwarder and sourcetype, sorted by forwarder. Narrow it with `?sourcetype=` and `?forwarder=<GUIDs>`. Added in 3.1.0.

```json
{
  "guid": "5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8", "hostname": "hf-osl-0001", "label": "hf-osl-0001",
  "status": "reporting", "sourcetype": "pan:traffic", "days": 30, "files": 720, "bytes": 412000000000,
  "earliest": 1793491200, "latest": 1796083200, "updated_at": 1796083100,
  "agents": ["5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8"], "shared_store": false
}
```

The catalog is kept per add-on that reports it. When one add-on reports a store that holds several forwarders' archives, `shared_store` is true and the numbers are those of the whole store.

## Errors

Errors have a status code and a JSON body; `field` names the request field at fault, when there is one:

```json
{ "error": "no forwarder holds data for this sourcetype", "field": "sourcetype" }
```

| Status | When |
|---|---|
| `400` | The request isn't valid. Nothing was created. |
| `403` | The caller lacks `promote_data`. |
| `404` | No such job or endpoint. |
| `405` | Wrong method for the endpoint. |
| `409` | The job can't be cancelled any more, or has no time range to verify. |
| `502` | The KV store or a search on the Search Head failed. |

## Splunk SOAR

Use the **Data Promotion** app for SOAR (`data_promotion_soar-<version>.tgz` in the [latest release](../../../releases/latest)). It has an action for each endpoint, takes hostnames as well as forwarder GUIDs, and documents a playbook that promotes from an edge host when an IOC matches. See [its README](../soar_app/README.md).

Without the app, use the **HTTP** app with an asset for the Search Head:

- **base_url**: `https://sh1.example.com:8089/services/data_promotion`
- **auth**: a token of the SOAR service account (role `promotion_user`), sent as `Authorization: Bearer <token>`

A playbook then:

1. **post data** to `/jobs` with a body such as `{"sourcetype": "pan:traffic", "earliest": "-24h@h", "forwarders": ["<GUID>"]}`, and keeps `job.job_id` from the response. Check `warnings` and decide whether to go on.
2. **get data** from `/jobs/<job id>` in a loop, every minute, until `status` is `completed`, `incomplete`, `failed` or `cancelled`.
3. **get data** from `/jobs/<job id>/verify` to record how many events were promoted, then search them with `promote_job=<job id>`.

The same in Python, for a custom function or a script:

```python
import time
import requests

SH = "https://sh1.example.com:8089/services/data_promotion"
H = {"Authorization": "Bearer " + TOKEN}

r = requests.post(SH + "/jobs", headers=H, verify=CA_BUNDLE, json={
    "sourcetype": "pan:traffic", "earliest": "-24h@h", "forwarders": [guid], "reject_over_limit": True})
if r.status_code != 201:
    raise RuntimeError(r.json()["error"])
job_id = r.json()["job"]["job_id"]

while True:
    job = requests.get(SH + "/jobs/" + job_id, headers=H, verify=CA_BUNDLE).json()
    if job["status"] in ("completed", "incomplete", "failed", "cancelled"):
        break
    time.sleep(60)

events = requests.get(SH + "/jobs/" + job_id + "/verify", headers=H, verify=CA_BUNDLE).json()["events"]
```
