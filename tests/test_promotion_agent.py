"""Tests for the promotion agent. Run: python3 -m unittest discover -s tests -v"""
import copy
import datetime
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "TA-promotion_agent", "bin"))
import promotion_agent as ra  # noqa: E402

# 2026-11-19 06:00:00 UTC, matches the sample file events_1795071600_1795068000_...
H06 = 1795068000
HOUR = 3600
GUID = "5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8"
GUID2 = "BF6301FD-4484-45FA-802E-F657F1AFB5F7"
AGENT = {"guid": GUID, "hostname": "hf1"}


class FakeKV:
    def __init__(self):
        self.data = {}

    def _c(self, coll):
        return self.data.setdefault(coll, {})

    def query(self, coll, query=None, sort=None, limit=None):
        rows = [copy.deepcopy(r) for r in self._c(coll).values()]

        def ok(r):
            for k, v in (query or {}).items():
                if isinstance(v, dict) and "$in" in v:
                    if r.get(k) not in v["$in"]:
                        return False
                elif isinstance(v, dict) and "$lt" in v:
                    if not r.get(k, 0) < v["$lt"]:
                        return False
                elif r.get(k) != v:
                    return False
            return True
        rows = [r for r in rows if ok(r)]
        if sort:
            rows.sort(key=lambda r: r.get(sort, 0))
        return rows

    def get(self, coll, key):
        r = self._c(coll).get(key)
        return copy.deepcopy(r) if r is not None else None

    def put(self, coll, key, rec):
        rec = dict(rec, _key=key)
        self._c(coll)[key] = copy.deepcopy(rec)

    def batch_save(self, coll, recs):
        for r in recs:
            self.put(coll, r["_key"], r)

    def delete(self, coll, query):
        for r in self.query(coll, query):
            del self._c(coll)[r["_key"]]


def event(t, index="_internal", st="splunkd", fields=True):
    ev = {"time": t, "event": "msg at %s" % t, "host": "h1",
          "source": "/opt/splunk/var/log/splunk/splunkd.log",
          "sourcetype": st, "index": index}
    if fields:
        ev["fields"] = {"node_guid": "abc"}
    return ev


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.dumps = os.path.join(root, "dumps")
        self.cfg = ra.Config(app_dir=os.path.join(root, "noapp"), overrides={"agent": {
            "fs_root": self.dumps,
            "work_dir": os.path.join(root, "work"),
            "staging_dir": os.path.join(root, "staging"),
            "min_free_bytes": "0",
        }})
        os.makedirs(self.cfg.get("agent", "work_dir"))
        os.makedirs(self.cfg.get("agent", "staging_dir"))
        self.kv = FakeKV()
        self.backend = ra.FilesystemBackend(self.dumps)

    def tearDown(self):
        self.tmp.cleanup()

    def write_hour(self, start, events, st="splunkd", gz=False, guid=GUID):
        d = datetime.datetime.fromtimestamp(start, datetime.timezone.utc)
        p = os.path.join(self.dumps, "year=%d" % d.year, "month=%02d" % d.month,
                         "day=%02d" % d.day, "sourcetype=%s" % st)
        os.makedirs(p, exist_ok=True)
        name = "events_%d_%d_0_000000_%s.ndjson" % (start + HOUR, start, guid)
        body = "".join(json.dumps(e) + "\n" for e in events).encode()
        if gz:
            import gzip
            name += ".gz"
            body = gzip.compress(body)
        with open(os.path.join(p, name), "wb") as f:
            f.write(body)
        return name

    def submit(self, earliest, latest, st="splunkd", idx="*", key="job1", fwd=None, target=None):
        job = {"job_id": key, "sourcetype": st, "index_filter": idx,
               "earliest": earliest, "latest": latest, "status": "pending", "submitted_at": 1}
        if fwd is not None:
            job["forwarders"] = fwd
        if target is not None:
            job["target_index"] = target
        self.kv.put(ra.JOBS, key, job)

    def agent_run(self, agent=AGENT, served=None):
        """One agent cycle: catalog (for 'served') + jobs, batch output."""
        if served is None:
            served = ra.build_catalog(self.backend, agent)[2]
        ra.process_jobs(self.cfg, self.backend, self.kv, agent, served, self.sink)

    def sink(self, job_key):
        return ra.make_sink(self.cfg, job_key)

    def task(self, key="job1", agent=AGENT):
        return self.kv.get(ra.TASKS, ra.task_key(key, agent["guid"]))

    def staged_events(self, key="job1"):
        d = os.path.join(self.cfg.get("agent", "staging_dir"), key)
        out = []
        for n in sorted(os.listdir(d)):
            with open(os.path.join(d, n)) as f:
                out += [json.loads(l) for l in f if l.strip()]
        return out


class TestFileSelection(Base):
    def test_parse_file_range_either_order(self):
        self.assertEqual(ra.parse_file_range(
            "events_1795071600_1795068000_0_000000_%s.ndjson" % GUID), (H06, H06 + HOUR))
        self.assertEqual(ra.parse_file_range(
            "events_1795068000_1795071600_0_000000_x.ndjson.gz"), (H06, H06 + HOUR))
        self.assertIsNone(ra.parse_file_range("something_else.ndjson"))

    def test_selects_overlapping_hours_with_slack(self):
        for h in range(6):
            self.write_hour(H06 + h * HOUR, [event(H06 + h * HOUR + 1)])
        # 08:30 - 09:30, no slack -> 08:00 and 09:00 files
        files = ra.select_files(self.backend, "splunkd", H06 + 2.5 * HOUR, H06 + 3.5 * HOUR, 0)
        self.assertEqual([ra.parse_file_range(n)[0] for n, _, _ in files],
                         [H06 + 2 * HOUR, H06 + 3 * HOUR])
        # 1h slack widens by one file each side
        files = ra.select_files(self.backend, "splunkd", H06 + 2.5 * HOUR, H06 + 3.5 * HOUR, HOUR)
        self.assertEqual(len(files), 4)

    def test_crosses_utc_midnight(self):
        midnight = 1795132800  # 2026-11-20 00:00 UTC
        self.write_hour(midnight - HOUR, [event(midnight - 10)])
        self.write_hour(midnight, [event(midnight + 10)])
        files = ra.select_files(self.backend, "splunkd", midnight - 60, midnight + 60, 0)
        self.assertEqual(len(files), 2)

    def test_unpadded_partitions(self):
        p = os.path.join(self.dumps, "year=2026", "month=11", "day=5", "sourcetype=splunkd")
        os.makedirs(p)
        start = 1793836800  # 2026-11-05 00:00 UTC
        open(os.path.join(p, "events_%d_%d_0_0_x.ndjson" % (start + HOUR, start)), "w").close()
        self.assertEqual(len(ra.select_files(self.backend, "splunkd", start, start + 60, 0)), 1)


class TestStaging(unittest.TestCase):
    def test_staged_name_matches_builtin_stash_hec_input(self):
        base = "events_1795071600_1795068000_0_000000_%s" % GUID
        for src in (base + ".ndjson", base + ".ndjson.gz"):
            self.assertEqual(ra.staged_name(src), base + ".stash_hec")


class TestRunJob(Base):
    def test_filters_by_event_time_and_tags(self):
        evs = [event(H06 + m * 60) for m in range(60)]
        self.write_hour(H06, evs)
        self.write_hour(H06 + HOUR, [event(H06 + HOUR + 5)])
        self.submit(H06 + 10 * 60, H06 + 20 * 60)
        self.agent_run()

        t = self.task()
        self.assertEqual(t["status"], "done", t.get("message"))
        self.assertEqual((t["events_staged"], t["agent_host"]), (10, "hf1"))
        out = self.staged_events()
        self.assertEqual([e["time"] for e in out], [H06 + m * 60 for m in range(10, 20)])
        for e in out:
            self.assertEqual(e["fields"], {"node_guid": "abc", "promote_job": "job1"})
            self.assertEqual(e["index"], "_internal")
            self.assertEqual(e["sourcetype"], "splunkd")
        self.assertEqual(os.listdir(self.cfg.get("agent", "work_dir")), [])
        # The agent never writes the job record.
        self.assertEqual(self.kv.get(ra.JOBS, "job1")["status"], "pending")

    def test_runs_each_job_once(self):
        self.write_hour(H06, [event(H06 + 5)])
        self.submit(H06, H06 + HOUR)
        self.agent_run()
        os.remove(os.path.join(self.cfg.get("agent", "staging_dir"), "job1",
                               os.listdir(os.path.join(self.cfg.get("agent", "staging_dir"), "job1"))[0]))
        self.agent_run()
        self.assertEqual(self.staged_events(), [])

    def test_adds_fields_when_missing_and_reads_gzip(self):
        self.write_hour(H06, [event(H06 + 5, fields=False)], gz=True)
        self.submit(H06, H06 + HOUR)
        self.agent_run()
        out = self.staged_events()
        self.assertEqual(out[0]["fields"], {"promote_job": "job1"})
        self.assertTrue(all(n.endswith(".stash_hec") for n in
                            os.listdir(os.path.join(self.cfg.get("agent", "staging_dir"), "job1"))))

    def test_skips_events_from_earlier_promotions(self):
        promoted = event(H06 + 2)
        promoted["fields"]["promote_job"] = "older"
        self.write_hour(H06, [event(H06 + 1), promoted])
        self.submit(H06, H06 + HOUR)
        self.agent_run()
        t = self.task()
        self.assertEqual((t["events_staged"], t["events_skipped"]), (1, 1))


    def test_index_filter_and_bad_lines(self):
        name = self.write_hour(H06, [event(H06 + 1, index="main"), event(H06 + 2, index="_internal")])
        path = os.path.join(self.dumps, "year=2026", "month=11", "day=19", "sourcetype=splunkd", name)
        with open(path, "a") as f:
            f.write("not json\n{\"event\":\"no time\"}\n")
        self.submit(H06, H06 + HOUR, idx="_int*")
        self.agent_run()
        t = self.task()
        self.assertEqual((t["events_staged"], t["events_skipped"], t["events_bad"]), (1, 1, 2))

    def test_destination_index(self):
        self.write_hour(H06, [event(H06 + 1, index="main"), event(H06 + 2, index="_internal")])
        self.submit(H06, H06 + HOUR, idx="_int*", target="promotion_test")
        self.agent_run()
        out = self.staged_events()
        self.assertEqual([(e["time"], e["index"]) for e in out], [(H06 + 2, "promotion_test")])
        self.assertEqual(out[0]["fields"]["promote_job"], "job1")

    def test_destination_index_without_tagging(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.cfg.cp.set("agent", "tag_events", "false")
        self.submit(H06, H06 + HOUR, target="promotion_test")
        self.agent_run()
        out = self.staged_events()
        self.assertEqual(out[0]["index"], "promotion_test")
        self.assertNotIn("promote_job", out[0]["fields"])

    def test_original_index_when_no_destination(self):
        for target in ("", "*"):
            key = "job_%s" % ("empty" if not target else "star")
            self.write_hour(H06, [event(H06 + 1)])
            self.submit(H06, H06 + HOUR, key=key, target=target)
            self.agent_run()
            self.assertEqual(self.staged_events(key)[0]["index"], "_internal")

    def test_invalid_destination_index_fails(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.submit(H06, H06 + HOUR, target="Bad Index")
        self.agent_run()
        t = self.task()
        self.assertEqual(t["status"], "failed")
        self.assertIn("destination index", t["message"])

    def test_no_files_in_range(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.submit(H06 + 10 * HOUR, H06 + 11 * HOUR)
        self.agent_run()
        t = self.task()
        self.assertEqual((t["status"], t["events_staged"]), ("done", 0))
        self.assertIn("no files", t["message"])

    def test_not_targeted_creates_no_task(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.submit(H06, H06 + HOUR, st="other")
        self.submit(H06, H06 + HOUR, key="job2", fwd=GUID2)
        self.agent_run()
        self.assertEqual(self.kv.query(ra.TASKS), [])

    def test_invalid_jobs_fail(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.submit(H06 + HOUR, H06, key="bad_range")
        self.agent_run()
        self.assertEqual(self.task("bad_range")["status"], "failed")
        self.assertIn("time range", self.task("bad_range")["message"])

    def test_size_limit(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.cfg.cp.set("agent", "max_bytes_per_job", "10")
        self.submit(H06, H06 + HOUR)
        self.agent_run()
        t = self.task()
        self.assertEqual(t["status"], "failed")
        self.assertIn("max_bytes_per_job", t["message"])

    def test_cancel_while_running(self):
        for h in range(3):
            self.write_hour(H06 + h * HOUR, [event(H06 + h * HOUR + 1)])
        self.submit(H06, H06 + 3 * HOUR)
        real_put = self.kv.put
        fired = []

        def put(coll, key, rec):
            real_put(coll, key, rec)
            if coll == ra.TASKS and rec.get("files_done") == 1 and not fired:
                fired.append(1)
                self.kv.data[ra.JOBS]["job1"]["status"] = "cancel_requested"
        self.kv.put = put
        ra.Task.__init__.__defaults__ = (0,)  # save progress after every file
        try:
            self.agent_run()
        finally:
            ra.Task.__init__.__defaults__ = (5,)
        self.assertEqual(self.task()["status"], "cancelled")
        self.assertEqual(len(self.staged_events()), 1)

    def test_disk_guard_while_writing(self):
        frees = iter([100, 5])
        w = ra.GuardedWriter(open(os.devnull, "wb"), lambda: next(frees), min_free=10, check_every=4)
        w.write(b"abcd")  # check: 100 free
        with self.assertRaises(ra.JobError):
            w.write(b"abcd")  # check: 5 free < 10

    def test_low_disk_fails_task_and_cleans_work_dir(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.cfg.cp.set("agent", "min_free_bytes", str(10 ** 18))
        self.submit(H06, H06 + HOUR)
        self.agent_run()
        t = self.task()
        self.assertEqual(t["status"], "failed")
        self.assertIn("free disk space", t["message"])
        self.assertFalse(os.path.exists(os.path.join(self.cfg.get("agent", "work_dir"), "job1")))


class TestBackPressure(Base):
    """Staged *.stash_hec files are read by Splunk; here a fake clock and a fake 'input'
    that drains the staging dir stand in for it."""

    def setUp(self):
        super().setUp()
        for h in range(3):
            self.write_hour(H06 + h * HOUR, [event(H06 + h * HOUR + 1)])
        self.cfg.cp.set("agent", "max_staged_bytes", "1")  # one staged file is already too much
        self.cfg.cp.set("agent", "staging_stall_seconds", "60")
        self.clock = [0.0]
        self.sleeps = 0
        self.on_sleep = lambda: None
        self._orig = (ra._sleep, ra._now)
        ra._now = lambda: self.clock[0]

        def sleep(sec):
            self.sleeps += 1
            self.clock[0] += sec
            self.on_sleep()
        ra._sleep = sleep

    def tearDown(self):
        ra._sleep, ra._now = self._orig
        super().tearDown()

    def drain(self):
        """What the stash_hec batch input does: index and delete staged files."""
        root = self.cfg.get("agent", "staging_dir")
        for dirpath, _, names in os.walk(root):
            for n in names:
                if n.endswith(ra.STAGED_SUFFIX):
                    os.remove(os.path.join(dirpath, n))

    def test_waits_for_input_to_read_staged_files(self):
        self.on_sleep = self.drain
        self.submit(H06, H06 + 3 * HOUR)
        self.agent_run()
        t = self.task()
        self.assertEqual(t["status"], "done")
        self.assertEqual(t["events_staged"], 3)
        self.assertEqual(self.sleeps, 2)  # waited before the 2nd and 3rd file

    def test_counts_files_left_by_other_jobs(self):
        other = os.path.join(self.cfg.get("agent", "staging_dir"), "older_job")
        os.makedirs(other)
        with open(os.path.join(other, "x.stash_hec"), "wb") as f:
            f.write(b"{}\n")
        self.on_sleep = self.drain
        self.submit(H06, H06 + 3 * HOUR)
        self.agent_run()
        self.assertEqual(self.task()["status"], "done")
        self.assertEqual(self.sleeps, 3)

    def test_fails_when_input_never_reads(self):
        self.submit(H06, H06 + 3 * HOUR)
        self.agent_run()
        t = self.task()
        self.assertEqual(t["status"], "failed")
        self.assertIn("stash_hec", t["message"])
        self.assertEqual(t["files_done"], 1)

    def test_slow_progress_is_not_a_stall(self):
        # Staged bytes keep shrinking, a little at a time, for longer than the stall limit.
        staged = os.path.join(self.cfg.get("agent", "staging_dir"), "older_job")
        os.makedirs(staged)
        for i in range(30):
            with open(os.path.join(staged, "f%02d.stash_hec" % i), "wb") as f:
                f.write(b"x" * 10)
        self.cfg.cp.set("agent", "max_staged_bytes", "0")

        def drain_one():
            names = sorted(os.listdir(staged))
            if names:
                os.remove(os.path.join(staged, names[0]))
            else:
                self.drain()
        self.on_sleep = drain_one
        self.submit(H06, H06 + HOUR)
        self.agent_run()
        self.assertEqual(self.task()["status"], "done")
        self.assertGreater(self.clock[0], 60)

    def test_cancel_while_waiting(self):
        def cancel():
            self.kv.data[ra.JOBS]["job1"]["status"] = "cancel_requested"
        self.on_sleep = cancel
        self.submit(H06, H06 + 3 * HOUR)
        self.agent_run()
        t = self.task()
        self.assertEqual(t["status"], "cancelled")
        self.assertEqual(t["files_done"], 1)

    def test_recover_stale(self):
        self.kv.put(ra.TASKS, "j|" + GUID, {"agent": GUID, "status": "running"})
        self.kv.put(ra.TASKS, "j|" + GUID2, {"agent": GUID2, "status": "running"})
        ra.recover_stale(self.kv, AGENT)
        self.assertEqual(self.kv.get(ra.TASKS, "j|" + GUID)["status"], "failed")
        self.assertEqual(self.kv.get(ra.TASKS, "j|" + GUID2)["status"], "running")


class TestManyAgents(Base):
    """Two agents (HF1 and HF2), each with its own local dumps, sharing one job."""

    def test_each_agent_promotes_its_own_files(self):
        self.write_hour(H06, [event(H06 + 1)], guid=GUID)
        other = ra.FilesystemBackend(os.path.join(self.tmp.name, "dumps2"))
        self.dumps, keep = other.root, self.dumps
        self.write_hour(H06, [event(H06 + 2), event(H06 + 3)], guid=GUID2)
        self.dumps = keep
        agent2 = {"guid": GUID2, "hostname": "hf2"}

        self.submit(H06, H06 + HOUR, fwd="*")
        self.agent_run()
        ra.process_jobs(self.cfg, other, self.kv, agent2, ra.build_catalog(other, agent2)[2], self.sink)
        self.assertEqual(self.task()["events_staged"], 1)
        self.assertEqual(self.task(agent=agent2)["events_staged"], 2)

    def test_catalogs_do_not_overwrite_each_other(self):
        self.write_hour(H06, [event(H06 + 1)], guid=GUID)
        other = ra.FilesystemBackend(os.path.join(self.tmp.name, "dumps2"))
        self.dumps, keep = other.root, self.dumps
        self.write_hour(H06, [event(H06 + 1)], guid=GUID2)
        self.dumps = keep
        ra.publish_catalog(self.kv, self.backend, AGENT)
        ra.publish_catalog(self.kv, other, {"guid": GUID2, "hostname": "hf2"})
        self.assertEqual(sorted(self.kv.data[ra.SOURCES]), sorted(["%s|%s" % (GUID, GUID), "%s|%s" % (GUID2, GUID2)]))
        self.assertEqual(len(self.kv.data[ra.CATALOG]), 2)


class TestCatalog(Base):
    def test_catalog_rows(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.write_hour(H06 + HOUR, [event(H06 + HOUR + 1)])
        self.write_hour(H06, [event(H06 + 1)], guid=GUID2)
        self.write_hour(H06, [event(H06 + 1, st="other")], st="other")
        parts, sources, served, _ = ra.build_catalog(self.backend, AGENT, now=5)
        rows = {r["_key"]: r for r in parts}
        r = rows["%s|splunkd|2026-11-19" % GUID]
        self.assertEqual((r["files"], r["earliest"], r["latest"], r["forwarders"], r["agent"]),
                         (3, H06, H06 + 2 * HOUR, 2, GUID))
        src = {s["guid"]: s for s in sources}
        self.assertEqual(src[GUID]["sourcetypes"], ["other", "splunkd"])
        self.assertEqual((src[GUID]["files"], src[GUID]["days"], src[GUID]["agent_host"]), (3, 1, "hf1"))
        self.assertEqual(served, {GUID: ["other", "splunkd"], GUID2: ["splunkd"]})

    def test_publish_removes_only_own_stale_rows(self):
        self.kv.put(ra.CATALOG, "%s|gone|2020-01-01" % GUID, {"agent": GUID, "updated_at": 1})
        self.kv.put(ra.CATALOG, "%s|x|2020-01-01" % GUID2, {"agent": GUID2, "updated_at": 1})
        self.kv.put(ra.SOURCES, "%s|gone" % GUID, {"agent": GUID, "guid": "gone", "updated_at": 1})
        self.write_hour(H06, [event(H06 + 1)])
        ra.publish_catalog(self.kv, self.backend, AGENT)
        self.assertEqual(sorted(self.kv.data[ra.CATALOG]),
                         sorted(["%s|splunkd|2026-11-19" % GUID, "%s|x|2020-01-01" % GUID2]))
        self.assertEqual(list(self.kv.data[ra.SOURCES]), ["%s|%s" % (GUID, GUID)])

    def test_run_once_registers_agent(self):
        self.write_hour(H06, [event(H06 + 1)])
        state = ra.run_once(self.cfg, self.kv, self.backend, AGENT, {}, self.sink, now=1000)
        a = self.kv.get(ra.AGENTS, GUID)
        self.assertEqual((a["hostname"], a["forwarders"]), ("hf1", 1))
        self.assertEqual(state["served"], {GUID: ["splunkd"]})

    def test_heartbeat_reports_size_limit(self):
        self.cfg.cp.set("agent", "max_bytes_per_job", "1234")
        ra.heartbeat(self.kv, self.cfg, AGENT)
        self.assertEqual(self.kv.get(ra.AGENTS, GUID)["max_bytes_per_job"], 1234)


class TestFieldSampling(Base):
    def publish(self, cache=None, now=100):
        newest = ra.build_catalog(self.backend, AGENT, newest_per_sourcetype=2)[3]
        return ra.publish_fields(self.kv, self.cfg, self.backend, AGENT, newest, cache or {}, now)

    def test_newest_files_per_sourcetype(self):
        old = self.write_hour(H06, [event(H06 + 1)])
        mid = self.write_hour(H06 + HOUR, [event(H06 + HOUR + 1)])
        new = self.write_hour(H06 + 2 * HOUR, [event(H06 + 2 * HOUR + 1)])
        newest = ra.build_catalog(self.backend, AGENT, newest_per_sourcetype=2)[3]
        self.assertEqual([n for n, _ in newest["splunkd"]], [new, mid])
        self.assertNotIn(old, [n for n, _ in newest["splunkd"]])
        self.assertEqual(ra.build_catalog(self.backend, AGENT)[3], {})

    def test_publishes_envelope_values_and_field_names_only(self):
        evs = [event(H06 + i, index="main") for i in range(3)] + [event(H06 + 9, index="_internal")]
        evs[0]["fields"]["secret"] = "s3cr3t"
        evs.append({"time": H06, "event": "x", "index": "loop", "fields": {ra.JOB_FIELD: "j0"}})
        self.write_hour(H06, evs, gz=True)
        self.publish()
        row = self.kv.get(ra.FIELDS, "%s|splunkd" % GUID)
        self.assertEqual(row["indexes"], ["main", "_internal"])
        self.assertEqual(row["hosts"], ["h1"])
        self.assertEqual(row["indexed_fields"], ["node_guid", "secret"])
        self.assertEqual((row["events_sampled"], row["files_sampled"], row["agent"]), (4, 1, GUID))
        self.assertNotIn("s3cr3t", json.dumps(row))
        self.assertNotIn("msg at", json.dumps(row))

    def test_line_and_value_limits(self):
        self.cfg.cp.read_dict({"agent": {"sample_lines": "5", "sample_max_values": "2"}})
        self.write_hour(H06, [dict(event(H06 + i), host="h%d" % i) for i in range(10)])
        self.publish()
        row = self.kv.get(ra.FIELDS, "%s|splunkd" % GUID)
        self.assertEqual((row["events_sampled"], len(row["hosts"]), row["hosts_truncated"]), (5, 2, True))

    def test_cut_off_head_is_tolerated(self):
        import gzip
        name = self.write_hour(H06, [event(H06 + i) for i in range(50)])
        p = os.path.join(self.backend.root, "year=2026", "month=11", "day=19", "sourcetype=splunkd")
        with open(os.path.join(p, name), "rb") as f:
            body = gzip.compress(f.read())[:200]
        os.remove(os.path.join(p, name))
        with open(os.path.join(p, name + ".gz"), "wb") as f:
            f.write(body)
        self.publish()
        self.assertEqual(self.kv.get(ra.FIELDS, "%s|splunkd" % GUID)["files_sampled"], 1)

    def test_unchanged_files_are_not_read_again(self):
        from unittest import mock
        self.write_hour(H06, [event(H06 + 1)])
        cache = self.publish(now=100)
        with mock.patch.object(ra, "sample_sourcetype") as sample:
            cache2 = self.publish(cache, now=200)
            sample.assert_not_called()
        self.assertEqual(cache2, cache)
        self.assertEqual(self.kv.get(ra.FIELDS, "%s|splunkd" % GUID)["updated_at"], 200)
        self.write_hour(H06 + HOUR, [event(H06 + HOUR + 1, index="new")])
        self.publish(cache2, now=300)
        self.assertEqual(self.kv.get(ra.FIELDS, "%s|splunkd" % GUID)["indexes"], ["_internal", "new"])

    def test_removes_only_own_stale_rows(self):
        self.kv.put(ra.FIELDS, "%s|gone" % GUID, {"agent": GUID, "updated_at": 1})
        self.kv.put(ra.FIELDS, "%s|x" % GUID2, {"agent": GUID2, "updated_at": 1})
        self.write_hour(H06, [event(H06 + 1)])
        self.publish()
        self.assertEqual(sorted(self.kv.data[ra.FIELDS]), sorted(["%s|splunkd" % GUID, "%s|x" % GUID2]))

    def test_run_once_samples_and_failure_does_not_block_jobs(self):
        from unittest import mock
        self.write_hour(H06, [event(H06 + 1)])
        state = ra.run_once(self.cfg, self.kv, self.backend, AGENT, {}, self.sink, now=1000)
        self.assertIn("splunkd", state["field_samples"])
        with mock.patch.object(ra, "publish_fields", side_effect=RuntimeError("boom")), \
                mock.patch.object(ra, "process_jobs") as jobs:
            state = ra.run_once(self.cfg, self.kv, self.backend, AGENT, {}, self.sink, now=1000)
        jobs.assert_called_once()
        self.assertEqual(state["served"], {GUID: ["splunkd"]})


class TestForwarders(Base):
    def test_parse_writer_guid(self):
        self.assertEqual(ra.parse_writer_guid(
            "events_1795071600_1795068000_0_000000_5a8d3399-fd7b-41cb-b0a9-23cc0bce0ef8.ndjson.gz"), GUID)
        self.assertEqual(ra.parse_writer_guid("events_1_2_x.ndjson"), ra.UNKNOWN_GUID)

    def test_parse_forwarders(self):
        self.assertIsNone(ra.parse_forwarders(None))
        self.assertIsNone(ra.parse_forwarders("*"))
        self.assertIsNone(ra.parse_forwarders(""))
        self.assertEqual(ra.parse_forwarders("%s %s" % (GUID.lower(), GUID2)), {GUID, GUID2})
        self.assertEqual(ra.parse_forwarders([GUID]), {GUID})
        with self.assertRaises(ra.JobError):
            ra.parse_forwarders("not-a-guid")

    def test_shared_store_agent_limits_to_selected_forwarders(self):
        self.write_hour(H06, [event(H06 + 1)], guid=GUID)
        self.write_hour(H06, [event(H06 + 2), event(H06 + 3)], guid=GUID2)
        self.submit(H06, H06 + HOUR, fwd=GUID2)
        self.agent_run()
        t = self.task()
        self.assertEqual((t["status"], t["files_total"], t["events_staged"]), ("done", 1, 2))

    def test_all_forwarders_by_default(self):
        self.write_hour(H06, [event(H06 + 1)], guid=GUID)
        self.write_hour(H06, [event(H06 + 2)], guid=GUID2)
        self.submit(H06, H06 + HOUR, fwd="*")
        self.agent_run()
        self.assertEqual(self.task()["events_staged"], 2)

    def test_invalid_forwarders_fail_task(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.submit(H06, H06 + HOUR, fwd="nope")
        self.agent_run()
        t = self.task()
        self.assertEqual(t["status"], "failed")
        self.assertIn("forwarder", t["message"])



class TestKVStoreClient(unittest.TestCase):
    """The real KV store only updates on POST .../<key>; new records need batch_save."""

    def test_put_upserts_with_batch_save(self):
        kv = ra.KVStore("https://sh:8089", "promotion_control", "Splunk X", verify=False)
        calls = []
        kv._do = lambda method, url, body=None: calls.append((method, url, body))
        kv.put(ra.AGENTS, GUID, {"hostname": "hf1", "_user": "nobody"})
        method, url, body = calls[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/storage/collections/data/promotion_agents/batch_save"), url)
        self.assertEqual(body, [{"hostname": "hf1", "_key": GUID}])

    def test_heartbeat_failure_does_not_block_jobs(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            cfg = ra.Config(app_dir="/nonexistent", overrides={"agent": {"fs_root": d}})
            state = {"catalog_at": 10 ** 10, "served": {}}
            with mock.patch.object(ra, "heartbeat", side_effect=RuntimeError("HTTP 404")), \
                    mock.patch.object(ra, "process_jobs") as jobs:
                ra.run_once(cfg, FakeKV(), ra.FilesystemBackend(d), AGENT, state, None, now=10 ** 10)
            jobs.assert_called_once()


class TestControlTarget(unittest.TestCase):
    def cfg(self, sh_uri):
        return ra.Config(app_dir="/nonexistent", overrides={"control": {"sh_uri": sh_uri}})

    def no_token(self, cfg, key):
        raise AssertionError("token must not be read for a local target")

    def test_empty_sh_uri_is_all_in_one(self):
        uri, auth, verify = ra.control_target(self.cfg(""), "SK", self.no_token)
        self.assertEqual((uri, auth, verify), ("https://127.0.0.1:8089", "Splunk SK", False))

    def test_localhost_uses_session_key(self):
        for u in ("https://localhost:8089", "https://127.0.0.1:8089", "https://[::1]:8089"):
            self.assertEqual(ra.control_target(self.cfg(u), "SK", self.no_token)[1], "Splunk SK")

    def test_remote_uses_stored_token(self):
        uri, auth, verify = ra.control_target(
            self.cfg("https://sh1.example.com:8089"), "SK", lambda c, k: "TOK")
        self.assertEqual((uri, auth, verify), ("https://sh1.example.com:8089", "Bearer TOK", True))


class TestS3Parsing(unittest.TestCase):
    def test_key_regex(self):
        m = ra.S3_KEY_RE.search(
            "pfx/year=2026/month=11/day=19/sourcetype=splunkd/events_1795071600_1795068000_0_0_x.ndjson")
        self.assertEqual(m.groups()[:4], ("2026", "11", "19", "splunkd"))


if __name__ == "__main__":
    unittest.main()
