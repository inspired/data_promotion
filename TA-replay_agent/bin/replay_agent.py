#!/usr/bin/env python3
"""
Data replay agent for a dedicated replay Heavy Forwarder.

Runs as a Splunk scripted input (passAuth = splunk-system-user). Each run:
  1. Takes a non-blocking lock so runs never overlap.
  2. Publishes a catalog of available (sourcetype, day) partitions to the
     Search Head KV store (every `catalog_interval` seconds).
  3. Pulls pending jobs from the SH KV store (replay_jobs) and, per job:
       - selects Ingest Actions output files by day directory + filename epochs
       - streams them, keeping only events whose "time" is inside
         [earliest, latest) (and optionally matching an index filter)
       - tags each event with the indexed field replay_job=<job id>
       - writes the result to a work dir, then atomically renames it into the
         staging dir that a [batch://] input with INDEXED_EXTRACTIONS = HEC reads.
  4. Writes progress/status back to the job record.

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

APP_NAME = "TA-replay_agent"
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOBS = "replay_jobs"
CATALOG = "replay_catalog"

# events_<epoch>_<epoch>_<n>_<seq>_<guid>.ndjson[.gz]
# Observed order is <latest>_<earliest>; we take min/max so order does not matter.
FILE_RE = re.compile(r"^events_(\d{9,11})_(\d{9,11})_.*\.ndjson(\.gz)?$")
S3_KEY_RE = re.compile(
    r"(?:^|/)year=(\d{4})/month=(\d{1,2})/day=(\d{1,2})/sourcetype=([^/]+)/(events_[^/]+)$"
)
DIR_RE = {
    "year": re.compile(r"^year=(\d{4})$"),
    "month": re.compile(r"^month=(\d{1,2})$"),
    "day": re.compile(r"^day=(\d{1,2})$"),
    "sourcetype": re.compile(r"^sourcetype=(.+)$"),
}

log = logging.getLogger("replay_agent")


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
        "work_dir": "$SPLUNK_HOME/var/replay/work",
        "staging_dir": "$SPLUNK_HOME/var/replay/staging",
        "file_slack_seconds": "3600",
        "max_bytes_per_job": "53687091200",
        "min_free_bytes": "10737418240",
        "catalog_interval": "900",
        "tag_events": "true",
        "log_level": "INFO",
    },
    "control": {
        "sh_uri": "",
        "app": "replay_control",
        "verify_ssl": "true",
        "ca_file": "",
        "local_uri": "https://127.0.0.1:8089",
        "credential_realm": "replay_agent",
        "credential_name": "sh_token",
        "request_timeout": "60",
    },
}


class Config:
    def __init__(self, app_dir=APP_DIR, overrides=None):
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_dict(DEFAULTS)
        cp.read([
            os.path.join(app_dir, "default", "replay_agent.conf"),
            os.path.join(app_dir, "local", "replay_agent.conf"),
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


def get_sh_token(cfg, session_key):
    """Read the Search Head auth token from this HF's storage/passwords."""
    realm = cfg.get("control", "credential_realm")
    name = cfg.get("control", "credential_name")
    entity = urllib.parse.quote("%s:%s:" % (realm, name), safe="")
    url = "%s/servicesNS/nobody/%s/storage/passwords/%s?output_mode=json" % (
        cfg.get("control", "local_uri").rstrip("/"), APP_NAME, entity)
    # Local management port normally uses the default self-signed cert.
    ctx = ssl._create_unverified_context()
    res = _http("GET", url, {"Authorization": "Splunk %s" % session_key},
                timeout=30, ctx=ctx)
    return res["entry"][0]["content"]["clear_password"]


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def control_target(cfg, session_key, token_reader=None):
    """Return (uri, auth_header, verify_ssl) for the KV store holding replay_control.

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
        rec = {k: v for k, v in record.items() if k != "_user"}
        return self._do("POST", self._url(coll, key), rec)

    def batch_save(self, coll, records, chunk=500):
        for i in range(0, len(records), chunk):
            self._do("POST", self._url(coll, suffix="batch_save"), records[i:i + chunk])

    def delete(self, coll, query):
        return self._do("DELETE", self._url(coll, params={"query": json.dumps(query)}))


# --------------------------------------------------------------------------- #
# Storage backends
# --------------------------------------------------------------------------- #

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

    def walk(self):
        """Yield (sourcetype, date, name, size) for every file."""
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
                                yield st, date, name, os.path.getsize(full)


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
            yield st, date, name, int(obj.get("Size", 0))


def make_backend(cfg):
    kind = cfg.get("agent", "backend").lower()
    if kind == "filesystem":
        return FilesystemBackend(cfg.get("agent", "fs_root"))
    if kind == "s3":
        return S3Backend(cfg.get("agent", "s3_uri"), cfg.get("agent", "aws_cli"))
    raise ValueError("unknown backend %r" % kind)


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #

def build_catalog(backend, now=None):
    now = int(now or time.time())
    rows = {}
    for st, date, name, size in backend.walk():
        lo, hi = parse_file_range(name)
        key = "%s|%s" % (st, date.isoformat())
        r = rows.setdefault(key, {
            "_key": key, "sourcetype": st, "day": date.isoformat(),
            "files": 0, "bytes": 0, "earliest": lo, "latest": hi,
            "backend": backend.name, "updated_at": now,
        })
        r["files"] += 1
        r["bytes"] += size
        r["earliest"] = min(r["earliest"], lo)
        r["latest"] = max(r["latest"], hi)
    return list(rows.values())


def publish_catalog(kv, backend):
    now = int(time.time())
    rows = build_catalog(backend, now)
    kv.batch_save(CATALOG, rows)
    kv.delete(CATALOG, {"updated_at": {"$lt": now}})
    log.info("action=catalog_published partitions=%d", len(rows))


# --------------------------------------------------------------------------- #
# Job processing
# --------------------------------------------------------------------------- #

def select_files(backend, sourcetype, earliest, latest, slack):
    """Files whose (slack-widened) epoch range overlaps [earliest, latest)."""
    picked = {}
    for day in utc_days(earliest - slack, latest + slack):
        for name, size, handle in backend.list_partition(day, sourcetype):
            lo, hi = parse_file_range(name)
            if hi + slack > earliest and lo - slack < latest:
                picked.setdefault(name, (lo, name, size, handle))
    return [(n, s, h) for _, n, s, h in sorted(picked.values())]


def filter_stream(src, dst, earliest, latest, index_filter, job_id, tag):
    """Copy matching NDJSON lines from src to dst. Returns (kept, skipped, bad)."""
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
        if tag:
            fields = ev.get("fields")
            if not isinstance(fields, dict):
                fields = {}
                ev["fields"] = fields
            fields["replay_job"] = job_id
            dst.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            dst.write(b"\n")
        else:
            dst.write(line)
            dst.write(b"\n")
        kept += 1
    return kept, skipped, bad


def _num(v, name):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise JobError("invalid %s: %r" % (name, v))


def update_job(kv, key, **changes):
    cur = kv.get(JOBS, key)
    if cur is None:
        raise JobError("job record disappeared")
    # Claiming a job: it must still be pending (it may have been cancelled meanwhile).
    if changes.get("status") == "running" and cur.get("status") != "pending":
        raise Cancelled()
    cur.update(changes)
    kv.put(JOBS, key, cur)
    return cur


def run_job(cfg, backend, kv, job):
    key = job["_key"]
    job_id = job.get("job_id") or key
    work_root = cfg.get("agent", "work_dir")
    staging_root = cfg.get("agent", "staging_dir")
    slack = cfg.getint("agent", "file_slack_seconds")
    tag = cfg.getbool("agent", "tag_events")

    sourcetype = str(job.get("sourcetype") or "").strip()
    index_filter = str(job.get("index_filter") or "*").strip()
    earliest = _num(job.get("earliest"), "earliest")
    latest = _num(job.get("latest"), "latest")
    if not sourcetype or sourcetype == "*" or "/" in sourcetype or ".." in sourcetype:
        raise JobError("invalid sourcetype %r" % sourcetype)
    if earliest <= 0 or latest <= earliest:
        raise JobError("invalid time range %s - %s" % (earliest, latest))

    update_job(kv, key, status="running", started_at=int(time.time()),
               agent_host=socket.gethostname(), message="selecting files")
    files = select_files(backend, sourcetype, earliest, latest, slack)
    total = sum(s for _, s, _ in files)
    log.info("action=job_selected job=%s sourcetype=%s earliest=%d latest=%d files=%d bytes=%d",
             job_id, sourcetype, earliest, latest, len(files), total)
    if not files:
        update_job(kv, key, status="staged", finished_at=int(time.time()),
                   files_total=0, files_done=0, events_staged=0,
                   message="no files found for this sourcetype and time range")
        return

    max_bytes = cfg.getint("agent", "max_bytes_per_job")
    if total > max_bytes:
        raise JobError("selected %d bytes exceeds max_bytes_per_job=%d" % (total, max_bytes))

    work = os.path.join(work_root, key)
    staging = os.path.join(staging_root, key)
    os.makedirs(work, exist_ok=True)
    os.makedirs(staging, exist_ok=True)
    free = shutil.disk_usage(work).free
    if free - total < cfg.getint("agent", "min_free_bytes"):
        raise JobError("not enough free space in %s (free=%d, need=%d)" % (work, free, total))

    stats = {"files_total": len(files), "files_done": 0, "bytes_read": 0,
             "events_staged": 0, "events_skipped": 0, "events_bad": 0}
    update_job(kv, key, message="staging", **stats)
    try:
        for name, size, handle in files:
            cur = kv.get(JOBS, key)
            if cur is None or cur.get("status") == "cancel_requested":
                raise Cancelled()
            out_name = name[:-3] if name.endswith(".gz") else name
            tmp = os.path.join(work, out_name)
            with backend.open(handle, work) as src, open(tmp, "wb") as dst:
                kept, skipped, bad = filter_stream(
                    src, dst, earliest, latest, index_filter, job_id, tag)
            if kept:
                # Same filesystem -> atomic; the batch input never sees partial files.
                os.replace(tmp, os.path.join(staging, out_name))
            else:
                os.remove(tmp)
            stats["files_done"] += 1
            stats["bytes_read"] += size
            stats["events_staged"] += kept
            stats["events_skipped"] += skipped
            stats["events_bad"] += bad
            update_job(kv, key, **stats)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    update_job(kv, key, status="staged", finished_at=int(time.time()),
               message="%d events from %d files handed to the batch input"
               % (stats["events_staged"], stats["files_done"]), **stats)
    log.info("action=job_staged job=%s %s", job_id,
             " ".join("%s=%s" % kv_ for kv_ in stats.items()))


def process_pending(cfg, backend, kv):
    jobs = kv.query(JOBS, {"status": "pending"}, sort="submitted_at")
    for job in jobs:
        key = job["_key"]
        try:
            run_job(cfg, backend, kv, job)
        except Cancelled:
            log.info("action=job_cancelled job=%s", key)
            _safe_update(kv, key, status="cancelled", finished_at=int(time.time()),
                         message="cancelled; files already staged will still be indexed")
        except Exception as e:  # noqa: BLE001 - report every failure on the job
            log.exception("action=job_failed job=%s", key)
            _safe_update(kv, key, status="failed", finished_at=int(time.time()),
                         message=str(e)[:1000])


def _safe_update(kv, key, **changes):
    try:
        cur = kv.get(JOBS, key)
        if cur is not None:
            cur.update(changes)
            kv.put(JOBS, key, cur)
    except Exception:  # noqa: BLE001
        log.exception("action=status_update_failed job=%s", key)


def recover_stale(kv):
    """We hold the lock, so any 'running' job owned by this host is orphaned."""
    host = socket.gethostname()
    for job in kv.query(JOBS, {"status": {"$in": ["running", "cancel_requested"]},
                               "agent_host": host}):
        log.warning("action=job_orphaned job=%s", job["_key"])
        _safe_update(kv, job["_key"], status="failed", finished_at=int(time.time()),
                     message="agent stopped while the job was running; resubmit")


def cleanup_staging(staging_root, min_age=600):
    """Remove empty per-job staging dirs once the batch input has drained them."""
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
                        "var", "log", "splunk", "replay_agent.log")
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=25 * 1024 * 1024,
                                                   backupCount=5)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s pid=%(process)d %(message)s"))
    log.addHandler(handler)
    log.setLevel(getattr(logging, level.upper(), logging.INFO))


def catalog_due(state_path, interval):
    try:
        with open(state_path) as f:
            last = json.load(f).get("catalog_at", 0)
    except (OSError, ValueError):
        last = 0
    return time.time() - last >= interval


def mark_catalog(state_path):
    with open(state_path, "w") as f:
        json.dump({"catalog_at": int(time.time())}, f)


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
        uri, auth, verify = control_target(cfg, session_key)
        kv = KVStore(uri, cfg.get("control", "app"), auth, verify=verify,
                     ca_file=cfg.get("control", "ca_file"),
                     timeout=cfg.getint("control", "request_timeout"))
        backend = make_backend(cfg)

        recover_stale(kv)
        state = os.path.join(work_root, ".state.json")
        if catalog_due(state, cfg.getint("agent", "catalog_interval")):
            try:
                publish_catalog(kv, backend)
                mark_catalog(state)
            except Exception:  # noqa: BLE001
                log.exception("action=catalog_failed")
        process_pending(cfg, backend, kv)
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
