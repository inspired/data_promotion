# Data Promotion for Splunk Ingest Actions output

Promote events that Heavy Forwarders' Ingest Actions wrote to a file system or S3
(`year=YYYY/month=MM/day=DD/sourcetype=<st>/events_<latest>_<earliest>_<n>_<seq>_<HF server GUID>.ndjson`)
back into Splunk, selected by **sourcetype + time range + forwarder(s)**, from a Search Head.

```
Search Head (promotion_control)                  Every HF with Ingest Actions output (TA-promotion_agent)
  Data Promotion page / `promote_submit`           scripted input every 30-60s:
        │ outputlookup                             1. register (own GUID + name) ──► promotion_agents
        ▼                                          2. publish what it holds ──► promotion_catalog, promotion_sources
  KV store: promotion_jobs ──────── REST ────────► 3. pull active jobs; if a job needs its data:
            promotion_tasks  ◄─────────────────────     write its own task ──► promotion_tasks
            promotion_agents, promotion_catalog, ...    select files (day dirs + filename epochs)
        ▲                                               keep earliest <= time < latest, add promote_job=<id>
  "Promotion - Track indexing" (every minute):          rename into var/spool/splunk/promotion/<job>/*.stash_hec
    tasks → job status, tstats → indexed count          ─► Splunk's built-in stash_hec input ─► outputs ─► indexers
```

Source files are never modified or deleted: the forwarder only reads them.

## How it works with many forwarders
Ingest Actions writes to each HF's local disk, so every such HF runs `TA-promotion_agent`
(deploy it with the Deployment Server) and promotes **its own** files through its own
Splunk's built-in `stash_hec` batch input and its outputs. Control stays central on the
Search Head.

- **Identity:** each forwarder reads its own server GUID and name from `server/info` and
  registers in `promotion_agents`. That GUID is the one at the end of every dump filename it
  wrote. (Names of forwarders that never registered fall back to the hourly
  **Promotion - Forwarder names** search over indexer metrics.)
- **Targeting:** a job selects a sourcetype, a time range and all forwarders or a list of
  GUIDs. At submit time the macro counts the forwarders holding matching data
  (`agents_expected`); only those create a task.
- **Progress:** each forwarder writes only its own task (`<job id>|<GUID>`), so thousands
  never overwrite each other. **Promotion - Track indexing** rolls the tasks up into the job:
  `running` until every expected forwarder finished, then indexing is checked. Forwarders
  that fail or do not report within 30 min make the job `incomplete`, with the reason.
- **Load:** every forwarder polls the Search Head KV store once per interval. For
  thousands of forwarders set the scripted input `interval = 60`.

## Promoted events and Ingest Actions on the same HF
Promoted events go through the HF's ingest pipeline again, **including its Ingest Actions
rulesets** (they run in the `ruleset` pipeline, after `typing`, and are chosen by the
event's sourcetype, which is the original one). Without a change, a ruleset that writes
`splunkd` to the dump writes the promoted `splunkd` events to the dump again, and any
filter / mask / drop rules apply to them again.

Every promoted event carries the indexed field `promote_job`. Give the rules that must
not touch promoted events a condition that excludes them, at least the route to the
file system / S3 destination, e.g. the eval expression `isnull(promote_job)`. If the
dump is written *before* filter or drop rules (a full copy), give those rules the same
condition, otherwise a promotion brings back only what was indexed originally.

Status: **not yet verified** that a ruleset condition can see the `promote_job` field of
events read with `INDEXED_EXTRACTIONS = HEC`. Test once per Splunk version (see below).
As a safety net, the agent skips events in the dump that already carry `promote_job`, so a
re-written promotion is never promoted a second time.

## Install

### Search Head: `promotion_control`
1. Install the app (on an SHC, deploy it via the deployer).
2. Give users who may submit promotions the `promotion_user` role.
3. Create a service account for the forwarders with role `promotion_user`, and an
   authentication token for it (Settings → Tokens). Token auth must be enabled.

### Every HF with Ingest Actions output: `TA-promotion_agent`
1. Deploy the app with the Deployment Server (restart required) and a
   `local/promotion_agent.conf` such as:
   ```ini
   [agent]
   fs_root = /dumps/splunk9.4-filesystemout

   [control]
   sh_uri = https://sh1.example.com:8089
   ```
   (`backend = s3` with `s3_uri = s3://bucket/prefix` if the HF writes to S3.)
2. Store the SH token in the HF's credential store (realm `promotion_agent`, name `sh_token`):
   ```sh
   curl -k -u admin https://localhost:8089/servicesNS/nobody/TA-promotion_agent/storage/passwords \
        -d name=sh_token -d realm=promotion_agent -d password='<token>'
   ```
   For many HFs, run this from your configuration management rather than shipping the
   token in a deployment app.
3. Add the `promote_job` condition to the HF's Ingest Actions rules (above).
4. Check `index=_internal source=*promotion_agent.log` and the **Agents** tab: the HF
   shows as *Reporting* after its first run.

### All-in-one instance
Install both apps on the same instance. Skip the service account, the token and the
`storage/passwords` step: leave `sh_uri` empty and the agent uses the session key Splunk
gives the scripted input. Minimal `TA-promotion_agent/local/promotion_agent.conf`:
```ini
[agent]
fs_root = /dumps/splunk9.4-filesystemout
```

### Verify the Ingest Actions condition (once per Splunk version)
1. Note the dump's file count for one sourcetype on the **Available data** tab.
2. Promote one hour of that sourcetype from one forwarder.
3. When the job is `completed`, wait for the next catalog run (15 min). The file count
   for the promoted day must not have grown, and `| tstats count where index=* promote_job=<id>`
   must equal the job's promoted events.

## Use
- **Data Promotion** page (Splunk UI Toolkit / Splunk Design System; follows the user's
  theme from Settings → User preferences): pick a sourcetype, a time range, optionally
  agents (search by hostname or GUID; empty = all) and an index filter, then
  **Promote**. Click a job for details, progress per agent, **Cancel job**,
  **Promote again**, and **Count now** (indexed events by index / sourcetype / host).
  Tabs list the available data and the agents (with heartbeat state). A completed job has
  **Open in Search** (its events, `promote_job=<id>`, over the job's time range).
- **Agent view:** click an agent on the **Agents** tab to see the datasets (sourcetypes)
  it holds. Pick one to see it on the timeline, select a range and **Promote from <agent>**:
  the job targets that agent only.
- **Timeline:** click a sourcetype or a day on **Available data** (or **Pick on a timeline**
  in the form) to open a timeline of that sourcetype: the data the forwarders hold per
  UTC day, and earlier promotions of it as bars colored by status (click one to open it).
  Drag to select a range, drag inside it to move it, drag an edge to resize, or click a day
  to select it; times snap to whole UTC hours and can also be typed. Ctrl/⌘ + scroll zooms,
  horizontal scroll pans, arrow keys move the selection (Shift: resize). A warning lists
  earlier promotions the range overlaps, since their events would be indexed again.
- Or from search (the time picker sets the range):
  ``| `promote_submit(<sourcetype>, <index filter>, <forwarders>, <destination index>)` ``, e.g.
  ``| `promote_submit(splunkd, *, *, *)` `` (all indexes, all forwarders, original index) or
  ``| `promote_submit(splunkd, *, 5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8 BF6301FD-4484-45FA-802E-F657F1AFB5F7, promotion_test)` ``.
- Cancel: ``| `promote_cancel(<job_id>)` ``
- Verify: ``| `promote_verify(<job_id>)` `` or search `promote_job=<job_id>`.
- Find or exclude promoted data: `promote_job=*` / `NOT promote_job=*`.

Job states (all set on the Search Head; forwarders only write their own task):

| Status | Meaning |
|---|---|
| `pending` | Submitted; no forwarder has reported yet |
| `running` | Forwarders are promoting (`n of m forwarders finished`) |
| `staged` / `indexing` | All expected forwarders finished; waiting until every promoted event is searchable |
| `completed` | Every promoted event is searchable and every forwarder succeeded |
| `incomplete` | Forwarders failed or did not report, or events still missing 30 min later (re-checked for 24h) |
| `failed` | No forwarder picked the job up within 30 min, or every forwarder failed |
| `rejected` | Submit validation failed (e.g. no forwarder holds the data) |
| `cancel_requested` → `cancelled` | Every forwarder stops after its current file |

Forwarders never search. **Promotion - Track indexing** (every minute) counts indexed events per
`promote_job` with `tstats`. It runs as the app owner (`nobody`), which must be able to
search every index you promote into, including `_internal` if you promote that.

## Things to know
- **Duplicates:** promotions go to the *original* index unless a destination index is set.
  Promoting a range that is still in Splunk duplicates those events; tell them apart
  with `promote_job`.
- **Destination index:** a job can send every event to one index instead (the index
  filter still matches the original index). That index must already exist on the
  indexers: Splunk cannot check it from the Search Head, and events for a missing index
  are dropped (or go to `lastChanceIndex`).
- **License:** promoted data counts against ingest license.
- **Retention:** events older than the index's `frozenTimePeriodInSecs` are
  frozen almost immediately. Events older than `MAX_DAYS_AGO` (10000 days for `stash_hec`)
  may get their timestamps changed.
- **Input:** the agent adds no input of its own. Staged files go to Splunk's built-in
  `[batch://$SPLUNK_HOME/var/spool/splunk/...stash_hec]` input (sourcetype `stash_hec`,
  `INDEXED_EXTRACTIONS = hec`), the one `collect output_format=hec` uses. Do not disable
  it on the HFs. Each event keeps its own sourcetype, host, source and index.
- **Precision:** files are hourly. Every event is filtered on its own `time`, so
  the range is exact to the second. `file_slack_seconds` (default 1h) also scans
  neighbouring files to catch late-arriving events.
- **Partitions** are in UTC; month/day may be zero-padded or not.
- **Field sampling:** with each catalog publish, every agent reads the first
  `sample_lines` (200) events of the `sample_files` (3) newest files per sourcetype and
  publishes the indexes, hosts, sources and indexed field *names* seen there
  (`promotion_fields`, one row per agent + sourcetype). The index filter suggests those
  indexes. Event text and field values are never published. A sourcetype is read again
  only when its newest files change; on S3 only the first `sample_bytes` (1 MiB) of an
  object are fetched. Search-time fields are not visible in the dump: the files hold
  `_raw` plus indexed fields only. `sample_files = 0` turns sampling off.

## Tests
- Agent: `python3 -m unittest discover -s tests -v`
- UI: `cd ui && npm ci && npm test`
- UI visual check without Splunk: `cd ui && npm run harness`, serve `ui/dev/out` and open
  `/?scheme=light` or `/?scheme=dark` (mock data: 1,500 forwarders, jobs in every state).

## Build and package
The Data Promotion page is a React app in `ui/`. Its bundle is built into
`promotion_control/appserver/static/pages/data_promotion.js` (not committed) and loaded by
Splunk's built-in `pages/splunk_ui_app.html` view template.

```sh
cd ui && npm ci && npm run package      # builds the page and writes dist/promotion_control-<version>.spl
cd .. && COPYFILE_DISABLE=1 tar --no-xattrs -czf dist/TA-promotion_agent-<version>.spl TA-promotion_agent
```
`npm run dev` rebuilds on change. Symlink `promotion_control` into `$SPLUNK_HOME/etc/apps`
and reload the page (bump the version in `app.conf` or use `/_bump` to clear static caches).
