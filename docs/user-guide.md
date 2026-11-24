# User guide

Data Promotion brings data back from the **Data Puddles**, the Ingest Actions copies kept at remote sites, into Splunk when you need it. This guide covers the **Data Promotion** page and the search macros.

To promote data you need the `promotion_user` role. Ask your Splunk admin if you don't see the app.

- [Promote data](#promote-data)
- [Pick a range on the timeline](#pick-a-range-on-the-timeline)
- [Follow a job](#follow-a-job)
- [See what the puddles hold](#see-what-the-puddles-hold)
- [Promote from search](#promote-from-search)
- [Find promoted events](#find-promoted-events)
- [Job states](#job-states)
- [Good to know](#good-to-know)

## Promote data

1. Open **Data Promotion**. The promotion form is at the top of the page.
2. Pick a **sourcetype**.
3. Pick a **time range**, or **Pick on a timeline** to see the data first.
4. Optionally:
   - **Agents**: the sites to promote from. Search by hostname or GUID. Leave it empty for all agents.
   - **Index filter**: only events whose original index matches, for example `os_*`. The list suggests the indexes the agents have seen for that sourcetype.
   - **Destination index**: send every event to this index instead of its original one. The index must already exist on the indexers.
5. Press **Promote**.

Each agent that holds matching data reads its own files, keeps only the events inside your range, and sends them to Splunk. You can follow the job on the **Jobs** tab.

## Pick a range on the timeline

Click a sourcetype or a day on **Available data**, or **Pick on a timeline** in the form. The timeline shows:

- the **data the puddles hold** for that sourcetype, per UTC day;
- **earlier promotions** of that sourcetype, as bars coloured by status. Click a bar to open that job.

To select a range:

- **Drag** to select. Drag inside the selection to move it, or drag an edge to resize it.
- **Click a day** to select that whole day.
- **Type** the start and end (`YYYY-MM-DD HH:MM`, UTC). Times snap to whole hours.

To move around: use the zoom buttons, **Ctrl/⌘ + scroll** (or pinch) to zoom, and horizontal scroll to pan. Arrow keys move the selection; **Shift + arrow** resizes it.

If the range overlaps earlier promotions, a warning lists them: those events would be indexed again.

## Follow a job

Click a job on the **Jobs** tab to open its details:

- **Status and progress**, with files read and events promoted and indexed.
- **Progress per agent**: every site's share of the job, problems first.
- **Indexed events**: press **Count now** to count the searchable events by index, sourcetype and host.
- **Open in Search**: searches `promote_job=<job id>` over the job's time range. Shown once events are indexed.
- **Cancel job**: every agent stops after the file it is reading. Events already sent are still indexed.
- **Promote again**: starts a new job with the same settings. Events that are already indexed are indexed again.

## See what the puddles hold

- **Available data**: what the puddles hold, one row per sourcetype and UTC day, with the number of agents, files, size, and the first and last hour.
- **Agents**: every agent with its status (**Reporting**, **Not reporting** after 15 minutes of silence, or **Not registered**), its sourcetypes, files, size and when it was last seen. Click an agent to see what its puddle holds, pick a sourcetype to see it on the timeline, select a range and press **Promote from &lt;agent&gt;**.

The catalog is refreshed every 15 minutes, so very recent data can take a little while to appear.

## Promote from search

The search's time picker sets the range.

| Task | Search |
|---|---|
| Promote | ``| `promote_submit(<sourcetype>, <index filter>, <agents>, <destination index>)` `` |
| All indexes, all agents, original index | ``| `promote_submit(splunkd, *, *, *)` `` |
| Two agents, into `promotion_test` | ``| `promote_submit(splunkd, *, 5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8 BF6301FD-4484-45FA-802E-F657F1AFB5F7, promotion_test)` `` |
| Cancel a job | ``| `promote_cancel(<job id>)` `` |
| Count a job's indexed events | ``| `promote_verify(<job id>)` `` |

Agents are given as server GUIDs separated by spaces, or `*` for all.

## Find promoted events

Every promoted event carries the indexed field `promote_job`:

| To find | Search |
|---|---|
| One job's events | `promote_job=<job id>` |
| All promoted events | `promote_job=*` |
| Only original events | `NOT promote_job=*` |

## Job states

| Status | Meaning |
|---|---|
| `pending` | Submitted. No agent has picked it up yet. |
| `running` | Agents are promoting (`n of m agents finished`). |
| `staged` / `indexing` | Every agent finished. Waiting until every promoted event is searchable. |
| `completed` | Every promoted event is searchable and every agent succeeded. |
| `incomplete` | Some agents failed or didn't report, or events were still missing after 30 minutes. It is checked again for 24 hours. The job's message says why. |
| `failed` | No agent picked the job up within 30 minutes, or every agent failed. |
| `rejected` | The job couldn't start, for example because no agent holds the data. |
| `cancel_requested` → `cancelled` | Cancelled. Every agent stops after its current file. |

## Good to know

- **Duplicates.** Promoting a range that is still in Splunk indexes those events again. Use `promote_job` to tell them apart, or send them to a separate destination index.
- **License.** Promoted data counts against your ingest license.
- **Old data.** Events older than an index's retention period are frozen almost immediately after they are indexed.
- **Exact ranges.** Files cover an hour each, but every event is filtered on its own timestamp, so the range is exact to the second.
- **Size limit.** An agent reads at most 50 GiB for one job by default. Your admin can change this.
