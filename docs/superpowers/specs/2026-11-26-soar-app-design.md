# Splunk SOAR app for Data Promotion — design

## Intent

Playbooks and analysts in Splunk SOAR should run data promotions as native SOAR
actions instead of hand-built calls through the generic HTTP app.

Driving use case: observables are extracted at the edge and forwarded to a
central instance, where they are looked up against known IOCs. On a match, a
playbook promotes data from the edge host that produced the observable, then
follows the job until the events are searchable.

## Goals

- A SOAR app, built with the Splunk SOAR SDK, that covers the whole
  `/services/data_promotion` REST API (3.0.0) of `promotion_control`.
- Promote from an edge host by **hostname or forwarder GUID**, because a
  playbook usually knows the host from the event's `host` field.
- Asynchronous jobs: **promote data** returns the job ID at once; **get job**
  is polled until the status is final.

## Non-goals

- No changes to the Search Head app or its API, except one (decided 2026-11-26, after the
  first build): `GET catalog?by=forwarder` in `promotion_control` 3.1.0, so **list data** can
  say which forwarders hold which sourcetypes. The aggregated catalog only gave counts.
- No on_poll ingestion of jobs as containers, no custom views, no webhooks.
- No "wait for job" action; playbooks loop over **get job**.

## Anti-goals

- Promoting from more forwarders than asked: a hostname that matches nothing
  or several forwarders fails the action, it never widens to "all".
- An empty `forwarders` value silently meaning "every forwarder". The
  parameter is required; promoting from all forwarders needs an explicit `*`.

## Approaches

1. **SOAR SDK app, thin client over the REST API** (picked). Current way to
   build SOAR apps; typed params and outputs; hostname lookup lives in the app.
2. Classic BaseConnector app: legacy format, more code, rejected.
3. Playbook template on the HTTP app (what `docs/api.md` describes today): no
   hostname lookup, untyped outputs; this is what the app replaces.

## Design

**Location and tooling.** `soar_app/` in this repo, created with
`soarapps init`, Python 3.13 and 3.15 via `uv`. HTTP with `httpx`. Packaged
with `soarapps package build` into `dist/data_promotion_soar-<version>.tgz`.
App version starts at 1.0.0, independent of the Splunk apps. *(builder's call)*

**Asset**

| Field | Notes |
|---|---|
| `base_url` | Search Head management URL, e.g. `https://sh1.example.com:8089`. The app appends `/services/data_promotion`. |
| `token` | Splunk token of a service account with the `promotion_user` role. Sensitive. Sent as `Authorization: Bearer`. |
| `verify_tls` | Default true. |
| `timeout` | Seconds per request, default 60. |

**Actions**

| Action | Type | API | Notes |
|---|---|---|---|
| test connectivity | — | `GET jobs?limit=1` | Distinct messages for unreachable, 401 (bad token), 403 (missing `promote_data`). |
| list forwarders | investigate | `GET forwarders` | Optional `sourcetype`. One output row per forwarder. |
| list data | investigate | `GET catalog?by=forwarder` | Optional `sourcetype`, `forwarder` (GUIDs or hostnames). One row per forwarder and sourcetype: hostname, GUID, days, files, bytes, span, `shared_store`. Needs `promotion_control` 3.1.0. |
| estimate promotion | investigate | `GET estimate` | Same selection params as promote data. Summary: bytes, forwarders, over-limit count. |
| promote data | correct | `POST jobs` | Params below. Returns `job_id`, status, warnings, estimate. Summary carries `job_id`. |
| get job | investigate | `GET jobs/<id>` | Adds `is_final` (bool) and `search` (`promote_job=<id>`) to the job fields. |
| get job tasks | investigate | `GET jobs/<id>/tasks` | One row per forwarder. |
| list jobs | investigate | `GET jobs` | Optional `status`, `limit`. |
| cancel job | correct | `POST jobs/<id>/cancel` | 409 becomes a clear failure ("job already final"). |
| verify job | investigate | `GET jobs/<id>/verify` | Rows per index/sourcetype/host; summary total `events`. |

**promote data params:** `sourcetype` (required), `forwarders` (required:
GUIDs and/or hostnames, comma- or space-separated, or `*`; tagged with the
`host name` CEF type so it shows on host artifacts), `earliest` (required),
`latest` (default `now`), `index_filter`, `target_index`, `dry_run`,
`reject_over_limit`.

**Forwarder resolution.** Each value other than `*` is matched against
`GET forwarders` rows: GUID, then hostname, then label, case-insensitive. No
match, or a hostname matching more than one GUID, fails the action and names
the value. Resolved GUIDs are sent to the API; the output lists what each
value resolved to.

**Times.** `earliest`/`latest` accept epoch seconds and Splunk time modifiers
(passed through), and ISO 8601 timestamps such as SOAR artifact times
(converted to epoch in the app). *(builder's call)*

**Errors.** API errors become `ActionFailure` with the HTTP status, the
API's `error` text and `field` when present.

**Docs.** `soar_app/README.md` (install, asset, actions, the polling playbook
pattern); `docs/api.md` "Splunk SOAR" section points to the app; root README
"What you install" table and CHANGELOG get a line.

**Tests.** Unit tests with `httpx.MockTransport`: forwarder resolution
(GUID, hostname, label, `*`, missing, ambiguous), time conversion, error
mapping, each action's request and output. Run with `uv run pytest`.

## Left to the builder

App ID (fresh UUID), logos, exact output field names beyond those above,
module layout inside `soar_app/src/`.
