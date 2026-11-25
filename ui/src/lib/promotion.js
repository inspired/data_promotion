/**
 * Pure helpers for the Data Promotion page: validation, search links, status metadata,
 * and formatting. No Splunk or DOM dependencies, so they are unit-tested directly.
 */

const num = (v) => {
    const n = typeof v === 'number' ? v : parseFloat(v);
    return Number.isFinite(n) ? n : null;
};

// Same rules as the API (promotion_api.py), so the form can explain problems as you type.
const SOURCETYPE_RE = /^[A-Za-z0-9_.:\-]+$/;
const INDEX_FILTER_RE = /^[A-Za-z0-9_*\-]+$/;
// Splunk index names: lowercase letters, digits, _ and -, not starting with - (max 80).
const INDEX_NAME_RE = /^[a-z0-9_][a-z0-9_-]{0,79}$/;
const JOB_ID_RE = /^[A-Za-z0-9_\-]+$/;
const GUID_RE = /^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$/;

export function isValidGuid(g) {
    return typeof g === 'string' && GUID_RE.test(g.toUpperCase());
}

export function validateSourcetype(st) {
    if (!st) return 'Pick a sourcetype.';
    if (!SOURCETYPE_RE.test(st) || st.includes('..')) return 'Sourcetype contains unsupported characters.';
    return null;
}

export function validateIndexFilter(idx) {
    if (!idx) return null;
    if (!INDEX_FILTER_RE.test(idx)) return 'Use letters, digits, _, - and * only.';
    return null;
}

/** Destination index: empty means each event's original index. */
export function validateTargetIndex(idx) {
    const v = (idx || '').trim();
    if (!v) return null;
    if (!INDEX_NAME_RE.test(v)) return 'Use lowercase letters, digits, _ and - only (not starting with -).';
    return null;
}

export function validateTimeRange(earliest, latest) {
    if (earliest === null || earliest === undefined || earliest === '' || earliest === '0') {
        return 'Pick a bounded time range (not All time).';
    }
    if (String(earliest).startsWith('rt') || String(latest).startsWith('rt')) {
        return 'Real-time ranges are not supported.';
    }
    return null;
}

// Ranges longer than this, sent to their original index, get a warning: old events mixed
// into live hot buckets make those buckets span weeks.
export const LONG_RANGE_SECONDS = 3 * 86400;

const UNIT_SECONDS = {
    s: 1, sec: 1, secs: 1, second: 1, seconds: 1,
    m: 60, min: 60, mins: 60, minute: 60, minutes: 60,
    h: 3600, hr: 3600, hrs: 3600, hour: 3600, hours: 3600,
    d: 86400, day: 86400, days: 86400,
    w: 604800, week: 604800, weeks: 604800,
    mon: 2592000, month: 2592000, months: 2592000,
    q: 7776000, qtr: 7776000, qtrs: 7776000, quarter: 7776000, quarters: 7776000,
    y: 31536000, yr: 31536000, yrs: 31536000, year: 31536000, years: 31536000,
};
const RELATIVE_RE = /^([+-]\d*)([a-z]+)(@.*)?$/;

/**
 * A Splunk time (epoch, "now", "@d", "-30d@d") as an approximate epoch, or null if it can't
 * be read. Snapping is ignored: good enough to estimate the length of a range.
 */
export function approxTime(v, now = Date.now() / 1000) {
    const t = String(v === undefined || v === null ? '' : v).trim().toLowerCase();
    if (t === '' || t === 'now' || t.startsWith('@')) return now;
    if (/^\d+(\.\d+)?$/.test(t)) return Number(t);
    const m = RELATIVE_RE.exec(t);
    if (!m || !UNIT_SECONDS[m[2]]) return null;
    const n = m[1].length > 1 ? Number(m[1].slice(1)) : 1;
    return now + (m[1][0] === '-' ? -1 : 1) * n * UNIT_SECONDS[m[2]];
}

/** Approximate length of [earliest, latest) in seconds, or null if unknown. */
export function rangeSeconds(earliest, latest, now = Date.now() / 1000) {
    const e = approxTime(earliest, now);
    const l = approxTime(latest, now);
    return e === null || l === null ? null : l - e;
}

/** Should the form warn that a long range goes back into live indexes? */
export function isLongRangeToOriginalIndex(seconds, targetIndex) {
    return seconds !== null && seconds > LONG_RANGE_SECONDS && !(targetIndex || '').trim();
}

export function isValidJobId(id) {
    return typeof id === 'string' && JOB_ID_RE.test(id);
}

/** Job 'forwarders' field ("*" or space-separated GUIDs) -> array of GUIDs ([] = all). */
export function parseForwarders(value) {
    if (Array.isArray(value)) return value.map((g) => String(g).toUpperCase());
    const v = String(value || '').trim();
    if (!v || v === '*') return [];
    return v.split(/\s+/).map((g) => g.toUpperCase());
}

/**
 * Search (for the Search app) that shows a job's promoted events: its destination index
 * if it had one, otherwise every index.
 */
export function eventsSearch(job) {
    if (!job || !isValidJobId(job.job_id)) throw new Error('Invalid job id');
    const target = (job.target_index || '').trim();
    const where = target && !validateTargetIndex(target) ? `index=${target}` : '(index=* OR index=_*)';
    return `${where} promote_job="${job.job_id}"`;
}

/** Query parameters for app/search/search that open a job's events over its time range. */
export function searchParams(job) {
    const e = num(job && job.earliest);
    const l = num(job && job.latest);
    const params = { q: `search ${eventsSearch(job)}` };
    if (e !== null && l !== null) {
        params.earliest = String(Math.floor(e));
        params.latest = String(Math.ceil(l) + 1);
    }
    return params;
}

/** Whether a job's events can be looked at in Search (it finished and something was indexed). */
export function canOpenInSearch(job) {
    if (!job || !isValidJobId(job.job_id)) return false;
    if (job.status === 'completed') return true;
    return job.status === 'incomplete' && (num(job.events_indexed) || 0) > 0;
}

/**
 * Job status -> status indicator (icon + text, the Design System table pattern).
 * tone: positive / warning / negative / info (work in progress) / neutral (waiting or
 * withdrawn). Color is never the only signal: every status also has its own icon and label.
 */
export const STATUS = {
    pending: { label: 'Pending', tone: 'neutral', icon: 'waiting', active: true },
    running: { label: 'Running', tone: 'info', icon: 'progress', active: true },
    staged: { label: 'Staged', tone: 'info', icon: 'progress', active: true },
    indexing: { label: 'Indexing', tone: 'info', icon: 'progress', active: true },
    completed: { label: 'Completed', tone: 'positive', icon: 'ok', active: false },
    incomplete: { label: 'Incomplete', tone: 'warning', icon: 'warning', active: false },
    cancel_requested: { label: 'Cancelling', tone: 'warning', icon: 'stopped', active: true },
    cancelled: { label: 'Cancelled', tone: 'neutral', icon: 'stopped', active: false },
    failed: { label: 'Failed', tone: 'negative', icon: 'error', active: false },
};

/** An agent's share of a job (promotion_tasks). */
export const TASK_STATUS = {
    running: { label: 'Running', tone: 'info', icon: 'progress' },
    done: { label: 'Done', tone: 'positive', icon: 'ok' },
    failed: { label: 'Failed', tone: 'negative', icon: 'error' },
    cancelled: { label: 'Cancelled', tone: 'neutral', icon: 'stopped' },
};

/** Forwarder heartbeat state (status in GET forwarders). */
export const AGENT_STATUS = {
    reporting: { label: 'Reporting', tone: 'positive', icon: 'ok' },
    stale: { label: 'Not reporting', tone: 'warning', icon: 'warning' },
    unregistered: { label: 'Not registered', tone: 'neutral', icon: 'unknown' },
};

const UNKNOWN = (status) => ({ label: status || 'Unknown', tone: 'neutral', icon: 'unknown', active: false });

export function statusMeta(status) {
    return STATUS[status] || UNKNOWN(status);
}

export function taskStatusMeta(status) {
    return TASK_STATUS[status] || UNKNOWN(status);
}

export function agentStatusMeta(status) {
    return AGENT_STATUS[status] || AGENT_STATUS.unregistered;
}

export function canCancel(job) {
    return job && (job.status === 'pending' || job.status === 'running');
}

/**
 * Progress for the standard Progress bar, in the unit of the current phase:
 * files staged while the agent runs, events indexed afterwards.
 */
export function progressOf(job) {
    if (!job) return null;
    const s = job.status;
    const filesTotal = num(job.files_total);
    const staged = num(job.events_staged);
    if (s === 'pending') return { percentage: 0, label: 'Waiting for forwarders' };
    if (s === 'running' || s === 'cancel_requested') {
        const done = num(job.files_done) || 0;
        const pct = filesTotal ? Math.round((done / filesTotal) * 100) : 0;
        return { percentage: pct, label: `${formatCount(done)} of ${formatCount(filesTotal || 0)} files promoted` };
    }
    if (s === 'staged' || s === 'indexing' || s === 'incomplete' || s === 'completed') {
        const indexed = num(job.events_indexed) || 0;
        if (!staged) return { percentage: 100, label: 'No events to index' };
        const pct = Math.min(100, Math.round((indexed / staged) * 100));
        return { percentage: pct, label: `${formatCount(indexed)} of ${formatCount(staged)} events indexed` };
    }
    return null;
}

/** Page of rows for a Paginator (1-based page). */
export function paginate(rows, page, pageSize) {
    const totalPages = Math.max(1, Math.ceil(rows.length / pageSize));
    const current = Math.min(Math.max(1, page), totalPages);
    return { current, totalPages, rows: rows.slice((current - 1) * pageSize, current * pageSize) };
}

export function formatBytes(v) {
    const n = num(v);
    if (n === null) return '–';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let x = n;
    while (x >= 1024 && i < units.length - 1) {
        x /= 1024;
        i += 1;
    }
    return `${x.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatCount(v) {
    const n = num(v);
    return n === null ? '–' : Math.round(n).toLocaleString('en-US');
}

/** Epoch seconds -> "YYYY-MM-DD HH:MM:SS" in UTC (dump partitions are UTC). */
export function formatEpoch(v) {
    const n = num(v);
    if (n === null || n <= 0) return '–';
    return new Date(n * 1000).toISOString().replace('T', ' ').slice(0, 19);
}

/** Epoch seconds -> "YYYY-MM-DD HH:MM" in UTC, for compact table cells. */
export function formatEpochShort(v) {
    const full = formatEpoch(v);
    return full === '–' ? full : full.slice(0, 16);
}

export function formatRangeShort(earliest, latest) {
    return `${formatEpochShort(earliest)} – ${formatEpochShort(latest)}`;
}

export function formatRange(earliest, latest) {
    return `${formatEpoch(earliest)} → ${formatEpoch(latest)} UTC`;
}

export function formatDuration(seconds) {
    const n = num(seconds);
    if (n === null || n < 0) return '–';
    if (n < 60) return `${Math.round(n)}s`;
    if (n < 3600) return `${Math.floor(n / 60)}m ${Math.round(n % 60)}s`;
    return `${Math.floor(n / 3600)}h ${Math.floor((n % 3600) / 60)}m`;
}

/** Sort jobs newest first. */
export function sortJobs(jobs) {
    return [...jobs].sort((a, b) => (num(b.submitted_at) || 0) - (num(a.submitted_at) || 0));
}

/** Aggregate catalog rows (one per sourcetype+day) into one summary per sourcetype. */
export function summarizeCatalog(rows) {
    const by = {};
    rows.forEach((r) => {
        const st = r.sourcetype;
        if (!st) return;
        const s = by[st] || (by[st] = {
            sourcetype: st, days: 0, files: 0, bytes: 0, firstDay: r.day, lastDay: r.day,
            earliest: num(r.earliest), latest: num(r.latest),
        });
        s.days += 1;
        s.files += num(r.files) || 0;
        s.bytes += num(r.bytes) || 0;
        if (r.day < s.firstDay) s.firstDay = r.day;
        if (r.day > s.lastDay) s.lastDay = r.day;
        const e = num(r.earliest);
        const l = num(r.latest);
        if (e !== null && (s.earliest === null || e < s.earliest)) s.earliest = e;
        if (l !== null && (s.latest === null || l > s.latest)) s.latest = l;
    });
    return Object.values(by).sort((a, b) => a.sourcetype.localeCompare(b.sourcetype));
}

const upper = (g) => String(g || '').toUpperCase();
const asList = (v) => (Array.isArray(v) ? v : [v].filter(Boolean));

/** Jobs that included one forwarder: all-forwarder jobs and jobs that listed its GUID. */
export function jobsForForwarder(jobs, guid) {
    const g = upper(guid);
    return jobs.filter((j) => {
        const sel = parseForwarders(j.forwarders);
        return !sel.length || sel.includes(g);
    });
}

export function forwardersWithSourcetype(forwarders, sourcetype) {
    if (!sourcetype) return forwarders;
    return forwarders.filter((f) => f.sourcetypes.includes(sourcetype));
}

/**
 * Options for a forwarder picker that may hold thousands of entries: keyword match on
 * hostname or GUID, capped at `limit`, with the selected ones always included.
 */
export function filterForwarders(forwarders, keyword, selected, limit = 100) {
    const k = (keyword || '').trim().toLowerCase();
    const sel = new Set(selected);
    const matches = k
        ? forwarders.filter((f) => f.label.toLowerCase().includes(k) || f.guid.toLowerCase().includes(k))
        : forwarders;
    const shown = matches.slice(0, limit);
    const shownSet = new Set(shown.map((f) => f.guid));
    const extra = forwarders.filter((f) => sel.has(f.guid) && !shownSet.has(f.guid));
    return { options: [...extra, ...shown], total: matches.length, truncated: matches.length > limit };
}

/** Splunk theme name / preference -> 'light' | 'dark' ('system' follows the OS). */
export function resolveColorScheme(theme, prefersDark = () => (
    typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
)) {
    const t = String(theme || '').toLowerCase();
    if (t === 'dark' || t === 'enterprisedark' || t === 'prismadark') return 'dark';
    if (t === 'system' || t === 'auto') return prefersDark() ? 'dark' : 'light';
    return 'light';
}

/** Short description of a job's forwarder selection. */
export function describeForwarders(job, nameByGuid = {}) {
    const guids = parseForwarders(job && job.forwarders);
    if (!guids.length) return 'All forwarders';
    if (guids.length === 1) return nameByGuid[guids[0]] || guids[0];
    return `${guids.length} forwarders`;
}

/**
 * Merge the promotion_fields rows of one sourcetype (one row per agent) into
 * { indexes: [{ name, agents }], fields: [{ name, agents }], agents, events, sampledAt }.
 * Values are ranked by how many agents saw them, then by name. null when nothing was sampled.
 */
export function mergeFieldSamples(rows) {
    const sampled = (rows || []).filter((r) => (num(r.events_sampled) || 0) > 0);
    if (!sampled.length) return null;
    const tally = (key) => {
        const m = new Map();
        sampled.forEach((r) => new Set(asList(r[key]).map(String)).forEach((v) => m.set(v, (m.get(v) || 0) + 1)));
        return [...m].map(([name, agents]) => ({ name, agents }))
            .sort((a, b) => b.agents - a.agents || a.name.localeCompare(b.name));
    };
    return {
        indexes: tally('indexes'),
        fields: tally('indexed_fields'),
        agents: sampled.length,
        events: sampled.reduce((t, r) => t + (num(r.events_sampled) || 0), 0),
        sampledAt: Math.max(...sampled.map((r) => num(r.sampled_at) || 0)),
    };
}

/** Index filter suggestions: indexes from the sample that match what was typed (* wildcards). */
export function indexOptions(indexes, typed, limit = 100) {
    const t = (typed || '').trim();
    let match = () => true;
    if (t && t !== '*') {
        const re = new RegExp(`^${t.split('*').map((p) => p.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*')}`, 'i');
        match = (name) => re.test(name);
    }
    return (indexes || []).filter((i) => match(i.name)).slice(0, limit);
}
