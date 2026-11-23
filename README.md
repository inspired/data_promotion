# Data Replay for Splunk Ingest Actions output

Replay events that a Heavy Forwarder's Ingest Actions wrote to a file system or S3
(`year=YYYY/month=MM/day=DD/sourcetype=<st>/events_<latest>_<earliest>_*.ndjson`)
back into Splunk, selected by **sourcetype + time range**, from a Search Head.

```
Search Head (replay_control)                     Dedicated replay HF (TA-replay_agent)
  Dashboard "Data Replay" / `replay_submit`        scripted input every 30s:
        │ outputlookup                               1. publish catalog  ──► KV replay_catalog
        ▼                                            2. pull pending jobs ◄── KV replay_jobs
  KV store: replay_jobs, replay_catalog  ◄── REST ─  3. select files (day dirs + filename epochs)
                                                     4. keep events with earliest <= time < latest,
                                                        add fields.replay_job=<job id>
                                                     5. atomic rename into staging/<job id>/
                                                   [batch://…/staging] sinkhole, crcSalt=<SOURCE>
                                                   sourcetype replay:hec (INDEXED_EXTRACTIONS = HEC)
                                                     → original index/host/source/sourcetype
```

Source files are never modified or deleted: the agent always writes filtered copies,
and only those copies are consumed (deleted) by the batch input.

## Install

### Search Head: `replay_control`
1. Install the app (on an SHC, deploy it via the deployer).
2. Give users who may submit replays the `replay_user` role.
3. Create a service account for the agent with role `replay_user`, and an
   authentication token for it (Settings → Tokens). Token auth must be enabled.

### Replay Heavy Forwarder: `TA-replay_agent`
1. Make the dumps readable by the `splunk` user: an NFS/EFS mount of the dump
   directory (read-only is fine), or S3 with an instance profile and the AWS CLI v2.
2. Install the app. Create `local/replay_agent.conf`:
   ```ini
   [agent]
   backend = filesystem
   fs_root = /dumps/splunk9.4-filesystemout

   [control]
   sh_uri = https://sh1.example.com:8089
   ```
   For S3: `backend = s3`, `s3_uri = s3://my-bucket/prefix`, `aws_cli = /usr/local/bin/aws`.
3. Create the staging directories (the batch input needs them at startup):
   `mkdir -p $SPLUNK_HOME/var/replay/{work,staging}`
4. Store the SH token in this HF's credential store:
   ```sh
   curl -k -u admin https://localhost:8089/servicesNS/nobody/TA-replay_agent/storage/passwords \
        -d name=sh_token -d realm=replay_agent -d password='<token>'
   ```
5. `outputs.conf` points at your indexers as usual. Every index named in the
   replayed events must exist on the indexers. Restart Splunk.
6. Check `index=_internal source=*replay_agent.log`. The catalog appears on the
   dashboard after the first run.

### All-in-one instance
Install both apps on the same instance. You can skip the service account, the token,
and the `storage/passwords` step. Leave `sh_uri` empty (or set it to
`https://127.0.0.1:8089`) and the agent uses the session key Splunk gives the
scripted input. Minimal `TA-replay_agent/local/replay_agent.conf`:
```ini
[agent]
fs_root = /dumps/splunk9.4-filesystemout
```
Still create the staging directories and restart. If this instance also runs the
Ingest Actions ruleset that writes these sourcetypes to `/dumps`, read the replay
loop note below first.

## Use
- Dashboard **Data Replay**: pick a sourcetype, time range, and optional index filter, then Submit.
- Or from search (the time picker sets the range): ``| `replay_submit(splunkd, *)` ``
- Cancel: ``| `replay_cancel(<job_id>)` ``
- Verify: ``| `replay_verify(<job_id>)` `` or search `replay_job=<job_id>`.
- Find or exclude replayed data: `replay_job=*` / `NOT replay_job=*`.

Job states:

| Status | Set by | Meaning |
|---|---|---|
| `pending` | SH (submit) | Waiting for the agent |
| `running` | HF agent | Selecting, filtering and staging files |
| `staged` | HF agent | All files handed to the batch input |
| `indexing` | SH saved search | Some, not yet all, staged events are searchable |
| `completed` | SH saved search | `events_indexed` >= `events_staged` |
| `incomplete` | SH saved search | Still short 30 min after staging (re-checked for 24h) |
| `failed` / `rejected` / `cancel_requested` → `cancelled` | | |

The HF never searches. **Replay - Track indexing** (runs every 2 min on the Search
Head) counts indexed events per `replay_job` with `tstats` and updates the job.
It runs as the app owner (`nobody`), which must be able to search every index you
replay into, including `_internal` if you replay that.

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
- **Never** run this agent or point the staging dir at an HF that also runs the
  Ingest Actions file system destination for these sourcetypes (replay loop).

## Tests
`python3 -m unittest discover -s tests -v`

## Package
`tar czf replay_control.spl replay_control && tar czf TA-replay_agent.spl TA-replay_agent`
