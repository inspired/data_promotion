"""Tests for the replay agent. Run: python3 -m unittest discover -s tests -v"""
import copy
import datetime
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "TA-replay_agent", "bin"))
import replay_agent as ra  # noqa: E402

# 2026-11-19 06:00:00 UTC, matches the sample file events_1795071600_1795068000_...
H06 = 1795068000
HOUR = 3600
GUID = "5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8"


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

    def write_hour(self, start, events, st="splunkd", gz=False):
        d = datetime.datetime.fromtimestamp(start, datetime.timezone.utc)
        p = os.path.join(self.dumps, "year=%d" % d.year, "month=%02d" % d.month,
                         "day=%02d" % d.day, "sourcetype=%s" % st)
        os.makedirs(p, exist_ok=True)
        name = "events_%d_%d_0_000000_%s.ndjson" % (start + HOUR, start, GUID)
        body = "".join(json.dumps(e) + "\n" for e in events).encode()
        if gz:
            import gzip
            name += ".gz"
            body = gzip.compress(body)
        with open(os.path.join(p, name), "wb") as f:
            f.write(body)
        return name

    def submit(self, earliest, latest, st="splunkd", idx="*", key="job1"):
        self.kv.put(ra.JOBS, key, {"job_id": key, "sourcetype": st, "index_filter": idx,
                                   "earliest": earliest, "latest": latest,
                                   "status": "pending", "submitted_at": 1})

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


class TestRunJob(Base):
    def test_filters_by_event_time_and_tags(self):
        evs = [event(H06 + m * 60) for m in range(60)]
        self.write_hour(H06, evs)
        self.write_hour(H06 + HOUR, [event(H06 + HOUR + 5)])
        self.submit(H06 + 10 * 60, H06 + 20 * 60)
        ra.process_pending(self.cfg, self.backend, self.kv)

        job = self.kv.get(ra.JOBS, "job1")
        self.assertEqual(job["status"], "staged", job.get("message"))
        self.assertEqual(job["events_staged"], 10)
        out = self.staged_events()
        self.assertEqual([e["time"] for e in out], [H06 + m * 60 for m in range(10, 20)])
        for e in out:
            self.assertEqual(e["fields"], {"node_guid": "abc", "replay_job": "job1"})
            self.assertEqual(e["index"], "_internal")
            self.assertEqual(e["sourcetype"], "splunkd")
        self.assertEqual(os.listdir(self.cfg.get("agent", "work_dir")), [])

    def test_adds_fields_when_missing_and_reads_gzip(self):
        self.write_hour(H06, [event(H06 + 5, fields=False)], gz=True)
        self.submit(H06, H06 + HOUR)
        ra.process_pending(self.cfg, self.backend, self.kv)
        out = self.staged_events()
        self.assertEqual(out[0]["fields"], {"replay_job": "job1"})
        self.assertTrue(all(n.endswith(".ndjson") for n in
                            os.listdir(os.path.join(self.cfg.get("agent", "staging_dir"), "job1"))))

    def test_index_filter_and_bad_lines(self):
        name = self.write_hour(H06, [event(H06 + 1, index="main"), event(H06 + 2, index="_internal")])
        path = os.path.join(self.dumps, "year=2026", "month=11", "day=19", "sourcetype=splunkd", name)
        with open(path, "a") as f:
            f.write("not json\n{\"event\":\"no time\"}\n")
        self.submit(H06, H06 + HOUR, idx="_int*")
        ra.process_pending(self.cfg, self.backend, self.kv)
        job = self.kv.get(ra.JOBS, "job1")
        self.assertEqual((job["events_staged"], job["events_skipped"], job["events_bad"]), (1, 1, 2))

    def test_no_files(self):
        self.submit(H06, H06 + HOUR)
        ra.process_pending(self.cfg, self.backend, self.kv)
        job = self.kv.get(ra.JOBS, "job1")
        self.assertEqual(job["status"], "staged")
        self.assertIn("no files", job["message"])

    def test_invalid_jobs_fail(self):
        self.submit(H06 + HOUR, H06, key="bad_range")
        self.submit(H06, H06 + HOUR, st="../etc", key="bad_st")
        ra.process_pending(self.cfg, self.backend, self.kv)
        self.assertEqual(self.kv.get(ra.JOBS, "bad_range")["status"], "failed")
        self.assertEqual(self.kv.get(ra.JOBS, "bad_st")["status"], "failed")

    def test_size_limit(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.cfg.cp.set("agent", "max_bytes_per_job", "10")
        self.submit(H06, H06 + HOUR)
        ra.process_pending(self.cfg, self.backend, self.kv)
        job = self.kv.get(ra.JOBS, "job1")
        self.assertEqual(job["status"], "failed")
        self.assertIn("max_bytes_per_job", job["message"])

    def test_cancel_while_running(self):
        for h in range(3):
            self.write_hour(H06 + h * HOUR, [event(H06 + h * HOUR + 1)])
        self.submit(H06, H06 + 3 * HOUR)
        real_put = self.kv.put
        fired = []

        def put(coll, key, rec):
            real_put(coll, key, rec)
            if rec.get("files_done") == 1 and not fired:
                fired.append(1)
                self.kv.data[coll][key]["status"] = "cancel_requested"
        self.kv.put = put
        ra.process_pending(self.cfg, self.backend, self.kv)
        job = self.kv.get(ra.JOBS, "job1")
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(len(self.staged_events()), 1)

    def test_cancelled_before_claim_is_not_run(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.submit(H06, H06 + HOUR)
        job = self.kv.query(ra.JOBS, {"status": "pending"})[0]
        self.kv.data[ra.JOBS]["job1"]["status"] = "cancelled"
        with self.assertRaises(ra.Cancelled):
            ra.run_job(self.cfg, self.backend, self.kv, job)

    def test_recover_stale(self):
        import socket
        self.kv.put(ra.JOBS, "j", {"status": "running", "agent_host": socket.gethostname()})
        ra.recover_stale(self.kv)
        self.assertEqual(self.kv.get(ra.JOBS, "j")["status"], "failed")


class TestCatalog(Base):
    def test_catalog_rows(self):
        self.write_hour(H06, [event(H06 + 1)])
        self.write_hour(H06 + HOUR, [event(H06 + HOUR + 1)])
        self.write_hour(H06, [event(H06 + 1, st="other")], st="other")
        rows = {r["_key"]: r for r in ra.build_catalog(self.backend, now=5)}
        r = rows["splunkd|2026-11-19"]
        self.assertEqual((r["files"], r["earliest"], r["latest"]), (2, H06, H06 + 2 * HOUR))
        self.assertIn("other|2026-11-19", rows)

    def test_publish_removes_stale(self):
        self.kv.put(ra.CATALOG, "gone|2020-01-01", {"updated_at": 1})
        self.write_hour(H06, [event(H06 + 1)])
        ra.publish_catalog(self.kv, self.backend)
        self.assertEqual(list(self.kv.data[ra.CATALOG]), ["splunkd|2026-11-19"])


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
