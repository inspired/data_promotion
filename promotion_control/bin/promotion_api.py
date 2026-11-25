"""
Data Promotion REST API (Search Head): /services/data_promotion/...

The Data Promotion page, SOAR and scripts all use this API. Splunk calls handle_request()
through the persistent REST handler in promotion_rest.py (restmap.conf). Every call needs
the promote_data capability (checked by splunkd before the handler runs).

    GET  catalog                 data the forwarders hold, per sourcetype and UTC day
    GET  catalog?forwarder=<g>   one forwarder's data (rows of the agents holding its files)
    GET  forwarders              every forwarder: what it holds, status, add-on version, limit
    GET  fields?sourcetype=<st>  sampled indexes and indexed field names, one row per agent
    GET  estimate                size estimate for sourcetype + range (+ forwarders)
    GET  jobs                    jobs, newest first
    POST jobs                    create a job (or validate only, with "dry_run": true)
    GET  jobs/<id>               one job
    GET  jobs/<id>/tasks         progress per forwarder
    POST jobs/<id>/cancel        cancel a pending or running job
    GET  jobs/<id>/verify        indexed events of a job by index, sourcetype and host

The KV store is read and written with the system token: people need the capability, not
access to the collections. Time ranges are resolved, and verify searches run, as the
caller, so their time zone and index permissions apply.

Only the Python standard library is used.
"""

import json
import logging
import logging.handlers
import os
import random
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

APP = "promotion_control"
JOBS = "promotion_jobs"
CATALOG = "promotion_catalog"
SOURCES = "promotion_sources"
FIELDS = "promotion_fields"
TASKS = "promotion_tasks"
AGENTS = "promotion_agents"
FORWARDER_NAMES = "promotion_forwarder_names"

SOURCETYPE_RE = re.compile(r"^[A-Za-z0-9_.:\-]+$")
INDEX_FILTER_RE = re.compile(r"^[A-Za-z0-9_*\-]+$")
INDEX_NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,79}$")
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
GUID_RE = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$")
EPOCH_RE = re.compile(r"^\d+(\.\d+)?$")
# Splunk time modifiers (-24h@h, @d, now, -1mon@mon+3h ...); passed as a search time range.
TIME_MODIFIER_RE = re.compile(r"^[A-Za-z0-9@+\-.:]{1,64}$")

# Add-on defaults (TA-promotion_agent): forwarders before 2.2.0 don't report their limit.
DEFAULT_MAX_BYTES_PER_JOB = 53687091200
FILE_SLACK_SECONDS = 3600
FORWARDER_STALE_SECONDS = 15 * 60
MAX_JOBS_LISTED = 1000

log = logging.getLogger("promotion_api")


class ApiError(Exception):
    def __init__(self, status, message, field=None):
        Exception.__init__(self, message)
        self.status = status
        self.message = message
        self.field = field

    def payload(self):
        out = {"error": self.message}
        if self.field:
            out["field"] = self.field
        return out


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def _list(v):
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def validate_sourcetype(st):
    st = str(st or "").strip()
    if not st:
        raise ApiError(400, "sourcetype is required", "sourcetype")
    if st == "*" or ".." in st or not SOURCETYPE_RE.match(st):
        raise ApiError(400, "sourcetype has unsupported characters", "sourcetype")
    return st


def validate_index_filter(v):
    v = str(v if v is not None else "*").strip() or "*"
    if not INDEX_FILTER_RE.match(v):
        raise ApiError(400, "index_filter: use letters, digits, _, - and * only", "index_filter")
    return v


def validate_target_index(v):
    v = str(v or "").strip()
    if v in ("", "*"):
        return ""
    if not INDEX_NAME_RE.match(v):
        raise ApiError(400, "target_index: use lowercase letters, digits, _ and -, at most 80 "
                            "characters, not starting with -", "target_index")
    return v


def parse_forwarders(v, field="forwarders"):
    """[] (all) or a sorted list of upper-case server GUIDs. Accepts a list, or a string
    with GUIDs separated by spaces or commas; "*" or empty means all."""
    items = []
    for x in _list(v):
        items += [p for p in re.split(r"[\s,]+", str(x).strip()) if p]
    if not items or items == ["*"]:
        return []
    guids = sorted({g.upper() for g in items})
    bad = [g for g in guids if not GUID_RE.match(g)]
    if bad:
        raise ApiError(400, "invalid forwarder GUID: %s" % bad[0], field)
    return guids


def validate_job_id(job_id):
    if not job_id or not JOB_ID_RE.match(job_id):
        raise ApiError(404, "no such job")
    return job_id


def parse_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------- #
# SPL (values are validated above; only these few searches are run)
# --------------------------------------------------------------------------- #

def catalog_search(sourcetype=None):
    where = ' where sourcetype="%s"' % sourcetype if sourcetype else ""
    return ("| inputlookup promotion_catalog%s "
            "| stats sum(files) AS files sum(bytes) AS bytes min(earliest) AS earliest "
            "max(latest) AS latest sum(forwarders) AS forwarders dc(agent) AS agents "
            "max(updated_at) AS updated_at by sourcetype day" % where)


def estimate_search(sourcetype, agents, earliest, latest, slack=FILE_SLACK_SECONDS):
    """Bytes each agent would read: catalog rows are per agent and UTC day; a partly
    covered day counts in proportion, widened by the file slack (agents also read
    neighbouring files)."""
    agent_filter = ""
    if agents:
        agent_filter = " | where " + " OR ".join('agent="%s"' % a for a in agents)
    return ('| inputlookup promotion_catalog where sourcetype="%s"%s'
            " | eval slo=%d, shi=%d, earliest=tonumber(earliest), latest=tonumber(latest)"
            " | where latest > slo AND earliest < shi"
            " | eval part=(min(latest, shi) - max(earliest, slo)) / max(latest - earliest, 1)"
            " | eval bytes=round(tonumber(bytes) * min(1, max(0, part)))"
            " | stats sum(bytes) AS bytes by agent"
            % (sourcetype, agent_filter, int(earliest) - slack, int(latest) + slack))


def verify_search(job_id):
    return ('| tstats count where (index=* OR index=_*) promote_job="%s" '
            "by index sourcetype host | sort 0 - count" % job_id)


RESOLVE_SEARCH = "| makeresults | addinfo | table info_min_time info_max_time"


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #

def build_forwarders(sources, agents, names, now):
    """One row per forwarder (writer GUID in the archive filenames): what it holds (its
    promotion_sources rows) merged with its own registration (promotion_agents) and, for
    forwarders that never registered, a name from indexer metrics."""
    rows = {}

    def row(guid):
        if guid not in rows:
            rows[guid] = {"guid": guid, "agents": [], "sourcetypes": [], "days": 0, "files": 0,
                          "bytes": 0, "earliest": None, "latest": None}
        return rows[guid]

    for s in sources:
        if not s.get("guid"):
            continue
        r = row(str(s["guid"]).upper())
        a = str(s.get("agent") or "").upper()
        if a and a not in r["agents"]:
            r["agents"].append(a)
        r["sourcetypes"] = sorted(set(r["sourcetypes"]) | set(_list(s.get("sourcetypes"))))
        r["days"] = max(r["days"], _int(s.get("days")) or 0)
        r["files"] += _int(s.get("files")) or 0
        r["bytes"] += _int(s.get("bytes")) or 0
        e, l = _num(s.get("earliest")), _num(s.get("latest"))
        if e is not None and (r["earliest"] is None or e < r["earliest"]):
            r["earliest"] = e
        if l is not None and (r["latest"] is None or l > r["latest"]):
            r["latest"] = l

    by_agent = {}
    for a in agents:
        if a.get("guid"):
            g = str(a["guid"]).upper()
            by_agent[g] = a
            row(g)
    name_by = {str(n["guid"]).upper(): n for n in names if n.get("guid")}

    out = []
    for g, r in rows.items():
        a = by_agent.get(g)
        last_seen = _num(a.get("last_seen")) if a else None
        hostname = (a and a.get("hostname")) or (name_by.get(g) or {}).get("hostname") or None
        if not a:
            status = "unregistered"
        elif last_seen is not None and now - last_seen <= FORWARDER_STALE_SECONDS:
            status = "reporting"
        else:
            status = "stale"
        limit = _int(a.get("max_bytes_per_job")) if a else None
        r.update(hostname=hostname, label=hostname or g, status=status, last_seen=last_seen,
                 version=(a and a.get("agent_version")) or None, max_bytes_per_job=limit)
        out.append(r)
    out.sort(key=lambda x: x["label"].lower())
    return out


def agents_for(sources, sourcetype, forwarders):
    """Agent GUIDs holding data for sourcetype from the selected writers ([] = all)."""
    want = set(forwarders)
    out = set()
    for s in sources:
        if sourcetype not in _list(s.get("sourcetypes")):
            continue
        if want and str(s.get("guid") or "").upper() not in want:
            continue
        agent = str(s.get("agent") or "").upper()
        # Forwarders write these records; only well-formed GUIDs reach a search.
        if GUID_RE.match(agent):
            out.add(agent)
    return sorted(out)


def summarize_estimate(rows, agents, earliest, latest):
    """Estimate rows (agent, bytes) + promotion_agents -> summary. over: agents whose
    estimate is above their max_bytes_per_job; their task fails before reading anything."""
    by_agent = {str(a.get("guid") or "").upper(): a for a in agents}
    items = []
    for r in rows:
        b = _int(r.get("bytes")) or 0
        if b <= 0:
            continue
        g = str(r.get("agent") or "").upper()
        a = by_agent.get(g) or {}
        reported = _int(a.get("max_bytes_per_job"))
        items.append({"guid": g, "name": a.get("hostname") or g, "bytes": b,
                      "limit": reported or DEFAULT_MAX_BYTES_PER_JOB,
                      "limit_reported": bool(reported)})
    items.sort(key=lambda x: (-x["bytes"], x["name"]))
    return {"earliest": earliest, "latest": latest, "seconds": latest - earliest,
            "forwarders": len(items), "bytes": sum(i["bytes"] for i in items),
            "largest": items[0] if items else None,
            "over_limit": [i for i in items if i["bytes"] > i["limit"]],
            "per_forwarder": items}


def new_job_id(now):
    return "%s-%06x" % (time.strftime("%Y%m%d-%H%M%S", time.gmtime(now)), random.getrandbits(24))


def public(rec):
    """A KV record without KV bookkeeping fields."""
    return {k: v for k, v in rec.items() if k not in ("_user",)}


# --------------------------------------------------------------------------- #
# The API
# --------------------------------------------------------------------------- #

class Api:
    def __init__(self, splunkd, user, now=time.time):
        self.d = splunkd
        self.user = user or "unknown"
        self.now = now

    # ---- routing ----
    def handle(self, method, path, query, body):
        parts = [p for p in path.strip("/").split("/") if p]
        method = method.upper()
        routes = {
            ("GET", "catalog"): self.get_catalog,
            ("GET", "forwarders"): self.get_forwarders,
            ("GET", "fields"): self.get_fields,
            ("GET", "estimate"): self.get_estimate,
            ("GET", "jobs"): self.list_jobs,
            ("POST", "jobs"): self.create_job,
        }
        if len(parts) == 1 and (method, parts[0]) in routes:
            return routes[(method, parts[0])](query, body)
        if len(parts) in (2, 3) and parts[0] == "jobs":
            job_id = validate_job_id(parts[1])
            sub = parts[2] if len(parts) == 3 else ""
            job_routes = {("GET", ""): self.get_job, ("GET", "tasks"): self.get_tasks,
                          ("POST", "cancel"): self.cancel_job, ("GET", "verify"): self.verify_job}
            if (method, sub) in job_routes:
                return job_routes[(method, sub)](job_id)
            if any(k[1] == sub for k in job_routes):
                raise ApiError(405, "method not allowed")
        known = {"catalog", "forwarders", "fields", "estimate", "jobs"}
        if len(parts) == 1 and parts[0] in known:
            raise ApiError(405, "method not allowed")
        raise ApiError(404, "no such endpoint")

    # ---- data ----
    def get_catalog(self, q, _):
        fwd = parse_forwarders(q.get("forwarder"), "forwarder")
        if fwd:
            srcs = self.d.kv_query(SOURCES, fields="agent,guid")
            agents = sorted({str(s["agent"]).upper() for s in srcs
                             if str(s.get("guid") or "").upper() in fwd and s.get("agent")}) or fwd
            rows = []
            for a in agents:
                rows += self.d.kv_query(CATALOG, {"agent": a},
                                        fields="agent,sourcetype,day,files,bytes,earliest,latest,"
                                               "forwarders,updated_at")
            return 200, {"items": [public(r) for r in rows]}
        st = validate_sourcetype(q["sourcetype"]) if q.get("sourcetype") else None
        return 200, {"items": self.d.search(catalog_search(st))}

    def get_forwarders(self, q, _):
        sources = self.d.kv_query(SOURCES, fields="agent,guid,sourcetypes,days,files,bytes,earliest,latest")
        agents = self.d.kv_query(AGENTS)
        names = self.d.kv_query(FORWARDER_NAMES, fields="guid,hostname,last_seen")
        items = build_forwarders(sources, agents, names, self.now())
        st = q.get("sourcetype")
        if st:
            st = validate_sourcetype(st)
            items = [f for f in items if st in f["sourcetypes"]]
        return 200, {"items": items}

    def get_fields(self, q, _):
        st = validate_sourcetype(q.get("sourcetype"))
        rows = self.d.kv_query(FIELDS, {"sourcetype": st},
                               fields="agent,indexes,indexed_fields,events_sampled,sampled_at")
        return 200, {"items": [public(r) for r in rows]}

    def get_estimate(self, q, _):
        st = validate_sourcetype(q.get("sourcetype"))
        fwd = parse_forwarders(q.get("forwarders"))
        earliest, latest = self.resolve_range(q.get("earliest"), q.get("latest"))
        return 200, self.estimate(st, fwd, earliest, latest)

    def estimate(self, sourcetype, forwarders, earliest, latest, sources=None):
        if sources is None:
            sources = self.d.kv_query(SOURCES, fields="agent,guid,sourcetypes")
        agents = agents_for(sources, sourcetype, forwarders)
        rows = self.d.search(estimate_search(sourcetype, agents, earliest, latest)) if agents else []
        registered = self.d.kv_query(AGENTS, fields="guid,hostname,max_bytes_per_job")
        return summarize_estimate(rows, registered, earliest, latest)

    def resolve_range(self, earliest, latest):
        """Epochs or Splunk time modifiers -> (earliest, latest) epochs, latest no later
        than now. Modifiers are resolved as the caller (their time zone)."""
        if earliest in (None, "", 0, "0"):
            raise ApiError(400, "a bounded time range is required: set earliest (not All time)", "earliest")
        latest = "now" if latest in (None, "") else latest
        e, l = str(earliest).strip(), str(latest).strip()
        for v, name in ((e, "earliest"), (l, "latest")):
            if not EPOCH_RE.match(v) and not TIME_MODIFIER_RE.match(v):
                raise ApiError(400, "%s: use an epoch or a Splunk time modifier such as -24h@h" % name, name)
            if v.startswith("rt"):
                raise ApiError(400, "real-time ranges are not supported", name)
        if EPOCH_RE.match(e) and EPOCH_RE.match(l):
            lo, hi = float(e), float(l)
        else:
            rows = self.d.search(RESOLVE_SEARCH, earliest=e, latest=l, as_user=True)
            if not rows:
                raise ApiError(400, "could not resolve the time range")
            lo, hi = _num(rows[0].get("info_min_time")), _num(rows[0].get("info_max_time"))
            if lo is None:
                raise ApiError(400, "a bounded time range is required: set earliest (not All time)", "earliest")
        now = self.now()
        lo, hi = int(lo), int(-(-min(hi if hi is not None else now, now) // 1))
        if lo <= 0:
            raise ApiError(400, "a bounded time range is required: set earliest (not All time)", "earliest")
        if hi <= lo:
            raise ApiError(400, "latest must be after earliest", "latest")
        return lo, hi

    # ---- jobs ----
    def list_jobs(self, q, _):
        limit = _int(q.get("limit")) or 200
        limit = max(1, min(limit, MAX_JOBS_LISTED))
        query = {}
        if q.get("status"):
            query["status"] = str(q["status"])
        rows = self.d.kv_query(JOBS, query or None, sort="submitted_at:-1", limit=limit)
        return 200, {"items": [public(r) for r in rows]}

    def _job(self, job_id):
        job = self.d.kv_get(JOBS, job_id)
        if not job:
            raise ApiError(404, "no such job")
        return job

    def get_job(self, job_id):
        return 200, public(self._job(job_id))

    def get_tasks(self, job_id):
        self._job(job_id)
        rows = self.d.kv_query(TASKS, {"job_id": job_id}, sort="agent_host")
        return 200, {"items": [public(r) for r in rows]}

    def create_job(self, _, body):
        body = body or {}
        if not isinstance(body, dict):
            raise ApiError(400, "send a JSON object")
        st = validate_sourcetype(body.get("sourcetype"))
        index_filter = validate_index_filter(body.get("index_filter"))
        target = validate_target_index(body.get("target_index"))
        fwd = parse_forwarders(body.get("forwarders"))
        earliest, latest = self.resolve_range(body.get("earliest"), body.get("latest"))
        sources = self.d.kv_query(SOURCES, fields="agent,guid,sourcetypes")
        agents = agents_for(sources, st, fwd)
        if not agents:
            raise ApiError(400, "no forwarder holds data for this sourcetype"
                                + (" among the selected forwarders" if fwd else ""), "sourcetype")
        est = self.estimate(st, fwd, earliest, latest, sources=sources)
        warnings = ["%s would read %d bytes, over its limit of %d (max_bytes_per_job): it will fail "
                    "and promote nothing" % (o["name"], o["bytes"], o["limit"]) for o in est["over_limit"]]
        if est["over_limit"] and parse_bool(body.get("reject_over_limit", False)):
            raise ApiError(400, "; ".join(warnings), "earliest")

        now = int(self.now())
        job_id = new_job_id(now)
        job = {"_key": job_id, "job_id": job_id, "sourcetype": st, "index_filter": index_filter,
               "forwarders": " ".join(fwd) if fwd else "*", "forwarders_count": len(fwd),
               "agents_expected": len(agents), "earliest": earliest, "latest": latest,
               "status": "pending", "submitted_by": self.user, "submitted_at": now,
               "message": "waiting for forwarders"}
        if target:
            job["target_index"] = target
        if parse_bool(body.get("dry_run", False)):
            return 200, {"job": job, "estimate": est, "warnings": warnings, "dry_run": True}
        self.d.kv_save(JOBS, job)
        log.info("action=job_submitted job=%s user=%s sourcetype=%s earliest=%d latest=%d forwarders=%s "
                 "target_index=%s agents_expected=%d bytes=%d over_limit=%d", job_id, self.user, st,
                 earliest, latest, job["forwarders"] if fwd else "all", target or "original",
                 len(agents), est["bytes"], len(est["over_limit"]))
        return 201, {"job": job, "estimate": est, "warnings": warnings}

    def cancel_job(self, job_id):
        job = self._job(job_id)
        status = job.get("status")
        if status == "pending":
            job.update(status="cancelled", finished_at=int(self.now()))
        elif status == "running":
            job["status"] = "cancel_requested"
        else:
            raise ApiError(409, "job is %s; it can no longer be cancelled" % status)
        job["message"] = "cancelled by %s" % self.user
        self.d.kv_save(JOBS, job)
        log.info("action=job_cancelled job=%s user=%s status=%s", job_id, self.user, job["status"])
        return 200, public(job)

    def verify_job(self, job_id):
        job = self._job(job_id)
        e, l = _num(job.get("earliest")), _num(job.get("latest"))
        if e is None or l is None:
            raise ApiError(409, "job has no time range")
        rows = self.d.search(verify_search(job_id), earliest=str(int(e)), latest=str(int(-(-l // 1)) + 1),
                             as_user=True)
        return 200, {"items": rows, "events": sum(_int(r.get("count")) or 0 for r in rows)}


# --------------------------------------------------------------------------- #
# splunkd access
# --------------------------------------------------------------------------- #

class Splunkd:
    """splunkd REST: KV store with the system token, searches as the system or the caller."""

    def __init__(self, rest_uri, system_token, user_token, user, timeout=120):
        self.base = rest_uri.rstrip("/")
        self.system_token = system_token
        self.user_token = user_token
        self.user = user
        self.timeout = timeout
        # splunkd on this host, usually with its default self-signed certificate.
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

    def _call(self, method, path, params=None, body=None, form=None, token=None):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Authorization": "Splunk %s" % (token or self.system_token)}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as res:
                raw = res.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 and method == "GET":
                return None
            detail = e.read().decode("utf-8", "replace")[:500]
            raise ApiError(502, "splunkd %s %s failed (%d): %s" % (method, path, e.code, detail))
        return json.loads(raw.decode("utf-8")) if raw else None

    def _kv(self, coll, suffix=""):
        return "/servicesNS/nobody/%s/storage/collections/data/%s%s" % (APP, coll, suffix)

    def kv_query(self, coll, query=None, sort=None, limit=None, fields=None):
        params = {"output_mode": "json"}
        if query:
            params["query"] = json.dumps(query)
        if sort:
            params["sort"] = sort
        params["limit"] = str(limit or 0)
        if fields:
            params["fields"] = fields
        return self._call("GET", self._kv(coll), params) or []

    def kv_get(self, coll, key):
        return self._call("GET", self._kv(coll, "/" + urllib.parse.quote(key, safe="")),
                          {"output_mode": "json"})

    def kv_save(self, coll, rec):
        self._call("POST", self._kv(coll, "/batch_save"), {"output_mode": "json"}, body=[rec])

    def search(self, spl, earliest=None, latest=None, as_user=False):
        owner = urllib.parse.quote(self.user, safe="") if as_user else "nobody"
        form = {"search": spl, "exec_mode": "oneshot", "output_mode": "json", "count": "0"}
        if earliest is not None:
            form["earliest_time"] = earliest
        if latest is not None:
            form["latest_time"] = latest
        res = self._call("POST", "/servicesNS/%s/%s/search/jobs" % (owner, APP), form=form,
                         token=self.user_token if as_user else None) or {}
        fatal = [m for m in res.get("messages") or [] if m.get("type") in ("FATAL", "ERROR")]
        if fatal:
            raise ApiError(400 if as_user else 502, fatal[0].get("text", "search failed"))
        return res.get("results") or []


# --------------------------------------------------------------------------- #
# Persistent REST handler entry point
# --------------------------------------------------------------------------- #

def _setup_logging():
    if log.handlers:
        return
    home = os.environ.get("SPLUNK_HOME", "/opt/splunk")
    path = os.path.join(home, "var", "log", "splunk", "promotion_api.log")
    try:
        h = logging.handlers.RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=3)
    except OSError:
        h = logging.NullHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


def _path(req):
    """The part of the URL after data_promotion/. splunkd passes it as path_info, or only the
    whole rest_path, depending on version: accept either, with or without the prefix."""
    p = req.get("path_info")
    if p is None:
        p = req.get("rest_path") or ""
    p = str(p).strip("/")
    parts = p.split("/")
    if "data_promotion" in parts:
        parts = parts[parts.index("data_promotion") + 1:]
    return "/".join(parts)


def _query(req):
    q = req.get("query") or {}
    if isinstance(q, list):
        out = {}
        for pair in q:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                k, v = pair
                # Repeated keys (forwarders=a&forwarders=b) become a list.
                out[k] = _list(out[k]) + [v] if k in out else v
        return out
    return dict(q)


def _body(req):
    raw = req.get("payload")
    if raw in (None, ""):
        form = req.get("form")
        if isinstance(form, list):
            return {k: v for k, v in form if isinstance(k, str)}
        return form or None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        parsed = urllib.parse.parse_qs(raw, keep_blank_values=True)
        if not parsed:
            raise ApiError(400, "send a JSON object")
        return {k: (v if len(v) > 1 else v[0]) for k, v in parsed.items()}


def handle_request(req, splunkd_factory=Splunkd, now=time.time):
    """One request from splunkd (persistent REST protocol) -> response dict."""
    _setup_logging()
    try:
        session = req.get("session") or {}
        user = session.get("user") or "unknown"
        rest_uri = (req.get("server") or {}).get("rest_uri") or "https://127.0.0.1:8089"
        d = splunkd_factory(rest_uri, req.get("system_authtoken"), session.get("authtoken"), user)
        method, path = req.get("method") or "GET", _path(req)
        log.debug("action=request method=%s path=%s user=%s", method, path, user)
        status, payload = Api(d, user, now).handle(method, path, _query(req), _body(req))
    except ApiError as e:
        status, payload = e.status, e.payload()
    except Exception as e:  # noqa: BLE001 - always answer with JSON
        log.exception("action=api_error")
        status, payload = 500, {"error": "internal error: %s" % e}
    return {"payload": json.dumps(payload), "status": status,
            "headers": {"Content-Type": "application/json"}}
