<h1 align="center">Data Promotion for Splunk</h1>

<p align="center">
  <a href="../../releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/inspired/data_promotion?style=flat-square&label=download&color=2E7D32"></a>
  <img alt="Splunk Enterprise" src="https://img.shields.io/badge/Splunk-Enterprise-0B6FA4?style=flat-square">
  <img alt="Ingest Actions: file system and S3" src="https://img.shields.io/badge/Ingest%20Actions-file%20system%20%7C%20S3-0B6FA4?style=flat-square">
  <a href="LICENSE"><img alt="License: Apache 2.0" src="https://img.shields.io/badge/license-Apache%202.0-6B7078?style=flat-square"></a>
</p>

<p align="center">
  <a href="#try-it"><b>Try it</b></a> ·
  <a href="docs/user-guide.md"><b>User guide</b></a> ·
  <a href="docs/admin-guide.md"><b>Admin guide</b></a> ·
  <a href="CHANGELOG.md"><b>Changelog</b></a> ·
  <a href="../../issues"><b>Report a problem</b></a>
</p>

---

**The data you need for an investigation is sitting on a disk at a remote site.** Splunk [Ingest Actions](https://docs.splunk.com/Documentation/Splunk/latest/Data/DataIngest) can keep a full copy of your data in cheap storage and send only what you search every day to the indexers. That saves license and storage, but the copy is out of reach: hourly files on disks and buckets at many sites, which nobody can search.

**Data Promotion** makes that copy usable. Each copy is an **edge archive**, kept at the site next to the forwarder that wrote it. A small agent beside every archive tells the Search Head what it holds. When you need it, pick a **sourcetype, a time range and the sites**, and press **Promote**. Each agent sends exactly those events from its own archive into Splunk, tagged so you can always tell them apart.

<p align="center">
  <img src="docs/edge-archives.svg" alt="Remote sites each hold an edge archive (Ingest Actions output on local disk, a share or S3). A promotion agent next to each archive reports to the Search Head and, on request, sends the selected events to the indexers." width="100%">
</p>

## What it does

<table>
  <tr>
    <td width="33%" valign="top">
      <h3>See what you have</h3>
      Every agent publishes what its edge archive holds, per sourcetype and day: files, size and time span. Event contents never leave the site until you promote them.
    </td>
    <td width="33%" valign="top">
      <h3>Pick on a timeline</h3>
      Select the range on a timeline of the data the edge archives hold, with earlier promotions shown, so you don't index the same events twice.
    </td>
    <td width="33%" valign="top">
      <h3>Promote exactly</h3>
      Every event is filtered on its own timestamp, so the range is exact to the second. Narrow it by original index, and send it to its original index or to one you choose.
    </td>
  </tr>
  <tr>
    <td valign="top">
      <h3>Move only what you ask for</h3>
      Archives stay at the edge. Only the selected events cross the network, through each forwarder's normal outputs. Source files are never changed or deleted.
    </td>
    <td valign="top">
      <h3>Scale to fleets</h3>
      Thousands of forwarders register themselves and report progress on their own. Promote from all of them, or only from the sites you pick.
    </td>
    <td valign="top">
      <h3>Follow every job</h3>
      Progress per agent, a live count of indexed events, and <b>Open in Search</b>. Every promoted event carries <code>promote_job=&lt;job id&gt;</code>.
    </td>
  </tr>
</table>

## See it in action

**One page on the Search Head.** Start a promotion at the top, and follow every job, the data the edge archives hold, and your agents below.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/overview-dark.png">
    <img alt="The Data Promotion page: the New promotion form above a list of jobs in every state, from pending to completed" src="docs/images/overview-light.png" width="100%">
  </picture>
</p>

**Pick the range on a timeline.** See which days the edge archives hold and where earlier promotions ran. Drag to select. A warning names any earlier promotions the range overlaps, before you index the same events twice.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/timeline-dark.png">
    <img alt="The timeline for splunkd: the data held per day, earlier promotions as coloured bars, a 24-hour selection and a warning that it overlaps three earlier promotions" src="docs/images/timeline-light.png" width="100%">
  </picture>
</p>

<table>
  <tr>
    <td width="40%" valign="top">
      <b>Follow every job.</b> Files read, events promoted and indexed, how long it took, and the progress of every agent. Open the promoted events in Search with one click.
      <br><br>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/images/job-dark.png">
        <img alt="Job details for a completed promotion from 1,500 agents: 844 million events promoted and indexed, with Open in Search and progress per agent" src="docs/images/job-light.png" width="100%">
      </picture>
    </td>
    <td width="60%" valign="top">
      <b>Promote from one site.</b> Open an agent to see what its edge archive holds, and promote a range from that agent only.
      <br><br>
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="docs/images/agent-dark.png">
        <img alt="One agent's view: its datasets per sourcetype, its splunkd data on the timeline with an 18-hour selection, and a Promote from this agent button" src="docs/images/agent-light.png" width="100%">
      </picture>
    </td>
  </tr>
</table>

<sub>Screenshots show sample data.</sub>

## Try it

The quickest way is an all-in-one Splunk instance that already writes Ingest Actions output to its own file system.

1. **Download** `promotion_control-<version>.spl` and `TA-promotion_agent-<version>.spl` from the [latest release](../../releases/latest).
2. **Install** both: **Apps → Manage Apps → Install app from file**, then restart.
3. **Point the agent at the edge archive.** Create `$SPLUNK_HOME/etc/apps/TA-promotion_agent/local/promotion_agent.conf`:
   ```ini
   [agent]
   fs_root = /path/to/your/ingest-actions/output
   ```
4. **Open Data Promotion.** After a few minutes the **Agents** tab shows the agent as reporting and **Available data** lists what it holds. Pick a sourcetype and a time range, then **Promote**.

To run it across sites, with a Search Head and many forwarders, see the [admin guide](docs/admin-guide.md).

> [!IMPORTANT]
> Promoted events pass through the forwarder's Ingest Actions rules again. Add the condition `isnull(promote_job)` to the rule that writes the edge archive, or promoted events are written back to it. See [Ingest Actions rules](docs/admin-guide.md#ingest-actions-rules).

## Documentation

| | |
|---|---|
| [**User guide**](docs/user-guide.md) | Promote data, use the timeline, follow jobs, and promote from search. |
| [**Admin guide**](docs/admin-guide.md) | Install, connect forwarders, Ingest Actions rules, settings and limits. |
| [**Changelog**](CHANGELOG.md) | What changed in every release. |

## What you install

| Package | Install on |
|---|---|
| `promotion_control` | The Search Head. On a search head cluster, through the deployer. |
| `TA-promotion_agent` | Every Heavy Forwarder that writes Ingest Actions output to a file system or S3. Deploy it with the Deployment Server. |

On an all-in-one instance, install both on the same server.

## Build from source

```sh
cd ui && npm ci && npm run package          # dist/promotion_control-<version>.spl
cd .. && COPYFILE_DISABLE=1 tar --no-xattrs -czf dist/TA-promotion_agent-<version>.spl TA-promotion_agent
python3 -m unittest discover -s tests       # agent tests
(cd ui && npm test)                         # UI tests
```

More in [Development](docs/admin-guide.md#development).

## Credits

Made by [Mikael Bjerkeland](https://github.com/inspired).

## License

[Apache License 2.0](LICENSE).
