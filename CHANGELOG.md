# Changelog

Each release lists the version of the Search Head app and of the agent add-on. Download them from [Releases](../../releases).

## 3.1.0 (2026-11-26)

`promotion_control` 3.1.0 · `data_promotion_soar` 1.0.0 (new) · `TA-promotion_agent` 2.2.0 (unchanged)

### Changes
- **Splunk SOAR app.** Playbooks and analysts can list forwarders and what the edge archives hold, estimate and promote, follow, cancel and verify jobs as SOAR actions. Promote from an edge host by its hostname or GUID: a name that matches no forwarder, or several, fails the action instead of promoting more than asked. Times can be ISO 8601 or epoch milliseconds, as in artifacts. **promote data** returns the job ID at once, and **get job** says when the job is final. See [the SOAR app](soar_app/README.md).
- **Which forwarders hold what.** `GET catalog?by=forwarder` returns one row per forwarder and sourcetype, with its hostname, days, size and time span. The SOAR app's **list data** uses it. See [REST API](docs/api.md#data-and-estimates).

### Install / upgrade
- Upgrade `promotion_control` on the Search Head to 3.1.0; the SOAR app needs it.
- Upload `data_promotion_soar-1.0.0.tgz` in SOAR and configure an asset with the Search Head's management URL and a token of a user with the `promotion_user` role.

## 3.0.0 (2026-11-25)

`promotion_control` 3.0.0 · `TA-promotion_agent` 2.2.0 (unchanged)

### Changes
- **REST API.** The Search Head app has an API under `/services/data_promotion`: list what the edge archives hold and the forwarders, estimate a promotion, create, follow, cancel and verify jobs. Splunk SOAR, scripts and other tools can now promote data. See [REST API](docs/api.md), which includes a SOAR example.
- **The page uses the same API.** Checks, the size estimate and the forwarder list are worked out on the Search Head, once, for the page and every other caller. A job that isn't valid is refused with the reason and is no longer stored as `rejected`.
- **Users need a capability, not KV store access.** The `promotion_user` role now grants the `promote_data` capability, which the page and the API need. Users no longer read or write the app's collections directly.
- **Own role for the forwarders.** The new `promotion_agent` role is for the forwarders' service account. It can only read jobs and write the forwarders' own records.
- **Search macros removed.** `promote_submit`, `promote_cancel` and `promote_verify` are gone; use the page or the API.

### Install / upgrade
- Give the forwarders' service account the `promotion_agent` role. With only `promotion_user` it can no longer reach the KV store, and the forwarders stop reporting.
- Restart the Search Head (new `restmap.conf`, `web.conf` and `authorize.conf`).

## 2.2.0 (2026-11-25)

`promotion_control` 2.2.0 · `TA-promotion_agent` 2.2.0

### Changes
- **Warning for long ranges.** When a range is longer than three days and no destination index is set, the page warns that the old events would share buckets with live data in their original index. Those buckets then span weeks, so searches get slower and data is kept longer. Shown both in **New promotion** and on the timeline.
- **Estimated size before you promote.** The form and the timeline show about how much the forwarders will read for the selected range, and the most any one forwarder will read. The estimate comes from the catalog: days that are only partly selected count in proportion.
- **"Forwarders" instead of "agents".** The page, the job messages and the docs name the Heavy Forwarders you promote from. The add-on is still `TA-promotion_agent`, and its settings, collections and fields keep their names.
- **Warning before a job exceeds a forwarder's limit.** Forwarders now report their `max_bytes_per_job` with their heartbeat. If a forwarder would read more than that, the page names it before you submit. Forwarders with an add-on before 2.2.0 are assumed to use the 50 GiB default.

### Fixes
- **Long promotions no longer fill the forwarder's disk.** Before, the add-on checked free space once, against the size of the (often compressed) archive files, and then staged uncompressed events as fast as it could read them. Now it stays at most `max_staged_bytes` (2 GiB) ahead of the `stash_hec` input: before each file it waits until the forwarder has sent enough of what is already staged. Free space is checked before every file and every 64 MiB written. A task whose staged data isn't read for `staging_stall_seconds` (30 min) fails with a message that points at the `stash_hec` input.

## 2.1.1 (2026-11-24)

`promotion_control` 2.1.1 · `TA-promotion_agent` 2.1.0 (unchanged)

### Fixes
- **Job details no longer slide under the buttons.** The details list in the job panel could be squeezed to fit, and its rows then showed up underneath **Open in Search** and **Promote again**. The details now keep their full height and the panel scrolls.

## 2.1.0 (2026-11-24)

`promotion_control` 2.1.0 · `TA-promotion_agent` 2.1.0

### Changes
- **Sourcetype sampling.** With each catalog refresh, the agent reads the first events of the newest files per sourcetype. It reports the indexes, hosts, sources and indexed field *names* it sees there. Event text and field values are never sent. Unchanged sourcetypes are not read again, and on S3 only the start of an object is fetched.
- **Index suggestions.** The index filter suggests the sampled indexes. Wildcards still work.

## 2.0.1 (2026-11-24)

`promotion_control` 2.0.0 (unchanged) · `TA-promotion_agent` 2.0.1

### Changes
- **Promoted events use Splunk's built-in `stash_hec` input.** The add-on no longer ships its own batch input or `props.conf`.

## 2.0.0 (2026-11-24)

`promotion_control` 2.0.0 · `TA-promotion_agent` 2.0.0

### Breaking changes
**Data Replay is now Data Promotion.** Everything is renamed, with no backward compatibility:
- Apps `promotion_control` and `TA-promotion_agent` (were `replay_control` and `TA-replay_agent`).
- KV store collections `promotion_*`, macros `promote_submit` / `promote_cancel` / `promote_verify`, role `promotion_user`, saved searches `Promotion - …`.
- Agent settings in `promotion_agent.conf`, credential realm `promotion_agent`.
- Indexed field `promote_job` (was `replay_job`).

To upgrade, install both new apps, move your agent settings to `promotion_agent.conf`, store the Search Head token under the realm `promotion_agent`, then remove the old apps. Job history isn't carried over.

## 1.5.0 (2026-11-24)

`replay_control` 1.5.0 · `TA-replay_agent` 1.3.0

### Changes
- **Agent view.** Click an agent to see its data on the timeline and replay a range from that agent only.
- **Destination index.** Send a job's events to one index instead of their original ones.
- **Open in Search** for completed jobs. **Replay again** keeps the destination index.

## 1.4.1 (2026-11-23)

`replay_control` 1.4.1 · `TA-replay_agent` 1.2.1 (unchanged)

### Changes
- **Status as icon and text** for jobs, agent tasks and agents, in theme colours.
- **Forwarders are now called Agents** throughout the page.

### Fixes
- **The job details panel scrolls**, with the job id and **Close** kept at the top.

## 1.4.0 (2026-11-23)

`replay_control` 1.4.0 · `TA-replay_agent` 1.2.1 (unchanged)

### Changes
- **Pick the time range on a timeline** of the data the forwarders hold, with earlier replays shown and a warning when the range overlaps them.

## 1.3.1 (2026-11-23)

`replay_control` 1.3.1 · `TA-replay_agent` 1.2.1

### Changes
- **Replay from every forwarder.** Each forwarder runs the agent and replays its own files. Pick all forwarders or any set of them per job.
- **Progress per forwarder.** Forwarders that fail or don't report make the job `incomplete`, with the reason.
- **Forwarders register themselves.**
- **New page** built with the Splunk UI Toolkit, in light and dark themes.

### Fixes
- New KV store records no longer fail with HTTP 404.

## 1.1.0 (2026-11-23)

`replay_control` 1.1.0 · `TA-replay_agent` 1.0.0

First release: replay Ingest Actions file system or S3 output into Splunk by sourcetype and time range, from the page or the `replay_*` macros, with indexing tracked on the Search Head.
