# Data Replay for Splunk Ingest Actions output

Replay events that Heavy Forwarders' Ingest Actions wrote to a file system or S3
(`year=YYYY/month=MM/day=DD/sourcetype=<st>/events_<latest>_<earliest>_<n>_<seq>_<HF server GUID>.ndjson`)
back into Splunk, selected by **sourcetype + time range + forwarder(s)**, from a Search Head.

```
Search Head (replay_control)                       Every HF with Ingest Actions output (TA-replay_agent)
  Data Replay page / `replay_submit`                  scripted input every 30-60s:
        │ outputlookup                                 1. register (own GUID + name) ──► replay_agents
        ▼                                              2. publish what it holds ──► replay_catalog, replay_sources
  KV store: replay_jobs ─────────────── REST ───────►  3. pull active jobs; if a job needs its data:
            replay_tasks  ◄───────────────────────────    write its own task ──► replay_tasks
            replay_agents, replay_catalog, ...           select files (day dirs + filename epochs)
        ▲                                                keep earliest <= time < latest, add replay_job=<id>
  "Replay - Track indexing" (every minute):             rename into staging/<job>/ ─► [batch://] input of
    tasks → job status, tstats → indexed count            this HF ─► its normal outputs ─► indexers
```

Source files are never modified or deleted: the forwarder only reads them.

## How it works with many forwarders
Ingest Actions writes to each HF's local disk, so every such HF runs `TA-replay_agent`
(deploy it with the Deployment Server) and replays **its own** files through its own
batch input and outputs. Control stays central on the Search Head.

- **Identity:** each forwarder reads its own server GUID and name from `server/info` and
  registers in `replay_agents`. That GUID is the one at the end of every dump filename it
  wrote. (Names of forwarders that never registered fall back to the hourly
  **Replay - Forwarder names** search over indexer metrics.)
- **Targeting:** a job selects a sourcetype, a time range and all forwarders or a list of
  GUIDs. At submit time the macro counts the forwarders holding matching data
  (`agents_expected`); only those create a task.
- **Progress:** each forwarder writes only its own task (`<job id>|<GUID>`), so thousands
  never overwrite each other. **Replay - Track indexing** rolls the tasks up into the job:
  `running` until every expected forwarder finished, then indexing is checked. Forwarders
  that fail or do not report within 30 min make the job `incomplete`, with the reason.
- **Load:** every forwarder polls the Search Head KV store once per interval. For
  thousands of forwarders set the scripted input `interval = 60`.

## Replayed events and Ingest Actions on the same HF
Replayed events go through the HF's ingest pipeline again, **including its Ingest Actions
rulesets** (they run in the `ruleset` pipeline, after `typing`, and are chosen by the
event's sourcetype, which is the original one). Without a change, a ruleset that writes
`splunkd` to the dump writes the replayed `splunkd` events to the dump again, and any
filter / mask / drop rules apply to them again.

Every replayed event carries the indexed field `replay_job`. Give the rules that must
not touch replayed events a condition that excludes them, at least the route to the
file system / S3 destination, e.g. the eval expression `isnull(replay_job)`. If the
dump is written *before* filter or drop rules (a full copy), give those rules the same
condition, otherwise a replay brings back only what was indexed originally.

Status: **not yet verified** that a ruleset condition can see the `replay_job` field of
events read with `INDEXED_EXTRACTIONS = HEC`. Test once per Splunk version (see below).
As a safety net, the agent skips events in the dump that already carry `replay_job`, so a
re-written replay is never replayed a second time.

## Install

### Search Head: `replay_control`
1. Install the app (on an SHC, deploy it via the deployer).
2. Give users who may submit replays the `replay_user` role.
3. Create a service account for the forwarders with role `replay_user`, and an
   authentication token for it (Settings → Tokens). Token auth must be enabled.

### Every HF with Ingest Actions output: `TA-replay_agent`
1. Deploy the app with the Deployment Server (restart required) and a
   `local/replay_agent.conf` such as:
   ```ini
   [agent]
   fs_root = /dumps/splunk9.4-filesystemout

   [control]
   sh_uri = https://sh1.example.com:8089
   ```
   (`backend = s3` with `s3_uri = s3://bucket/prefix` if the HF writes to S3.)
2. Store the SH token in the HF's credential store (realm `replay_agent`, name `sh_token`):
   ```sh
   curl -k -u admin https://localhost:8089/servicesNS/nobody/TA-replay_agent/storage/passwords \
        -d name=sh_token -d realm=replay_agent -d password='<token>'
   ```
   For many HFs, run this from your configuration management rather than shipping the
   token in a deployment app.
3. Add the `replay_job` condition to the HF's Ingest Actions rules (above).
4. Check `index=_internal source=*replay_agent.log` and the **Forwarders** tab: the HF
   shows as *Reporting* after its first run.

### All-in-one instance
Install both apps on the same instance. Skip the service account, the token and the
`storage/passwords` step: leave `sh_uri` empty and the agent uses the session key Splunk
gives the scripted input. Minimal `TA-replay_agent/local/replay_agent.conf`:
```ini
[agent]
fs_root = /dumps/splunk9.4-filesystemout
```

### Verify the Ingest Actions condition (once per Splunk version)
1. Note the dump's file count for one sourcetype on the **Available data** tab.
2. Replay one hour of that sourcetype from one forwarder.
3. When the job is `completed`, wait for the next catalog run (15 min). The file count
   for the replayed day must not have grown, and `| tstats count where index=* replay_job=<id>`
   must equal the job's replayed events.

## Use
- **Data Replay** page (Splunk UI Toolkit / Splunk Design System; follows the user's
  theme from Settings → User preferences): pick a sourcetype, a time range, optionally
  forwarders (search by hostname or GUID; empty = all) and an index filter, then
  **Replay**. Click a job for details, progress per forwarder, **Cancel job**,
  **Replay again**, and **Count now** (indexed events by index / sourcetype / host).
  Tabs list the available data and the forwarders (with heartbeat state).
- **Timeline:** click a sourcetype or a day on **Available data** (or **Pick on a timeline**
  in the form) to open a timeline of that sourcetype: the data the forwarders hold per
  UTC day, and earlier replays of it as bars colored by status (click one to open it).
  Drag to select a range, drag inside it to move it, drag an edge to resize, or click a day
  to select it; times snap to whole UTC hours and can also be typed. Ctrl/⌘ + scroll zooms,
  horizontal scroll pans, arrow keys move the selection (Shift: resize). A warning lists
  earlier replays the range overlaps, since their events would be indexed again.
- Or from search (the time picker sets the range):
  ``| `replay_submit(splunkd, *, *)` `` (all forwarders) or
  ``| `replay_submit(splunkd, *, 5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8 BF6301FD-4484-45FA-802E-F657F1AFB5F7)` ``.
  ``replay_submit(<sourcetype>, <index filter>)`` still works and means all forwarders.
- Cancel: ``| `replay_cancel(<job_id>)` ``
- Verify: ``| `replay_verify(<job_id>)` `` or search `replay_job=<job_id>`.
- Find or exclude replayed data: `replay_job=*` / `NOT replay_job=*`.

Job states (all set on the Search Head; forwarders only write their own task):

| Status | Meaning |
|---|---|
| `pending` | Submitted; no forwarder has reported yet |
| `running` | Forwarders are replaying (`n of m forwarders finished`) |
| `staged` / `indexing` | All expected forwarders finished; waiting until every replayed event is searchable |
| `completed` | Every replayed event is searchable and every forwarder succeeded |
| `incomplete` | Forwarders failed or did not report, or events still missing 30 min later (re-checked for 24h) |
| `failed` | No forwarder picked the job up within 30 min, or every forwarder failed |
| `rejected` | Submit validation failed (e.g. no forwarder holds the data) |
| `cancel_requested` → `cancelled` | Every forwarder stops after its current file |

Forwarders never search. **Replay - Track indexing** (every minute) counts indexed events per
`replay_job` with `tstats`. It runs as the app owner (`nobody`), which must be able to
search every index you replay into, including `_internal` if you replay that.

## Things to know
- **Duplicates:** replays go to the *original* index. Replaying a range that is
  still in Splunk duplicates those events; tell them apart with `replay_job`.
- **License:** replayed data counts against ingest license.
- **Retention:** events older than the index's `frozenTimePeriodInSecs` are
  frozen almost immediately. Events older than `MAX_DAYS_AGO` (default 2000 days)
  may get their timestamps changed.
- **Precision:** files are hourly. Every event is filtered on its own `time`, so
  the range is exact to the second. `file_slack_seconds` (default 1h) also scans
  neighbouring files to catch late-arriving events.
- **Partitions** are in UTC; month/day may be zero-padded or not.
- **Upgrading from 1.1:** update `replay_control` and every forwarder together.
  TA-replay_agent 1.1 writes the job record directly and is not understood by the new
  tracker. TA-replay_agent 1.2.0 cannot register (KV store 404) and processes no jobs:
  upgrade to 1.2.1.

## Tests
- Agent: `python3 -m unittest discover -s tests -v`
- UI: `cd ui && npm ci && npm test`
- UI visual check without Splunk: `cd ui && npm run harness`, serve `ui/dev/out` and open
  `/?scheme=light` or `/?scheme=dark` (mock data: 1,500 forwarders, jobs in every state).

## Build and package
The Data Replay page is a React app in `ui/`. Its bundle is built into
`replay_control/appserver/static/pages/data_replay.js` (not committed) and loaded by
Splunk's built-in `pages/splunk_ui_app.html` view template.

```sh
cd ui && npm ci && npm run package      # builds the page and writes dist/replay_control-<version>.spl
cd .. && COPYFILE_DISABLE=1 tar --no-xattrs -czf dist/TA-replay_agent-1.2.1.spl TA-replay_agent
```
`npm run dev` rebuilds on change. Symlink `replay_control` into `$SPLUNK_HOME/etc/apps`
and reload the page (bump the version in `app.conf` or use `/_bump` to clear static caches).
