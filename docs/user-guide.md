# User guide

Data Promotion brings data back from the **edge archives**, the Ingest Actions copies kept at remote sites, into Splunk when you need it. This guide covers the **Data Promotion** page.

To promote data you need the `promotion_user` role. Ask your Splunk admin if you don't see the app.

- [Promote data](#promote-data)
- [Pick a range on the timeline](#pick-a-range-on-the-timeline)
- [Follow a job](#follow-a-job)
- [See what the edge archives hold](#see-what-the-edge-archives-hold)
- [Promote from SOAR or a script](#promote-from-soar-or-a-script)
- [Find promoted events](#find-promoted-events)
- [Job states](#job-states)
- [Good to know](#good-to-know)

## Promote data

1. Open **Data Promotion**. The promotion form is at the top of the page.
2. Pick a **sourcetype**.
3. Pick a **time range**, or **Pick on a timeline** to see the data first.
4. Optionally:
   - **Forwarders**: the sites to promote from. Search by hostname or GUID. Leave it empty for all forwarders.
   - **Index filter**: only events whose original index matches, for example `os_*`. The list suggests the indexes the forwarders have seen for that sourcetype.
   - **Destination index**: send every event to this index instead of its original one. The index must already exist on the indexers.
     For ranges longer than three days, the page suggests one. Old events sent back to an index that also takes live data make its buckets span weeks, which slows searches there.
5. Check the **estimated size** below the form: about how much the forwarders will read, and the most any one forwarder will read. If a forwarder would read more than its limit, a warning names it. That forwarder fails the job without promoting anything, while the others still promote their share. Pick a shorter range and promote the rest as separate jobs, or ask your admin to raise the limit.
6. Press **Promote**.

Each forwarder that holds matching data reads its own files, keeps only the events inside your range, and sends them to Splunk. You can follow the job on the **Jobs** tab.

## Pick a range on the timeline

Click a sourcetype or a day on **Available data**, or **Pick on a timeline** in the form. The timeline shows:

- the **data the edge archives hold** for that sourcetype, per UTC day;
- **earlier promotions** of that sourcetype, as bars coloured by status. Click a bar to open that job.

To select a range:

- **Drag** to select. Drag inside the selection to move it, or drag an edge to resize it.
- **Click a day** to select that whole day.
- **Type** the start and end (`YYYY-MM-DD HH:MM`, UTC). Times snap to whole hours.

To move around: use the zoom buttons, **Ctrl/⌘ + scroll** (or pinch) to zoom, and horizontal scroll to pan. Arrow keys move the selection; **Shift + arrow** resizes it.

The estimated size and the size-limit warning are shown here too.

If the range overlaps earlier promotions, a warning lists them: those events would be indexed again.

## Follow a job

Click a job on the **Jobs** tab to open its details:

- **Status and progress**, with files read and events promoted and indexed.
- **Progress per forwarder**: every site's share of the job, problems first.
- **Indexed events**: press **Count now** to count the searchable events by index, sourcetype and host.
- **Open in Search**: searches `promote_job=<job id>` over the job's time range. Shown once events are indexed.
- **Cancel job**: every forwarder stops after the file it is reading. Events already sent are still indexed.
- **Promote again**: starts a new job with the same settings. Events that are already indexed are indexed again.

## See what the edge archives hold

- **Available data**: what the edge archives hold, one row per sourcetype and UTC day, with the number of forwarders, files, size, and the first and last hour.
- **Forwarders**: every forwarder with its status (**Reporting**, **Not reporting** after 15 minutes of silence, or **Not registered**), its sourcetypes, files, size and when it was last seen. Click a forwarder to see what its edge archive holds, pick a sourcetype to see it on the timeline, select a range and press **Promote from &lt;forwarder&gt;**.

The catalog is refreshed every 15 minutes, so very recent data can take a little while to appear.

## Promote from SOAR or a script

Everything the page does goes through the app's REST API, so Splunk SOAR, scripts and other tools can promote data too: list what the edge archives hold, estimate, create a job, follow it and count its events. See the [REST API](api.md) reference, which has a SOAR example.

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
| `pending` | Submitted. No forwarder has picked it up yet. |
| `running` | Forwarders are promoting (`n of m forwarders finished`). |
| `staged` / `indexing` | Every forwarder finished. Waiting until every promoted event is searchable. |
| `completed` | Every promoted event is searchable and every forwarder succeeded. |
| `incomplete` | Some forwarders failed or didn't report, or events were still missing after 30 minutes. It is checked again for 24 hours. The job's message says why. |
| `failed` | No forwarder picked the job up within 30 minutes, or every forwarder failed. |
| `cancel_requested` → `cancelled` | Cancelled. Every forwarder stops after its current file. |

## Good to know

- **Duplicates.** Promoting a range that is still in Splunk indexes those events again. Use `promote_job` to tell them apart, or send them to a separate destination index.
- **License.** Promoted data counts against your ingest license.
- **Old data.** Events older than an index's retention period are frozen almost immediately after they are indexed.
- **Exact ranges.** Files cover an hour each, but every event is filtered on its own timestamp, so the range is exact to the second.
- **Size limit.** A forwarder reads at most 50 GiB for one job by default. Your admin can change this.
