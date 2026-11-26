# Data Promotion for Splunk SOAR

A Splunk SOAR app for [Data Promotion](../README.md). Playbooks and analysts can see what the edge archives hold, promote events from one edge host or many, follow the job and count what was indexed. The app calls the Search Head's [REST API](../docs/api.md). It needs `promotion_control` 3.1.0 or later on the Search Head, and Splunk SOAR 7.0 or later.

## Install

1. **Download** `data_promotion_soar-<version>.tgz` from the [latest release](../../../releases/latest).
2. **Upload it in SOAR:** **Home → Apps → Install App**.
3. **Create a service account on the Search Head** with the `promotion_user` role, and a token for it (**Settings → Tokens**).
4. **Configure an asset** for the Search Head:

| Field | Value |
|---|---|
| `base_url` | The Search Head's management port, e.g. `https://sh1.example.com:8089` (a trailing `/services/data_promotion` is ignored). On a search head cluster, any member or the load balancer in front of their management ports. |
| `token` | The service account's token. |
| `verify_tls` | Verify the Search Head's certificate. Default on. |
| `timeout` | Seconds to wait for each request. Default 60. |

**Test Connectivity** names the problem when the token is wrong (401) or its user lacks the `promote_data` capability (403).

## Actions

| Action | Does |
|---|---|
| **list forwarders** | Every forwarder: what its edge archive holds, status (`reporting`, `stale`, `unregistered`), add-on version and size limit. Optional `sourcetype`. |
| **list data** | Which forwarders hold which sourcetypes: one row per forwarder and sourcetype, with hostname, GUID, days, files, bytes and time span (`earliest`, `latest`). Optional `sourcetype` and `forwarder`. `shared_store` means the numbers cover a store shared with other forwarders. |
| **estimate promotion** | How much a promotion would read, and which forwarders would go over their `max_bytes_per_job`. Nothing is created. |
| **promote data** | Creates a job and returns its `job_id` at once. With `dry_run`, checks and estimates without creating a job; `job_id` is then empty. |
| **get job** | Status and progress of a job, with `is_final` and the search for its events (`promote_job=<job id>`). |
| **get job tasks** | Progress on each forwarder. |
| **list jobs** | Jobs, newest first. Optional `status` and `limit` (at most 1000). |
| **cancel job** | Cancels a pending or running job. |
| **verify job** | Indexed events of the job by index, sourcetype and host, and their total. |

### Forwarders

`forwarders` (and `forwarder` in **list data**) takes GUIDs, hostnames or labels as shown in **list forwarders**, separated by commas or spaces. Case doesn't matter. It must name the **forwarder** that holds the edge archive. An event's `host` is often the device that logged it, not the forwarder; when they differ, map the device to its forwarder in the playbook first.

- A value that matches no forwarder fails the action and names it. Nothing is promoted.
- A hostname that two forwarders share fails the action and lists their GUIDs; pass the GUID instead.
- `*` alone promotes from every forwarder that holds the sourcetype. An empty value is refused, so a playbook with a missing host never promotes from the whole fleet.
- The output lists what each value resolved to (`resolved.*.value`, `guid`, `hostname`).

### Times

`earliest` and `latest` take epoch seconds or milliseconds (CEF `rt` and `end` are milliseconds), ISO 8601 (`2026-11-25T10:00:00Z`, as in artifact and event times) or a Splunk time modifier such as `-24h@h`. An ISO time without a time zone is taken as UTC. `latest` defaults to `now`. Relative times are resolved in the time zone of the service account.

## Promote from an edge host when an IOC matches

Observables are extracted at the edge and looked up centrally against known IOCs. When one matches, the playbook promotes the raw data around it from the edge host that saw it:

1. **promote data** with `forwarders` = the forwarder at the site that saw the observable (its hostname or GUID), `sourcetype` = the data to promote, and `earliest` / `latest` a window around the event time. Keep `action_result.data.*.job_id`. Look at `warnings`: a forwarder named there would read more than its limit and fail. Set `reject_over_limit` to refuse such a job instead.
2. **get job** with that `job_id`, every 60 seconds, until `action_result.data.*.is_final` is true. Jobs move on once a minute, so polling faster gains nothing. `status` is then `completed`, `incomplete`, `failed` or `cancelled`.
3. **verify job** to record how many events were indexed (`action_result.summary.events`). Search them with `action_result.data.*.search` from **get job**, `promote_job=<job id>`.

An `incomplete` job promoted some of the data: some forwarders failed or didn't report, or events were still missing after 30 minutes. **get job tasks** shows which forwarder failed and why.

## Build and test

Requires [uv](https://docs.astral.sh/uv/) and the SOAR SDK CLI: `uv tool install splunk-soar-sdk`.

```sh
cd soar_app
uv run pytest                                                   # unit tests
uv run python -m src.app action get-job -p params.json -a asset.json   # run one action locally
uv run soarapps package build -o ../dist/data_promotion_soar-<version>.tgz
```

Raise `version` in `pyproject.toml` for a new release.
