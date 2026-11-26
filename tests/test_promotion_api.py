"""Tests for the Data Promotion REST API. Run: python3 -m unittest discover -s tests -v"""
import io
import json
import os
import sys
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "promotion_control", "bin"))
sys.path.insert(0, HERE)
import promotion_api as api  # noqa: E402
import promotion_agent as ra  # noqa: E402
from test_promotion_agent import AGENT, GUID, GUID2, H06, HOUR, Base, FakeKV, event  # noqa: E402

NOW = 1795100000
GUID3 = "0C3E5B1A-1111-4222-8333-944455556666"


class FakeSplunkd:
    """KV store in memory; searches answered by self.on_search(spl, earliest, latest, as_user)."""

    def __init__(self, kv=None):
        self.kv = kv or FakeKV()
        self.searches = []
        self.on_search = lambda spl, e, l, u: []

    def kv_query(self, coll, query=None, sort=None, limit=None, fields=None):
        rows = self.kv.query(coll, query)
        if sort:
            field, _, direction = sort.partition(":")
            rows.sort(key=lambda r: r.get(field) or 0, reverse=direction == "-1")
        return rows[:limit] if limit else rows

    def kv_get(self, coll, key):
        return self.kv.get(coll, key)

    def kv_save(self, coll, rec):
        self.kv.put(coll, rec["_key"], rec)

    def search(self, spl, earliest=None, latest=None, as_user=False):
        self.searches.append({"spl": spl, "earliest": earliest, "latest": latest, "as_user": as_user})
        return self.on_search(spl, earliest, latest, as_user)


def source(agent, guid, sourcetypes, **kw):
    rec = {"_key": "%s_%s" % (agent, guid), "agent": agent, "guid": guid,
           "sourcetypes": sourcetypes, "days": 2, "files": 10, "bytes": 1000,
           "earliest": H06, "latest": H06 + 2 * HOUR}
    rec.update(kw)
    return rec


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.d = FakeSplunkd()
        self.api = api.Api(self.d, "alice", now=lambda: NOW)
        # GUID holds its own splunkd + access_combined; GUID2 holds splunkd only.
        self.kv_put(api.SOURCES, source(GUID, GUID, ["splunkd", "access_combined"]))
        self.kv_put(api.SOURCES, source(GUID2, GUID2, ["splunkd"]))
        self.kv_put(api.AGENTS, {"_key": GUID, "guid": GUID, "hostname": "hf1", "last_seen": NOW - 30,
                                 "agent_version": "2.2.0", "max_bytes_per_job": 5000})
        self.kv_put(api.AGENTS, {"_key": GUID2, "guid": GUID2, "hostname": "hf2", "last_seen": NOW - 3600,
                                 "agent_version": "2.1.0"})

    def kv_put(self, coll, rec):
        self.d.kv.put(coll, rec["_key"], rec)

    def call(self, method, path, query=None, body=None):
        return self.api.handle(method, path, query or {}, body)

    def err(self, method, path, query=None, body=None):
        with self.assertRaises(api.ApiError) as cm:
            self.call(method, path, query, body)
        return cm.exception


class TestRouting(ApiBase):
    def test_unknown_endpoint(self):
        self.assertEqual(self.err("GET", "nope").status, 404)
        self.assertEqual(self.err("GET", "jobs/x/y/z").status, 404)

    def test_wrong_method(self):
        self.assertEqual(self.err("DELETE", "jobs").status, 405)
        self.assertEqual(self.err("POST", "catalog").status, 405)
        self.kv_put(api.JOBS, {"_key": "j1", "job_id": "j1", "status": "pending"})
        self.assertEqual(self.err("GET", "jobs/j1/cancel").status, 405)

    def test_bad_job_id_is_not_found(self):
        self.assertEqual(self.err("GET", "jobs/..%2f").status, 404)
        self.assertEqual(self.err("GET", "jobs/missing").status, 404)

    def test_leading_and_trailing_slashes(self):
        self.assertEqual(self.call("GET", "/jobs/")[0], 200)


class TestValidation(ApiBase):
    def test_sourcetype(self):
        for bad in ("", "*", 'x"y', "a b", "a|b", None):
            e = self.err("POST", "jobs", body={"sourcetype": bad, "earliest": H06, "latest": H06 + HOUR})
            self.assertEqual((e.status, e.field), (400, "sourcetype"), bad)

    def test_forwarders(self):
        self.assertEqual(api.parse_forwarders("*"), [])
        self.assertEqual(api.parse_forwarders(None), [])
        self.assertEqual(api.parse_forwarders([GUID2.lower(), GUID]), sorted([GUID, GUID2]))
        self.assertEqual(api.parse_forwarders("%s, %s" % (GUID, GUID2)), sorted([GUID, GUID2]))
        with self.assertRaises(api.ApiError):
            api.parse_forwarders(["not-a-guid"])

    def test_target_index(self):
        self.assertEqual(api.validate_target_index("*"), "")
        self.assertEqual(api.validate_target_index(" promoted "), "promoted")
        for bad in ("Main", "-x", "a" * 81, "a b"):
            with self.assertRaises(api.ApiError):
                api.validate_target_index(bad)

    def test_index_filter(self):
        self.assertEqual(api.validate_index_filter(""), "*")
        self.assertEqual(api.validate_index_filter("_int*"), "_int*")
        with self.assertRaises(api.ApiError):
            api.validate_index_filter("a OR b")


class TestTimeRange(ApiBase):
    def test_epochs_and_clamp_to_now(self):
        self.assertEqual(self.api.resolve_range(H06 + 0.5, NOW + 999), (H06, NOW))
        self.assertEqual(self.d.searches, [])

    def test_latest_defaults_to_now(self):
        self.d.on_search = lambda spl, e, l, u: [{"info_min_time": str(H06), "info_max_time": str(NOW + 5)}]
        self.assertEqual(self.api.resolve_range(str(H06), None), (H06, NOW))
        self.assertEqual(self.d.searches[0]["latest"], "now")

    def test_modifiers_are_resolved_as_the_caller(self):
        self.d.on_search = lambda spl, e, l, u: [{"info_min_time": "1795000000.000", "info_max_time": "1795003600.000"}]
        self.assertEqual(self.api.resolve_range("-7d@d", "@d"), (1795000000, 1795003600))
        s = self.d.searches[0]
        self.assertEqual((s["spl"], s["earliest"], s["latest"], s["as_user"]),
                         (api.RESOLVE_SEARCH, "-7d@d", "@d", True))

    def test_all_time_and_bad_ranges(self):
        for e in (None, "", 0, "0"):
            self.assertEqual(self.err("GET", "estimate", {"sourcetype": "splunkd", "earliest": e}).field, "earliest")
        self.assertEqual(self.err("GET", "estimate", {"sourcetype": "splunkd", "earliest": H06,
                                                      "latest": H06}).field, "latest")
        self.assertEqual(self.err("GET", "estimate", {"sourcetype": "splunkd", "earliest": "rt-5m"}).status, 400)
        self.assertEqual(self.err("GET", "estimate", {"sourcetype": "splunkd", "earliest": "x y"}).status, 400)
        self.d.on_search = lambda spl, e, l, u: [{"info_min_time": "", "info_max_time": ""}]
        self.assertEqual(self.err("GET", "estimate", {"sourcetype": "splunkd", "earliest": "-0s@s"}).field,
                         "earliest")


class TestData(ApiBase):
    def test_forwarders(self):
        self.kv_put(api.SOURCES, source(GUID, GUID3, ["splunkd"]))
        self.kv_put(api.FORWARDER_NAMES, {"_key": GUID3, "guid": GUID3, "hostname": "uf-old"})
        status, out = self.call("GET", "forwarders")
        self.assertEqual(status, 200)
        by = {f["guid"]: f for f in out["items"]}
        self.assertEqual([f["label"] for f in out["items"]], ["hf1", "hf2", "uf-old"])
        self.assertEqual(by[GUID]["status"], "reporting")
        self.assertEqual(by[GUID]["max_bytes_per_job"], 5000)
        self.assertEqual(by[GUID]["sourcetypes"], ["access_combined", "splunkd"])
        self.assertEqual(by[GUID2]["status"], "stale")
        self.assertIsNone(by[GUID2]["max_bytes_per_job"])
        self.assertEqual((by[GUID3]["status"], by[GUID3]["agents"]), ("unregistered", [GUID]))
        _, out = self.call("GET", "forwarders", {"sourcetype": "access_combined"})
        self.assertEqual([f["guid"] for f in out["items"]], [GUID])

    def test_catalog_summary(self):
        self.d.on_search = lambda spl, e, l, u: [{"sourcetype": "splunkd", "day": "2026-11-19", "bytes": "9"}]
        _, out = self.call("GET", "catalog")
        self.assertEqual(out["items"][0]["bytes"], "9")
        self.assertEqual(self.d.searches[0]["spl"], api.catalog_search())
        self.assertFalse(self.d.searches[0]["as_user"])
        self.call("GET", "catalog", {"sourcetype": "splunkd"})
        self.assertIn('where sourcetype="splunkd"', self.d.searches[1]["spl"])

    def test_catalog_for_one_forwarder_reads_its_agents_rows(self):
        self.kv_put(api.SOURCES, source(GUID2, GUID3, ["splunkd"]))
        self.kv_put(api.CATALOG, {"_key": "a", "agent": GUID2, "sourcetype": "splunkd", "day": "d1"})
        self.kv_put(api.CATALOG, {"_key": "b", "agent": GUID, "sourcetype": "splunkd", "day": "d1"})
        _, out = self.call("GET", "catalog", {"forwarder": GUID3.lower()})
        self.assertEqual([r["_key"] for r in out["items"]], ["a"])

    def test_catalog_by_forwarder(self):
        # GUID reports its own archive and GUID3's (shared store); GUID2 its own.
        self.kv_put(api.SOURCES, source(GUID, GUID3, ["splunkd"]))
        agg = [
            {"agent": GUID, "sourcetype": "splunkd", "days": "2", "files": "48", "bytes": "900",
             "earliest": str(H06), "latest": str(H06 + HOUR), "updated_at": "5"},
            {"agent": GUID, "sourcetype": "access_combined", "days": "1", "files": "24", "bytes": "100",
             "earliest": str(H06), "latest": str(H06 + HOUR), "updated_at": "5"},
            {"agent": GUID2, "sourcetype": "splunkd", "days": "3", "files": "72", "bytes": "300",
             "earliest": str(H06 - HOUR), "latest": str(H06), "updated_at": "6"},
        ]
        self.d.on_search = lambda spl, e, l, u: [r for r in agg if 'sourcetype="' not in spl or r["sourcetype"] in spl]
        _, out = self.call("GET", "catalog", {"by": "forwarder"})
        self.assertEqual(self.d.searches[0]["spl"], api.catalog_by_agent_search())
        rows = [(r["hostname"], r["guid"], r["sourcetype"]) for r in out["items"]]
        # Sorted by label, as in GET forwarders: GUID3 has no hostname, so its GUID.
        self.assertEqual(rows, [(None, GUID3, "splunkd"), ("hf1", GUID, "access_combined"),
                                ("hf1", GUID, "splunkd"), ("hf2", GUID2, "splunkd")])
        hf2 = out["items"][3]
        self.assertEqual((hf2["days"], hf2["files"], hf2["bytes"], hf2["earliest"], hf2["latest"]),
                         (3, 72, 300, H06 - HOUR, H06))
        self.assertEqual((hf2["agents"], hf2["shared_store"], hf2["status"]), ([GUID2], False, "stale"))
        self.assertTrue(out["items"][0]["shared_store"])
        self.assertTrue(out["items"][2]["shared_store"])

    def test_catalog_by_forwarder_filters(self):
        self.d.on_search = lambda spl, e, l, u: [
            {"agent": GUID, "sourcetype": "splunkd", "days": "1"}, {"agent": GUID2, "sourcetype": "splunkd", "days": "1"}]
        _, out = self.call("GET", "catalog", {"by": "forwarder", "sourcetype": "splunkd", "forwarder": GUID2.lower()})
        self.assertIn('where sourcetype="splunkd"', self.d.searches[0]["spl"])
        self.assertEqual([r["guid"] for r in out["items"]], [GUID2])

    def test_catalog_updated_at_is_the_agents_last_publish(self):
        self.d.kv.data[api.AGENTS][GUID]["catalog_at"] = 700
        self.d.kv.data[api.AGENTS][GUID2]["catalog_at"] = 800
        self.d.on_search = lambda spl, e, l, u: [
            {"sourcetype": "splunkd", "day": "d1", "agent_list": [GUID, GUID2], "updated_at": "5"},
            {"sourcetype": "other", "day": "d1", "agent_list": "UNKNOWN-AGENT", "updated_at": "5"}]
        _, out = self.call("GET", "catalog")
        self.assertEqual([(r["updated_at"], "agent_list" in r) for r in out["items"]], [(800, False), ("5", False)])

        self.kv_put(api.CATALOG, {"_key": "a", "agent": GUID, "sourcetype": "splunkd", "day": "d1", "updated_at": 5})
        _, out = self.call("GET", "catalog", {"forwarder": GUID})
        self.assertEqual(out["items"][0]["updated_at"], 700)

        self.d.on_search = lambda spl, e, l, u: [{"agent": GUID2, "sourcetype": "splunkd", "days": "1", "updated_at": "5"}]
        _, out = self.call("GET", "catalog", {"by": "forwarder"})
        self.assertEqual(out["items"][0]["updated_at"], 800)

    def test_catalog_by_unknown(self):
        self.assertEqual(self.err("GET", "catalog", {"by": "host"}).field, "by")

    def test_fields(self):
        self.kv_put(api.FIELDS, {"_key": "f", "agent": GUID, "sourcetype": "splunkd", "indexes": ["_internal"]})
        self.kv_put(api.FIELDS, {"_key": "g", "agent": GUID, "sourcetype": "other", "indexes": ["main"]})
        _, out = self.call("GET", "fields", {"sourcetype": "splunkd"})
        self.assertEqual([r["_key"] for r in out["items"]], ["f"])
        self.assertEqual(self.err("GET", "fields").field, "sourcetype")


class TestEstimate(ApiBase):
    def setUp(self):
        super().setUp()
        self.d.on_search = lambda spl, e, l, u: [{"agent": GUID, "bytes": "6000"}, {"agent": GUID2, "bytes": "70"}]

    def test_summary_and_limits(self):
        status, out = self.call("GET", "estimate", {"sourcetype": "splunkd", "earliest": H06,
                                                    "latest": H06 + HOUR})
        self.assertEqual(status, 200)
        self.assertEqual((out["forwarders"], out["bytes"], out["seconds"]), (2, 6070, HOUR))
        self.assertEqual(out["largest"]["name"], "hf1")
        self.assertEqual([o["guid"] for o in out["over_limit"]], [GUID])
        hf2 = out["per_forwarder"][1]
        self.assertEqual((hf2["limit"], hf2["limit_reported"]), (api.DEFAULT_MAX_BYTES_PER_JOB, False))
        spl = self.d.searches[-1]["spl"]
        self.assertIn('agent="%s" OR agent="%s"' % (GUID, GUID2), spl)
        self.assertIn("slo=%d, shi=%d" % (H06 - api.FILE_SLACK_SECONDS, H06 + HOUR + api.FILE_SLACK_SECONDS), spl)

    def test_only_selected_forwarders_agents(self):
        self.call("GET", "estimate", {"sourcetype": "splunkd", "earliest": H06, "latest": H06 + HOUR,
                                      "forwarders": [GUID2]})
        self.assertIn('where agent="%s" |' % GUID2, self.d.searches[-1]["spl"])

    def test_malformed_agent_never_reaches_the_search(self):
        self.kv_put(api.SOURCES, source('x" | delete', GUID3, ["splunkd"]))
        self.call("GET", "estimate", {"sourcetype": "splunkd", "earliest": H06, "latest": H06 + HOUR})
        self.assertNotIn("delete", self.d.searches[-1]["spl"])

    def test_no_data_runs_no_search(self):
        _, out = self.call("GET", "estimate", {"sourcetype": "nothing", "earliest": H06, "latest": H06 + HOUR})
        self.assertEqual((out["forwarders"], out["bytes"], out["largest"]), (0, 0, None))
        self.assertEqual(self.d.searches, [])


class TestJobs(ApiBase):
    def body(self, **kw):
        b = {"sourcetype": "splunkd", "earliest": H06, "latest": H06 + HOUR}
        b.update(kw)
        return b

    def test_create(self):
        status, out = self.call("POST", "jobs", body=self.body(forwarders=[GUID2], target_index="promoted",
                                                               index_filter="_internal"))
        self.assertEqual(status, 201)
        job = self.d.kv.get(api.JOBS, out["job"]["job_id"])
        self.assertEqual(job["_key"], job["job_id"])
        self.assertRegex(job["job_id"], r"^\d{8}-\d{6}-[0-9a-f]{6}$")
        for k, v in {"sourcetype": "splunkd", "index_filter": "_internal", "forwarders": GUID2,
                     "forwarders_count": 1, "agents_expected": 1, "earliest": H06, "latest": H06 + HOUR,
                     "status": "pending", "submitted_by": "alice", "submitted_at": NOW,
                     "target_index": "promoted"}.items():
            self.assertEqual(job[k], v, k)
        self.assertEqual(out["warnings"], [])

    def test_create_all_forwarders(self):
        _, out = self.call("POST", "jobs", body=self.body())
        job = out["job"]
        self.assertEqual((job["forwarders"], job["forwarders_count"], job["agents_expected"]), ("*", 0, 2))
        self.assertNotIn("target_index", job)
        self.assertEqual(job["index_filter"], "*")

    def test_no_forwarder_holds_the_sourcetype(self):
        e = self.err("POST", "jobs", body=self.body(sourcetype="access_combined", forwarders=[GUID2]))
        self.assertEqual((e.status, e.field), (400, "sourcetype"))
        self.assertIn("among the selected forwarders", e.message)
        self.assertEqual(self.d.kv.query(api.JOBS), [])

    def test_dry_run_writes_nothing(self):
        status, out = self.call("POST", "jobs", body=self.body(dry_run=True))
        self.assertEqual((status, out["dry_run"], out["job"]["status"]), (200, True, "pending"))
        self.assertEqual(self.d.kv.query(api.JOBS), [])

    def test_over_limit_warns_or_rejects(self):
        self.d.on_search = lambda spl, e, l, u: [{"agent": GUID, "bytes": "6000"}] if "inputlookup" in spl else []
        status, out = self.call("POST", "jobs", body=self.body())
        self.assertEqual(status, 201)
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("hf1 would read 6000 bytes", out["warnings"][0])
        self.assertEqual(self.err("POST", "jobs", body=self.body(reject_over_limit="true")).status, 400)
        self.assertEqual(len(self.d.kv.query(api.JOBS)), 1)

    def test_body_must_be_an_object(self):
        self.assertEqual(self.err("POST", "jobs", body=["x"]).status, 400)

    def test_list_newest_first_with_limit(self):
        for i in range(5):
            self.kv_put(api.JOBS, {"_key": "j%d" % i, "job_id": "j%d" % i, "submitted_at": i,
                                   "status": "completed" if i % 2 else "failed"})
        _, out = self.call("GET", "jobs", {"limit": "3"})
        self.assertEqual([j["job_id"] for j in out["items"]], ["j4", "j3", "j2"])
        _, out = self.call("GET", "jobs", {"status": "completed"})
        self.assertEqual([j["job_id"] for j in out["items"]], ["j3", "j1"])

    def test_get_and_tasks(self):
        self.kv_put(api.JOBS, {"_key": "j1", "job_id": "j1", "status": "running"})
        self.kv_put(api.TASKS, {"_key": "t1", "job_id": "j1", "agent_host": "hf1"})
        self.kv_put(api.TASKS, {"_key": "t2", "job_id": "j2", "agent_host": "hf1"})
        self.assertEqual(self.call("GET", "jobs/j1")[1]["status"], "running")
        self.assertEqual([t["_key"] for t in self.call("GET", "jobs/j1/tasks")[1]["items"]], ["t1"])
        self.assertEqual(self.err("GET", "jobs/j2/tasks").status, 404)

    def test_cancel(self):
        for key, status in (("p", "pending"), ("r", "running"), ("c", "completed")):
            self.kv_put(api.JOBS, {"_key": key, "job_id": key, "status": status})
        self.assertEqual(self.call("POST", "jobs/p/cancel")[1]["status"], "cancelled")
        self.assertEqual(self.d.kv.get(api.JOBS, "p")["finished_at"], NOW)
        self.assertEqual(self.call("POST", "jobs/r/cancel")[1]["status"], "cancel_requested")
        self.assertEqual(self.d.kv.get(api.JOBS, "r")["message"], "cancelled by alice")
        self.assertEqual(self.err("POST", "jobs/c/cancel").status, 409)
        self.assertEqual(self.d.kv.get(api.JOBS, "c")["status"], "completed")

    def test_verify_runs_as_the_caller_over_the_job_range(self):
        self.kv_put(api.JOBS, {"_key": "j1", "job_id": "j1", "earliest": H06, "latest": H06 + 1799.5})
        self.d.on_search = lambda spl, e, l, u: [{"index": "main", "count": "7"}, {"index": "x", "count": "3"}]
        _, out = self.call("GET", "jobs/j1/verify")
        self.assertEqual(out["events"], 10)
        s = self.d.searches[0]
        self.assertEqual((s["spl"], s["earliest"], s["latest"], s["as_user"]),
                         (api.verify_search("j1"), str(H06), str(H06 + 1801), True))


class TestHandleRequest(unittest.TestCase):
    def setUp(self):
        self.d = FakeSplunkd()
        self.made = []

        def factory(rest_uri, system_token, user_token, user):
            self.made.append((rest_uri, system_token, user_token, user))
            return self.d
        self.factory = factory

    def req(self, **kw):
        r = {"method": "GET", "path_info": "jobs", "query": [], "session": {"user": "bob", "authtoken": "UT"},
             "system_authtoken": "ST", "server": {"rest_uri": "https://127.0.0.1:8089"}}
        r.update(kw)
        out = api.handle_request(r, splunkd_factory=self.factory, now=lambda: NOW)
        return out["status"], json.loads(out["payload"])

    def test_ok(self):
        self.d.kv.put(api.JOBS, "j1", {"job_id": "j1", "submitted_at": 1})
        status, body = self.req(query=[["limit", "5"]])
        self.assertEqual((status, [j["job_id"] for j in body["items"]]), (200, ["j1"]))
        self.assertEqual(self.made, [("https://127.0.0.1:8089", "ST", "UT", "bob")])

    def test_path_from_rest_path(self):
        for kw in ({"path_info": None, "rest_path": "/data_promotion/jobs"},
                   {"path_info": None, "rest_path": "/services/data_promotion/jobs/"},
                   {"path_info": "/data_promotion/jobs"},
                   {"path_info": "/jobs"}):
            self.assertEqual(self.req(**kw)[0], 200, kw)

    def test_post_json_payload_and_user(self):
        self.d.kv.put(api.SOURCES, GUID, source(GUID, GUID, ["splunkd"]))
        status, body = self.req(method="POST", payload=json.dumps(
            {"sourcetype": "splunkd", "earliest": H06, "latest": H06 + HOUR}))
        self.assertEqual((status, body["job"]["submitted_by"]), (201, "bob"))

    def test_post_form_payload(self):
        self.d.kv.put(api.SOURCES, GUID, source(GUID, GUID, ["splunkd"]))
        form = urllib.parse.urlencode({"sourcetype": "splunkd", "earliest": H06, "latest": H06 + HOUR,
                                       "dry_run": "1"})
        status, body = self.req(method="POST", payload=form)
        self.assertEqual((status, body["dry_run"]), (200, True))

    def test_repeated_query_keys(self):
        self.req(path_info="estimate", query=[["sourcetype", "splunkd"], ["earliest", str(H06)],
                                              ["forwarders", GUID], ["forwarders", GUID2]])
        # neither forwarder holds splunkd in this fake, so no search, but both GUIDs parsed fine
        self.assertEqual(self.req(path_info="estimate", query=[["sourcetype", "splunkd"], ["earliest", str(H06)],
                                                               ["forwarders", GUID], ["forwarders", "bad"]])[0], 400)

    def test_errors(self):
        status, body = self.req(path_info="jobs/nope")
        self.assertEqual((status, body), (404, {"error": "no such job"}))
        status, body = self.req(method="POST", payload=json.dumps({"sourcetype": "*"}))
        self.assertEqual((status, body["field"]), (400, "sourcetype"))

    def test_unexpected_error_is_500_json(self):
        def boom(*a, **k):
            raise RuntimeError("kaboom")
        self.d.kv_query = boom
        status, body = self.req()
        self.assertEqual(status, 500)
        self.assertIn("kaboom", body["error"])


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestSplunkd(unittest.TestCase):
    def setUp(self):
        self.d = api.Splunkd("https://127.0.0.1:8089/", "ST", "UT", "dom\\bob")
        self.reqs = []

    def answer(self, body):
        def urlopen(req, timeout=None, context=None):
            self.reqs.append(req)
            return FakeResponse(json.dumps(body).encode())
        return mock.patch.object(api.urllib.request, "urlopen", urlopen)

    def test_kv_query_uses_system_token(self):
        with self.answer([{"_key": "a"}]):
            self.assertEqual(self.d.kv_query(api.JOBS, {"status": "pending"}, sort="submitted_at:-1",
                                             limit=5, fields="a,b"), [{"_key": "a"}])
        r = self.reqs[0]
        url = urllib.parse.urlsplit(r.full_url)
        self.assertEqual(url.path, "/servicesNS/nobody/promotion_control/storage/collections/data/promotion_jobs")
        q = dict(urllib.parse.parse_qsl(url.query))
        self.assertEqual((q["query"], q["sort"], q["limit"], q["fields"]),
                         ('{"status": "pending"}', "submitted_at:-1", "5", "a,b"))
        self.assertEqual(r.get_header("Authorization"), "Splunk ST")

    def test_kv_query_pages_past_max_rows(self):
        pages = [[{"_key": str(i)} for i in range(3)], [{"_key": "3"}]]

        def urlopen(req, timeout=None, context=None):
            self.reqs.append(req)
            return FakeResponse(json.dumps(pages.pop(0)).encode())
        with mock.patch.object(api.urllib.request, "urlopen", urlopen), \
                mock.patch.object(api.Splunkd, "KV_PAGE", 3):
            rows = self.d.kv_query(api.CATALOG, {"agent": "a"})
        self.assertEqual([r["_key"] for r in rows], ["0", "1", "2", "3"])
        qs = [dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.full_url).query)) for r in self.reqs]
        self.assertEqual([(q["skip"], q["limit"]) for q in qs], [("0", "3"), ("3", "3")])

    def test_kv_query_limit_stops_early(self):
        def urlopen(req, timeout=None, context=None):
            self.reqs.append(req)
            return FakeResponse(json.dumps([{"_key": "x"}] * 3).encode())
        with mock.patch.object(api.urllib.request, "urlopen", urlopen), \
                mock.patch.object(api.Splunkd, "KV_PAGE", 3):
            self.assertEqual(len(self.d.kv_query(api.JOBS, limit=5)), 5)
        qs = [dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.full_url).query)) for r in self.reqs]
        self.assertEqual([(q["skip"], q["limit"]) for q in qs], [("0", "3"), ("3", "2")])

    def test_kv_save(self):
        with self.answer(["j1"]):
            self.d.kv_save(api.JOBS, {"_key": "j1"})
        r = self.reqs[0]
        self.assertTrue(r.full_url.split("?")[0].endswith("/promotion_jobs/batch_save"))
        self.assertEqual(json.loads(r.data), [{"_key": "j1"}])

    def test_user_search_uses_user_token_and_namespace(self):
        with self.answer({"results": [{"count": "1"}], "messages": []}):
            rows = self.d.search("| makeresults", earliest="-1h", latest="now", as_user=True)
        self.assertEqual(rows, [{"count": "1"}])
        r = self.reqs[0]
        self.assertEqual(urllib.parse.urlsplit(r.full_url).path,
                         "/servicesNS/dom%5Cbob/promotion_control/search/jobs")
        self.assertEqual(r.get_header("Authorization"), "Splunk UT")
        form = dict(urllib.parse.parse_qsl(r.data.decode()))
        self.assertEqual((form["exec_mode"], form["earliest_time"], form["latest_time"]), ("oneshot", "-1h", "now"))

    def test_search_errors(self):
        with self.answer({"messages": [{"type": "FATAL", "text": "bad search"}]}):
            with self.assertRaises(api.ApiError) as cm:
                self.d.search("| x", as_user=True)
        self.assertEqual((cm.exception.status, cm.exception.message), (400, "bad search"))

    def test_http_errors(self):
        def urlopen(req, timeout=None, context=None):
            raise api.urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b"{}"))
        with mock.patch.object(api.urllib.request, "urlopen", urlopen):
            self.assertIsNone(self.d.kv_get(api.JOBS, "x"))
            with self.assertRaises(api.ApiError) as cm:
                self.d.kv_save(api.JOBS, {"_key": "x"})
        self.assertEqual(cm.exception.status, 502)


class TestEndToEnd(Base):
    """A job created through the API is picked up and run by the forwarder add-on."""

    def test_api_job_runs_on_the_agent(self):
        for h in range(3):
            self.write_hour(H06 + h * HOUR, [event(H06 + h * HOUR + 1)])
        ra.publish_catalog(self.kv, self.backend, AGENT)
        d = FakeSplunkd(self.kv)
        status, out = api.Api(d, "alice", now=lambda: NOW).handle(
            "POST", "jobs", {}, {"sourcetype": "splunkd", "earliest": H06, "latest": H06 + 2 * HOUR,
                                 "forwarders": [GUID]})
        self.assertEqual(status, 201)
        job_id = out["job"]["job_id"]
        self.agent_run()
        task = self.task(job_id)
        self.assertEqual(task["status"], "done", task)
        staged = self.staged_events(job_id)
        self.assertEqual([e["time"] for e in staged], [H06 + 1, H06 + HOUR + 1])
        for e in staged:
            self.assertEqual(e["fields"]["promote_job"], job_id)


if __name__ == "__main__":
    unittest.main()
