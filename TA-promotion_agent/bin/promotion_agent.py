#!/usr/bin/env python3
"""
Data promotion agent. Runs inside every Heavy Forwarder whose Ingest Actions output should
be promotable, reads that forwarder's own dump directory and promotes it through the
forwarder's built-in stash_hec input. Any number of forwarders work against one Search Head.

Runs as a Splunk scripted input (passAuth = splunk-system-user). Each run:
  1. Takes a non-blocking lock so runs never overlap.
  2. Identifies itself (server GUID and name from this instance's server/info) and
     registers in the Search Head KV store (promotion_agents).
  3. Publishes what it can promote (promotion_catalog, promotion_sources), every
     `catalog_interval` seconds. Rows are keyed by this agent's GUID, so agents never
     overwrite each other.
     With the catalog it samples the head of the newest files per sourcetype and
     publishes the indexes, hosts, sources and indexed field names seen there
     (promotion_fields). Event contents are never published.
  4. Pulls active jobs (promotion_jobs). For each job that targets data this agent holds,
     it creates its own task record (promotion_tasks, key <job id>|<agent GUID>) and:
       - selects Ingest Actions output files by day directory + filename epochs
       - streams them, keeping only events whose "time" is inside
         [earliest, latest) (and optionally matching an index filter)
       - tags each event with the indexed field promote_job=<job id>
       - sets the event's index to the job's destination index, if the job has one
       - renames filtered files (*.stash_hec) into the staging dir below
         $SPLUNK_HOME/var/spool/splunk, where Splunk's built-in [batch://...stash_hec]
         input (INDEXED_EXTRACTIONS = hec) reads them, so this forwarder sends them to
         its indexers
  Agents never write the job record; the Search Head's "Promotion - Track indexing"
  search rolls the tasks up into the job status.

Only the Python standard library is used. S3 access shells out to the AWS CLI.
"""

import configparser
import contextlib
import datetime
import fcntl
import fnmatch
import gzip
import json
import logging
import logging.handlers
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

APP_NAME = "TA-promotion_agent"
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS = "promotion_jobs"
CATALOG = "promotion_catalog"
SOURCES = "promotion_sources"
FIELDS = "promotion_fields"
TASKS = "promotion_tasks"
AGENTS = "promotion_agents"
ACTIVE_JOB_STATUSES = ["pending", "running"]
STOP_JOB_STATUSES = ("cancel_requested", "cancelled", "failed", "rejected")

# events_<epoch>_<epoch>_<n>_<seq>_<guid>.ndjson[.gz]
# Observed order is <latest>_<earliest>; we take min/max so order does not matter.
FILE_RE = re.compile(r"^events_(\d{9,11})_(\d{9,11})_.*\.ndjson(\.gz)?$")
# The trailing GUID is the server GUID of the instance that wrote the file (the HF).
GUID_RE = re.compile(
    r"_([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\.ndjson(?:\.gz)?$")
UNKNOWN_GUID = "UNKNOWN"
S3_KEY_RE = re.compile(
    r"(?:^|/)year=(\d{4})/month=(\d{1,2})/day=(\d{1,2})/sourcetype=([^/]+)/(events_[^/]+)$"
)
DIR_RE = {
    "year": re.compile(r"^year=(\d{4})$"),
    "month": re.compile(r"^month=(\d{1,2})$"),
    "day": re.compile(r"^day=(\d{1,2})$"),
    "sourcetype": re.compile(r"^sourcetype=(.+)$"),
}

# Splunk index names: lowercase letters, digits, _ and -, not starting with - (max 80).
INDEX_NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]{0,79}$")

# Indexed field that marks promoted events. Events in the dump that carry it are never
# promoted again.
JOB_FIELD = "promote_job"

log = logging.getLogger("promotion_agent")


class Cancelled(Exception):
    pass


class JobError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULTS = {
    "agent": {
        "backend": "filesystem",
        "fs_root": "/dumps/splunk9.4-filesystemout",
        "s3_uri": "",
        "aws_cli": "aws",
        "work_dir": "$SPLUNK_HOME/var/promotion/work",
        "staging_dir": "$SPLUNK_HOME/var/spool/splunk/promotion",
        "file_slack_seconds": "3600",
        "max_bytes_per_job": "53687091200",
        "min_free_bytes": "10737418240",
        "catalog_interval": "900",
        "sample_files": "3",
        "sample_lines": "200",
        "sample_bytes": "1048576",
        "sample_max_values": "100",
        "tag_events": "true",
        "heartbeat_interval": "300",
        "task_retention_days": "30",
        "log_level": "INFO",
    },
    "control": {
        "sh_uri": "",
        "app": "promotion_control",
        "verify_ssl": "true",
        "ca_file": "",
        "local_uri": "https://127.0.0.1:8089",
        "credential_realm": "promotion_agent",
        "credential_name": "sh_token",
        "request_timeout": "60",
    },
}


class Config:
    def __init__(self, app_dir=APP_DIR, overrides=None):
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_dict(DEFAULTS)
        cp.read([
            os.path.join(app_dir, "default", "promotion_agent.conf"),
            os.path.join(app_dir, "local", "promotion_agent.conf"),
        ])
        if overrides:
            cp.read_dict(overrides)
        self.cp = cp

    def get(self, section, key):
        return os.path.expandvars(self.cp.get(section, key).strip())

    def getint(self, section, key):
        return int(self.get(section, key))

    def getbool(self, section, key):
        return self.get(section, key).lower() in ("1", "true", "yes", "t", "y", "on")


# --------------------------------------------------------------------------- #
# HTTP / KV store
# --------------------------------------------------------------------------- #

def _http(method, url, headers, body=None, timeout=60, ctx=None):
    data = None
    headers = dict(headers)
    if body is not None:
        if isinstance(body, (bytes, str)):
            data = body.encode() if isinstance(body, str) else body
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read()
    if not raw:
        return None
    return json.loads(raw)


def get_credential(cfg, session_key, name):
    """Read a secret from this instance's storage/passwords (realm credential_realm)."""
    realm = cfg.get("control", "credential_realm")
    entity = urllib.parse.quote("%s:%s:" % (realm, name), safe="")
    url = "%s/servicesNS/nobody/%s/storage/passwords/%s?output_mode=json" % (
        cfg.get("control", "local_uri").rstrip("/"), APP_NAME, entity)
    # Local management port normally uses the default self-signed cert.
    ctx = ssl._create_unverified_context()
    res = _http("GET", url, {"Authorization": "Splunk %s" % session_key},
                timeout=30, ctx=ctx)
    return res["entry"][0]["content"]["clear_password"]


def get_sh_token(cfg, session_key):
    """Read the Search Head auth token from this HF's storage/passwords."""
    return get_credential(cfg, session_key, cfg.get("control", "credential_name"))


def read_app_version(app_dir=APP_DIR):
    try:
        with open(os.path.join(app_dir, "default", "app.conf")) as f:
            for line in f:
                m = re.match(r"\s*version\s*=\s*(\S+)", line)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return ""


def whoami(cfg, session_key):
    """This instance's identity: server GUID (as used in dump filenames) and name."""
    url = "%s/services/server/info?output_mode=json" % cfg.get("control", "local_uri").rstrip("/")
    res = _http("GET", url, {"Authorization": "Splunk %s" % session_key}, timeout=30,
                ctx=ssl._create_unverified_context())
    c = res["entry"][0]["content"]
    return {"guid": str(c["guid"]).upper(), "hostname": c.get("serverName") or socket.gethostname(),
            "splunk_version": c.get("version", ""), "os": c.get("os_name", "")}


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def control_target(cfg, session_key, token_reader=None):
    """Return (uri, auth_header, verify_ssl) for the KV store holding promotion_control.

    All-in-one: when sh_uri is empty or points at this host, use the session key
    Splunk passes to the scripted input. No stored token is needed.
    Otherwise use the Search Head token from storage/passwords.
    """
    uri = cfg.get("control", "sh_uri") or cfg.get("control", "local_uri")
    if urllib.parse.urlparse(uri).hostname in LOCAL_HOSTS:
        return uri, "Splunk %s" % session_key, False
    token = (token_reader or get_sh_token)(cfg, session_key)
    return uri, "Bearer %s" % token, cfg.getbool("control", "verify_ssl")


class KVStore:
    """Minimal client for /storage/collections/data on the Search Head."""

    def __init__(self, base_uri, app, auth_header, verify=True, ca_file=None, timeout=60):
        self.base = "%s/servicesNS/nobody/%s/storage/collections/data" % (
            base_uri.rstrip("/"), app)
        self.headers = {"Authorization": auth_header}
        self.timeout = timeout
        if verify:
            self.ctx = ssl.create_default_context(cafile=ca_file or None)
        else:
            self.ctx = ssl._create_unverified_context()

    def _url(self, coll, key=None, suffix=None, params=None):
        url = "%s/%s" % (self.base, coll)
        if key is not None:
            url += "/" + urllib.parse.quote(key, safe="")
        if suffix:
            url += "/" + suffix
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def _do(self, method, url, body=None):
        return _http(method, url, self.headers, body, self.timeout, self.ctx)

    def query(self, coll, query=None, sort=None, limit=None):
        params = {}
        if query is not None:
            params["query"] = json.dumps(query)
        if sort:
            params["sort"] = sort
        if limit:
            params["limit"] = str(limit)
        return self._do("GET", self._url(coll, params=params)) or []

    def get(self, coll, key):
        try:
            return self._do("GET", self._url(coll, key))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def put(self, coll, key, record):
        """Insert or replace one record. (POST to .../<key> only updates existing
        records and returns 404 for new keys; batch_save upserts by _key.)"""
        rec = {k: v for k, v in record.items() if k != "_user"}
        rec["_key"] = key
        return self._do("POST", self._url(coll, suffix="batch_save"), [rec])

    def batch_save(self, coll, records, chunk=500):
        for i in range(0, len(records), chunk):
            self._do("POST", self._url(coll, suffix="batch_save"), records[i:i + chunk])

    def delete(self, coll, query):
        return self._do("DELETE", self._url(coll, params={"query": json.dumps(query)}))


# --------------------------------------------------------------------------- #
# Storage backends
# --------------------------------------------------------------------------- #

def parse_writer_guid(name):
    """Server GUID of the HF that wrote the file (upper case), or UNKNOWN."""
    m = GUID_RE.search(name)
    return m.group(1).upper() if m else UNKNOWN_GUID


def parse_forwarders(value):
    """Job 'forwarders' field -> set of GUIDs, or None for all forwarders."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = re.split(r"[\s,;|]+", str(value))
    guids = {i.strip().upper() for i in items if i and i.strip()}
    if not guids or "*" in guids:
        return None
    bad = [g for g in guids if g != UNKNOWN_GUID and not re.match(r"^[0-9A-F-]{36}$", g)]
    if bad:
        raise JobError("invalid forwarder GUID(s): %s" % ", ".join(sorted(bad)[:5]))
    return guids


def parse_file_range(name):
    m = FILE_RE.match(name)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    return min(a, b), max(a, b)


def utc_days(lo, hi):
    d = datetime.datetime.fromtimestamp(max(lo, 0), datetime.timezone.utc).date()
    end = datetime.datetime.fromtimestamp(max(hi, 0), datetime.timezone.utc).date()
    while d <= end:
        yield d
        d += datetime.timedelta(days=1)


def _variants(n):
    # Partition values may or may not be zero-padded; try both.
    return list(dict.fromkeys(["%02d" % n, str(n)]))


@contextlib.contextmanager
def open_ndjson(path):
    f = gzip.open(path, "rb") if path.endswith(".gz") else open(path, "rb")
    try:
        yield f
    finally:
        f.close()


class FilesystemBackend:
    name = "filesystem"

    def __init__(self, root):
        self.root = root

    def list_partition(self, day, sourcetype):
        """Return [(name, size, handle)] for one day/sourcetype partition."""
        out = []
        for mm in _variants(day.month):
            for dd in _variants(day.day):
                p = os.path.join(self.root, "year=%04d" % day.year, "month=" + mm,
                                 "day=" + dd, "sourcetype=" + sourcetype)
                if not os.path.isdir(p):
                    continue
                for name in os.listdir(p):
                    full = os.path.join(p, name)
                    if FILE_RE.match(name) and os.path.isfile(full):
                        out.append((name, os.path.getsize(full), full))
        return out

    @contextlib.contextmanager
    def open(self, handle, work_dir):
        with open_ndjson(handle) as f:
            yield f

    @contextlib.contextmanager
    def open_head(self, handle, work_dir, max_bytes):
        """Open a file for reading its first events only (the whole file is local)."""
        with open_ndjson(handle) as f:
            yield f

    def walk(self):
        """Yield (sourcetype, date, name, size, handle) for every file."""
        def subdirs(path, kind):
            try:
                names = os.listdir(path)
            except OSError:
                return
            for n in names:
                m = DIR_RE[kind].match(n)
                if m and os.path.isdir(os.path.join(path, n)):
                    yield m.group(1), os.path.join(path, n)

        for y, yp in subdirs(self.root, "year"):
            for mo, mp in subdirs(yp, "month"):
                for d, dp in subdirs(mp, "day"):
                    try:
                        date = datetime.date(int(y), int(mo), int(d))
                    except ValueError:
                        continue
                    for st, sp in subdirs(dp, "sourcetype"):
                        for name in os.listdir(sp):
                            full = os.path.join(sp, name)
                            if FILE_RE.match(name) and os.path.isfile(full):
                                yield st, date, name, os.path.getsize(full), full


class S3Backend:
    name = "s3"

    def __init__(self, s3_uri, aws_cli="aws"):
        u = urllib.parse.urlparse(s3_uri)
        if u.scheme != "s3" or not u.netloc:
            raise ValueError("s3_uri must look like s3://bucket/prefix")
        self.bucket = u.netloc
        self.prefix = u.path.strip("/")
        self.aws = aws_cli

    def _key(self, *parts):
        return "/".join([p for p in (self.prefix,) + parts if p])

    def _list(self, prefix):
        res = subprocess.run(
            [self.aws, "s3api", "list-objects-v2", "--bucket", self.bucket,
             "--prefix", prefix, "--output", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
        if res.returncode != 0:
            raise JobError("aws s3api list-objects-v2 failed: %s"
                           % res.stderr.decode(errors="replace").strip())
        if not res.stdout.strip():
            return []
        return json.loads(res.stdout).get("Contents") or []

    def list_partition(self, day, sourcetype):
        out, seen = [], set()
        for mm in _variants(day.month):
            for dd in _variants(day.day):
                prefix = self._key("year=%04d" % day.year, "month=" + mm,
                                   "day=" + dd, "sourcetype=" + sourcetype) + "/"
                for obj in self._list(prefix):
                    key = obj["Key"]
                    name = key.rsplit("/", 1)[-1]
                    if FILE_RE.match(name) and key not in seen:
                        seen.add(key)
                        out.append((name, int(obj.get("Size", 0)), key))
        return out

    @contextlib.contextmanager
    def open(self, handle, work_dir):
        suffix = ".ndjson.gz" if handle.endswith(".gz") else ".ndjson"
        fd, tmp = tempfile.mkstemp(prefix="s3dl_", suffix=suffix, dir=work_dir)
        os.close(fd)
        try:
            res = subprocess.run(
                [self.aws, "s3", "cp", "--only-show-errors",
                 "s3://%s/%s" % (self.bucket, handle), tmp],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3600)
            if res.returncode != 0:
                raise JobError("aws s3 cp failed: %s"
                               % res.stderr.decode(errors="replace").strip())
            with open_ndjson(tmp) as f:
                yield f
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)

    @contextlib.contextmanager
    def open_head(self, handle, work_dir, max_bytes):
        """Download only the first max_bytes of an object (ranged GET). The last line, or
        the gzip stream, is usually cut off; read_head() stops there."""
        suffix = ".ndjson.gz" if handle.endswith(".gz") else ".ndjson"
        fd, tmp = tempfile.mkstemp(prefix="s3head_", suffix=suffix, dir=work_dir)
        os.close(fd)
        try:
            res = subprocess.run(
                [self.aws, "s3api", "get-object", "--bucket", self.bucket, "--key", handle,
                 "--range", "bytes=0-%d" % (max_bytes - 1), tmp],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
            if res.returncode != 0:
                raise JobError("aws s3api get-object failed: %s"
                               % res.stderr.decode(errors="replace").strip())
            with open_ndjson(tmp) as f:
                yield f
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)

    def walk(self):
        for obj in self._list(self._key() + "/" if self.prefix else ""):
            m = S3_KEY_RE.search(obj["Key"])
            if not m:
                continue
            y, mo, d, st, name = m.groups()
            if not FILE_RE.match(name):
                continue
            try:
                date = datetime.date(int(y), int(mo), int(d))
            except ValueError:
                continue
            yield st, date, name, int(obj.get("Size", 0)), obj["Key"]


def make_backend(cfg):
    kind = cfg.get("agent", "backend").lower()
    if kind == "filesystem":
        return FilesystemBackend(cfg.get("agent", "fs_root"))
    if kind == "s3":
        return S3Backend(cfg.get("agent", "s3_uri"), cfg.get("agent", "aws_cli"))
    raise ValueError("unknown backend %r" % kind)


# --------------------------------------------------------------------------- #
# Catalog and registration
# --------------------------------------------------------------------------- #

def build_catalog(backend, agent, now=None, newest_per_sourcetype=0):
    """Walk the store once and return (partitions, sources, served, newest).

    partitions: one row per sourcetype + UTC day held by this agent, with the number of
                writing forwarders. Key <agent>|<sourcetype>|<day>.
    sources:    one row per writing forwarder (server GUID in the filename) held by this
                agent. Key <agent>|<writer>.
    served:     {writer GUID: [sourcetypes]}, used to decide which jobs target this agent.
    newest:     {sourcetype: [(name, handle)]}, its newest_per_sourcetype newest files
                (by the latest epoch in the filename), newest first. Used for sampling.
    """
    now = int(now or time.time())
    aguid = agent["guid"]
    parts, guids_per_part, sources, newest = {}, {}, {}, {}
    for st, date, name, size, handle in backend.walk():
        lo, hi = parse_file_range(name)
        if newest_per_sourcetype > 0:
            top = newest.setdefault(st, [])
            top.append((hi, name, handle))
            if len(top) > newest_per_sourcetype:
                top.sort(reverse=True)
                del top[newest_per_sourcetype:]
        guid = parse_writer_guid(name)
        key = "%s|%s|%s" % (aguid, st, date.isoformat())
        r = parts.setdefault(key, {
            "_key": key, "agent": aguid, "sourcetype": st, "day": date.isoformat(),
            "files": 0, "bytes": 0, "earliest": lo, "latest": hi,
            "backend": backend.name, "updated_at": now,
        })
        r["files"] += 1
        r["bytes"] += size
        r["earliest"] = min(r["earliest"], lo)
        r["latest"] = max(r["latest"], hi)
        guids_per_part.setdefault(key, set()).add(guid)

        s = sources.setdefault(guid, {
            "_key": "%s|%s" % (aguid, guid), "agent": aguid, "agent_host": agent["hostname"],
            "guid": guid, "sourcetypes": set(), "days": set(),
            "files": 0, "bytes": 0, "earliest": lo, "latest": hi,
            "backend": backend.name, "updated_at": now,
        })
        s["sourcetypes"].add(st)
        s["days"].add(date.isoformat())
        s["files"] += 1
        s["bytes"] += size
        s["earliest"] = min(s["earliest"], lo)
        s["latest"] = max(s["latest"], hi)

    for key, r in parts.items():
        r["forwarders"] = len(guids_per_part[key])
    for s in sources.values():
        s["sourcetypes"] = sorted(s["sourcetypes"])
        s["days"] = len(s["days"])
    served = {g: s["sourcetypes"] for g, s in sources.items()}
    newest = {st: [(n, h) for _, n, h in sorted(top, reverse=True)] for st, top in newest.items()}
    return list(parts.values()), list(sources.values()), served, newest


def publish_catalog(kv, backend, agent, newest_per_sourcetype=0):
    """Publish this agent's rows and remove only this agent's stale rows.
    Returns (served, totals, newest); see build_catalog."""
    now = int(time.time())
    parts, sources, served, newest = build_catalog(backend, agent, now, newest_per_sourcetype)
    kv.batch_save(CATALOG, parts)
    kv.delete(CATALOG, {"agent": agent["guid"], "updated_at": {"$lt": now}})
    kv.batch_save(SOURCES, sources)
    kv.delete(SOURCES, {"agent": agent["guid"], "updated_at": {"$lt": now}})
    log.info("action=catalog_published partitions=%d forwarders=%d", len(parts), len(sources))
    return served, {
        "forwarders": len(sources),
        "files": sum(s["files"] for s in sources),
        "bytes": sum(s["bytes"] for s in sources),
        "sourcetypes": sorted({st for s in sources for st in s["sourcetypes"]}),
    }, newest


# --------------------------------------------------------------------------- #
# Field sampling: what the sourcetypes contain (envelope values and field names only)
# --------------------------------------------------------------------------- #

def read_head(f, max_lines):
    """Yield up to max_lines raw lines; stop quietly at a cut-off end (ranged S3 GET)."""
    it = iter(f)
    n = 0
    while n < max_lines:
        try:
            raw = next(it)
        except StopIteration:
            return
        except (EOFError, OSError, zlib.error):
            return
        n += 1
        yield raw


class FieldSample:
    """Counts what a sample of HEC events of one sourcetype holds. Only the envelope
    values (index, host, source) and the names of indexed fields are kept: never the
    event text or indexed field values, which may be data that was kept out of Splunk
    on purpose."""

    def __init__(self):
        self.indexes, self.hosts, self.sources, self.fields = {}, {}, {}, {}
        self.events = self.files = 0

    @staticmethod
    def _inc(d, v):
        if v is None or v == "":
            return
        v = str(v)
        d[v] = d.get(v, 0) + 1

    def add_line(self, raw):
        line = raw.strip()
        if not line:
            return
        try:
            ev = json.loads(line)
        except ValueError:
            return
        if not isinstance(ev, dict):
            return
        fields = ev.get("fields")
        if isinstance(fields, dict) and JOB_FIELD in fields:
            return  # output of an earlier promotion; never promoted again
        self.events += 1
        self._inc(self.indexes, ev.get("index"))
        self._inc(self.hosts, ev.get("host"))
        self._inc(self.sources, ev.get("source"))
        if isinstance(fields, dict):
            for k in fields:
                self._inc(self.fields, k)

    def summary(self, max_values):
        def top(d):
            vals = sorted(d, key=lambda k: (-d[k], k))
            return vals[:max_values], len(vals) > max_values
        indexes, _ = top(self.indexes)
        hosts, hosts_more = top(self.hosts)
        sources, sources_more = top(self.sources)
        fields, fields_more = top(self.fields)
        return {
            "indexes": indexes, "hosts": hosts, "sources": sources, "indexed_fields": fields,
            "hosts_truncated": hosts_more, "sources_truncated": sources_more,
            "fields_truncated": fields_more,
            "events_sampled": self.events, "files_sampled": self.files,
        }


def sample_sourcetype(backend, files, work_dir, max_lines, max_bytes, max_values):
    """Sample the first max_lines events of each (name, handle) in files."""
    s = FieldSample()
    for name, handle in files:
        try:
            with backend.open_head(handle, work_dir, max_bytes) as f:
                for raw in read_head(f, max_lines):
                    s.add_line(raw)
            s.files += 1
        except Exception as e:  # noqa: BLE001 - one unreadable file must not stop sampling
            log.warning("action=sample_failed file=%s error=%s", name, e)
    return s.summary(max_values)


def publish_fields(kv, cfg, backend, agent, newest, cache, now=None):
    """Publish one promotion_fields row per sourcetype this agent holds.

    cache: {sourcetype: {"files": [names], "summary": {...}}} from the previous run.
    A sourcetype is sampled again only when its newest files changed, so an unchanged
    store costs no reads (and no S3 GETs). Returns the new cache."""
    now = int(now or time.time())
    work_dir = cfg.get("agent", "work_dir")
    max_lines = cfg.getint("agent", "sample_lines")
    max_bytes = cfg.getint("agent", "sample_bytes")
    max_values = cfg.getint("agent", "sample_max_values")
    new_cache, rows = {}, []
    for st, files in sorted(newest.items()):
        names = [n for n, _ in files]
        hit = cache.get(st)
        if hit and hit.get("files") == names and hit.get("summary"):
            entry = hit
        else:
            entry = {"files": names, "sampled_at": now,
                     "summary": sample_sourcetype(backend, files, work_dir, max_lines,
                                                  max_bytes, max_values)}
        new_cache[st] = entry
        rows.append(dict(entry["summary"], _key="%s|%s" % (agent["guid"], st),
                         agent=agent["guid"], sourcetype=st,
                         sampled_at=entry.get("sampled_at", now), updated_at=now))
    kv.batch_save(FIELDS, rows)
    kv.delete(FIELDS, {"agent": agent["guid"], "updated_at": {"$lt": now}})
    log.info("action=fields_published sourcetypes=%d sampled=%d", len(rows),
             sum(1 for st in new_cache if cache.get(st) is not new_cache[st]))
    return new_cache


def heartbeat(kv, cfg, agent, totals=None):
    """Register this forwarder (name, versions) in promotion_agents."""
    rec = kv.get(AGENTS, agent["guid"]) or {}
    rec.update({
        "guid": agent["guid"], "hostname": agent["hostname"],
        "splunk_version": agent.get("splunk_version", ""), "os": agent.get("os", ""),
        "agent_version": read_app_version(), "backend": cfg.get("agent", "backend"),
        "last_seen": int(time.time()),
    })
    if totals is not None:
        rec.update(totals)
        rec["catalog_at"] = int(time.time())
    kv.put(AGENTS, agent["guid"], rec)


# --------------------------------------------------------------------------- #
# Output: Splunk's built-in stash_hec input on this forwarder
# --------------------------------------------------------------------------- #

# Splunk's default inputs.conf reads every file under $SPLUNK_HOME/var/spool/splunk whose
# name ends in "stash_hec" with sourcetype stash_hec (INDEXED_EXTRACTIONS = hec), then
# deletes it. Each event's own time, host, source, sourcetype, index and fields apply.
STAGED_SUFFIX = ".stash_hec"


def staged_name(name):
    """events_..._<guid>.ndjson[.gz] -> events_..._<guid>.stash_hec"""
    for ext in (".gz", ".ndjson"):
        if name.endswith(ext):
            name = name[:-len(ext)]
    return name + STAGED_SUFFIX


class StagingSink:
    """Filtered files are written to work/<job>/, then renamed into staging/<job>/,
    where Splunk's stash_hec batch input indexes and deletes them."""

    def __init__(self, work_root, staging_root, job_key):
        self.work = os.path.join(work_root, job_key)
        self.staging = os.path.join(staging_root, job_key)
        os.makedirs(self.work, exist_ok=True)
        os.makedirs(self.staging, exist_ok=True)
        self.tmp = self.f = None

    def free_bytes(self):
        return shutil.disk_usage(self.work).free

    def begin(self, name):
        self.name_out = staged_name(name)
        self.tmp = os.path.join(self.work, self.name_out)
        self.f = open(self.tmp, "wb")
        return self.f

    def end(self, kept):
        self.f.close()
        if kept:
            # Same filesystem -> atomic; the input never sees partial files (and the work
            # file's name does not end in stash_hec, so it is never read early).
            os.replace(self.tmp, os.path.join(self.staging, self.name_out))
        else:
            os.remove(self.tmp)

    def close(self):
        if self.f and not self.f.closed:
            self.f.close()
        shutil.rmtree(self.work, ignore_errors=True)

    def done_message(self, events, files):
        return "%d events from %d files handed to the stash_hec input" % (events, files)


def make_sink(cfg, job_key):
    return StagingSink(cfg.get("agent", "work_dir"), cfg.get("agent", "staging_dir"), job_key)


# --------------------------------------------------------------------------- #
# Job processing
# --------------------------------------------------------------------------- #

def select_files(backend, sourcetype, earliest, latest, slack, forwarders=None):
    """Files whose (slack-widened) epoch range overlaps [earliest, latest).

    forwarders: set of writer GUIDs to include, or None for all.
    """
    picked = {}
    for day in utc_days(earliest - slack, latest + slack):
        for name, size, handle in backend.list_partition(day, sourcetype):
            if forwarders is not None and parse_writer_guid(name) not in forwarders:
                continue
            lo, hi = parse_file_range(name)
            if hi + slack > earliest and lo - slack < latest:
                picked.setdefault(name, (lo, name, size, handle))
    return [(n, s, h) for _, n, s, h in sorted(picked.values())]


def filter_stream(src, dst, earliest, latest, index_filter, job_id, tag, target_index=None):
    """Copy matching NDJSON lines from src to dst. Returns (kept, skipped, bad).

    Events that already carry promote_job (output of an earlier promotion that was written
    to the dump again) are skipped, so a promotion loop cannot multiply events.
    target_index: send every event to this index instead of its own (index_filter still
    matches the original index)."""
    kept = skipped = bad = 0
    match_all = index_filter in ("", "*")
    for raw in src:
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
            t = float(ev["time"])
        except (ValueError, KeyError, TypeError):
            bad += 1
            continue
        if t < earliest or t >= latest:
            skipped += 1
            continue
        if not match_all and not fnmatch.fnmatchcase(str(ev.get("index", "")), index_filter):
            skipped += 1
            continue
        fields = ev.get("fields")
        if isinstance(fields, dict) and JOB_FIELD in fields:
            skipped += 1
            continue
        if tag or target_index:
            if tag:
                if not isinstance(fields, dict):
                    fields = {}
                    ev["fields"] = fields
                fields[JOB_FIELD] = job_id
            if target_index:
                ev["index"] = target_index
            dst.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        else:
            dst.write(line + b"\n")
        kept += 1
    return kept, skipped, bad


def _num(v, name):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise JobError("invalid %s: %r" % (name, v))


def task_key(job_id, agent_guid):
    return "%s|%s" % (job_id, agent_guid)


def is_targeted(job, served):
    """Does this job need this agent? Same rule the promote_submit macro uses to count
    agents_expected: the agent holds the sourcetype for at least one selected writer."""
    st = str(job.get("sourcetype") or "").strip()
    try:
        fwd = parse_forwarders(job.get("forwarders"))
    except JobError:
        fwd = None  # invalid selection: take the job so the error is reported on a task
    return any(st in sts and (fwd is None or w in fwd) for w, sts in served.items())


def job_stopped(kv, job_key):
    cur = kv.get(JOBS, job_key)
    return cur is None or cur.get("status") in STOP_JOB_STATUSES


class Task:
    """This agent's share of one job (promotion_tasks). Only this agent writes it."""
    STATS = ("files_total", "files_done", "bytes_read", "events_staged",
             "events_skipped", "events_bad")

    def __init__(self, kv, job, agent, min_interval=5):
        self.kv = kv
        self.job_id = job.get("job_id") or job["_key"]
        self.key = task_key(self.job_id, agent["guid"])
        self.min_interval = min_interval
        self.last_save = 0
        self.rec = {"job_id": self.job_id, "agent": agent["guid"], "agent_host": agent["hostname"],
                    "status": "running", "started_at": int(time.time()), "message": "selecting files"}
        self.rec.update({k: 0 for k in self.STATS})
        self.save(force=True)

    def save(self, force=False, **changes):
        self.rec.update(changes)
        now = time.time()
        if force or now - self.last_save >= self.min_interval:
            self.rec["updated_at"] = int(now)
            self.kv.put(TASKS, self.key, self.rec)
            self.last_save = now

    def finish(self, status, message):
        self.save(force=True, status=status, finished_at=int(time.time()), message=message[:1000])


def run_task(cfg, backend, kv, job, task, sink_factory):
    key = job["_key"]
    job_id = task.job_id
    slack = cfg.getint("agent", "file_slack_seconds")
    tag = cfg.getbool("agent", "tag_events")

    sourcetype = str(job.get("sourcetype") or "").strip()
    index_filter = str(job.get("index_filter") or "*").strip()
    target_index = str(job.get("target_index") or "").strip()
    if target_index in ("*", "-"):
        target_index = ""
    forwarders = parse_forwarders(job.get("forwarders"))
    earliest = _num(job.get("earliest"), "earliest")
    latest = _num(job.get("latest"), "latest")
    if not sourcetype or sourcetype == "*" or "/" in sourcetype or ".." in sourcetype:
        raise JobError("invalid sourcetype %r" % sourcetype)
    if earliest <= 0 or latest <= earliest:
        raise JobError("invalid time range %s - %s" % (earliest, latest))
    if target_index and not INDEX_NAME_RE.match(target_index):
        raise JobError("invalid destination index %r" % target_index)

    files = select_files(backend, sourcetype, earliest, latest, slack, forwarders)
    total = sum(s for _, s, _ in files)
    log.info("action=task_selected job=%s sourcetype=%s earliest=%d latest=%d forwarders=%s "
             "target_index=%s files=%d bytes=%d", job_id, sourcetype, earliest, latest,
             "all" if forwarders is None else len(forwarders), target_index or "original",
             len(files), total)
    if not files:
        task.finish("done", "no files in this time range")
        return

    max_bytes = cfg.getint("agent", "max_bytes_per_job")
    if total > max_bytes:
        raise JobError("selected %d bytes exceeds max_bytes_per_job=%d" % (total, max_bytes))

    sink = sink_factory(key)
    try:
        free = sink.free_bytes()
        if free is not None and free - total < cfg.getint("agent", "min_free_bytes"):
            raise JobError("not enough free disk space (free=%d, need=%d)" % (free, total))
        task.save(force=True, files_total=len(files), message="promoting")
        for name, size, handle in files:
            if job_stopped(kv, key):
                raise Cancelled()
            with backend.open(handle, cfg.get("agent", "work_dir")) as src:
                dst = sink.begin(name)
                kept, skipped, bad = filter_stream(src, dst, earliest, latest, index_filter, job_id, tag,
                                                    target_index)
                sink.end(kept)
            r = task.rec
            task.save(files_done=r["files_done"] + 1, bytes_read=r["bytes_read"] + size,
                      events_staged=r["events_staged"] + kept,
                      events_skipped=r["events_skipped"] + skipped,
                      events_bad=r["events_bad"] + bad)
    finally:
        sink.close()

    task.finish("done", sink.done_message(task.rec["events_staged"], task.rec["files_done"]))
    log.info("action=task_done job=%s %s", job_id,
             " ".join("%s=%s" % (k, task.rec[k]) for k in Task.STATS))


def process_jobs(cfg, backend, kv, agent, served, sink_factory):
    jobs = kv.query(JOBS, {"status": {"$in": ACTIVE_JOB_STATUSES}}, sort="submitted_at")
    if not jobs:
        return
    ids = [j.get("job_id") or j["_key"] for j in jobs]
    done = {t.get("job_id") for t in kv.query(TASKS, {"agent": agent["guid"], "job_id": {"$in": ids}})}
    for job in jobs:
        job_id = job.get("job_id") or job["_key"]
        if job_id in done or not is_targeted(job, served):
            continue
        task = Task(kv, job, agent)
        try:
            run_task(cfg, backend, kv, job, task, sink_factory)
        except Cancelled:
            log.info("action=task_cancelled job=%s", job_id)
            task.finish("cancelled", "cancelled; events already handed over will still be indexed")
        except Exception as e:  # noqa: BLE001 - report every failure on the task
            log.exception("action=task_failed job=%s", job_id)
            with contextlib.suppress(Exception):
                task.finish("failed", str(e))


def recover_stale(kv, agent):
    """We hold the lock, so any 'running' task of this agent is orphaned."""
    for t in kv.query(TASKS, {"agent": agent["guid"], "status": "running"}):
        log.warning("action=task_orphaned task=%s", t["_key"])
        t.update(status="failed", finished_at=int(time.time()), updated_at=int(time.time()),
                 message="agent stopped while the task was running; resubmit the job")
        kv.put(TASKS, t["_key"], t)


def purge_old_tasks(kv, agent, days):
    kv.delete(TASKS, {"agent": agent["guid"], "updated_at": {"$lt": int(time.time()) - days * 86400}})


def cleanup_staging(staging_root, min_age=600):
    """Remove empty per-job staging dirs once the stash_hec input has drained them."""
    if not os.path.isdir(staging_root):
        return
    now = time.time()
    for name in os.listdir(staging_root):
        p = os.path.join(staging_root, name)
        try:
            if os.path.isdir(p) and not os.listdir(p) and now - os.path.getmtime(p) > min_age:
                os.rmdir(p)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def setup_logging(level):
    path = os.path.join(os.environ.get("SPLUNK_HOME", "/opt/splunk"),
                        "var", "log", "splunk", "promotion_agent.log")
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=25 * 1024 * 1024,
                                                   backupCount=5)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s pid=%(process)d %(message)s"))
    log.addHandler(handler)
    log.setLevel(getattr(logging, level.upper(), logging.INFO))


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def run_once(cfg, kv, backend, agent, state, sink_factory, now=None):
    """One agent cycle. Returns the updated state."""
    now = now or time.time()
    recover_stale(kv, agent)
    if now - state.get("catalog_at", 0) >= cfg.getint("agent", "catalog_interval") or "served" not in state:
        try:
            sample_files = cfg.getint("agent", "sample_files")
            served, totals, newest = publish_catalog(kv, backend, agent, sample_files)
            state.update(catalog_at=int(now), served=served)
            heartbeat(kv, cfg, agent, totals)
            state["heartbeat_at"] = int(now)
            purge_old_tasks(kv, agent, cfg.getint("agent", "task_retention_days"))
            if sample_files > 0:
                try:
                    state["field_samples"] = publish_fields(
                        kv, cfg, backend, agent, newest, state.get("field_samples") or {}, now)
                except Exception:  # noqa: BLE001 - sampling must never block promotions
                    log.exception("action=fields_failed")
        except Exception:  # noqa: BLE001
            log.exception("action=catalog_failed")
    elif now - state.get("heartbeat_at", 0) >= cfg.getint("agent", "heartbeat_interval"):
        try:
            heartbeat(kv, cfg, agent)
            state["heartbeat_at"] = int(now)
        except Exception:  # noqa: BLE001 - registration must never block promotions
            log.exception("action=heartbeat_failed")
    process_jobs(cfg, backend, kv, agent, state.get("served") or {}, sink_factory)
    return state


def main():
    session_key = sys.stdin.readline().strip()
    cfg = Config()
    setup_logging(cfg.get("agent", "log_level"))

    work_root = cfg.get("agent", "work_dir")
    staging_root = cfg.get("agent", "staging_dir")
    os.makedirs(work_root, exist_ok=True)
    os.makedirs(staging_root, exist_ok=True)

    lock = open(os.path.join(work_root, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0

    try:
        agent = whoami(cfg, session_key)
        uri, auth, verify = control_target(cfg, session_key)
        kv = KVStore(uri, cfg.get("control", "app"), auth, verify=verify,
                     ca_file=cfg.get("control", "ca_file"),
                     timeout=cfg.getint("control", "request_timeout"))
        backend = make_backend(cfg)
        state_path = os.path.join(work_root, ".state.json")
        state = run_once(cfg, kv, backend, agent, load_state(state_path),
                         lambda job_key: make_sink(cfg, job_key))
        save_state(state_path, state)
        cleanup_staging(staging_root)
    except Exception:  # noqa: BLE001
        log.exception("action=run_failed")
        return 1
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
