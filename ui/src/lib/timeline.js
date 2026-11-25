/**
 * Pure helpers for the promotion timeline: what data exists (catalog rows per UTC day),
 * where earlier promotions were, scales, ticks and the UTC text format used by the inputs.
 * All times are epoch seconds, UTC.
 */

export const HOUR = 3600;
export const DAY = 86400;

const num = (v) => {
    const n = typeof v === 'number' ? v : parseFloat(v);
    return Number.isFinite(n) ? n : null;
};

export const floorTo = (t, step) => Math.floor(t / step) * step;
export const ceilTo = (t, step) => Math.ceil(t / step) * step;

/** Catalog rows of one sourcetype -> one segment per UTC day: first to last hour with files. */
export function dataSegments(rows, sourcetype) {
    return rows
        .filter((r) => r.sourcetype === sourcetype && num(r.earliest) !== null && num(r.latest) !== null)
        .map((r) => ({
            day: r.day,
            start: floorTo(num(r.earliest), HOUR),
            end: ceilTo(num(r.latest), HOUR),
            files: num(r.files) || 0,
            bytes: num(r.bytes) || 0,
            forwarders: num(r.forwarders) || 0,
        }))
        .sort((a, b) => a.start - b.start);
}

/**
 * Earlier jobs of one sourcetype as segments, each on the first lane where it does not
 * overlap another, so overlapping promotions are stacked rather than hidden.
 */
export function promotionSegments(jobs, sourcetype) {
    const segs = jobs
        .filter((j) => j.sourcetype === sourcetype)
        .map((j) => ({
            key: j._key || j.job_id,
            jobId: j.job_id,
            status: j.status,
            forwarders: j.forwarders,
            start: num(j.earliest),
            end: num(j.latest),
        }))
        .filter((s) => s.start !== null && s.end !== null && s.end > s.start)
        .sort((a, b) => a.start - b.start || a.end - b.end);
    const laneEnds = [];
    segs.forEach((s) => {
        let lane = laneEnds.findIndex((end) => end <= s.start);
        if (lane === -1) {
            lane = laneEnds.length;
            laneEnds.push(s.end);
        } else {
            laneEnds[lane] = s.end;
        }
        s.lane = lane;
    });
    return segs;
}

/** Full UTC days covering the data and the promotions; the last 7 days when there is neither. */
export function domainOf(data, promotions, now = Date.now() / 1000) {
    const all = [...data, ...promotions];
    if (!all.length) return [floorTo(now, DAY) - 6 * DAY, ceilTo(now, DAY)];
    const lo = Math.min(...all.map((s) => s.start));
    const hi = Math.max(...all.map((s) => s.end));
    const start = floorTo(lo, DAY);
    return [start, Math.max(ceilTo(hi, DAY), start + DAY)];
}

/** A view around a selection, padded by a quarter on each side, never smaller than 6 hours. */
export function zoomTo(sel, domain) {
    const span = Math.max(sel.end - sel.start, 6 * HOUR);
    const pad = span / 4;
    const start = Math.max(domain[0], floorTo(sel.start - pad, HOUR));
    const end = Math.min(domain[1], ceilTo(sel.start + span + pad, HOUR));
    return [start, Math.max(end, start + HOUR)];
}

export function makeScale(view, width) {
    const [d0, d1] = view;
    const k = width / (d1 - d0);
    return {
        x: (t) => (t - d0) * k,
        t: (x) => d0 + x / k,
        secondsPerPixel: 1 / k,
    };
}

const TICK_STEPS = [HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 28 * DAY];

/** Axis ticks at UTC hour/day boundaries, spaced at least minPx apart. */
export function ticks(view, width, minPx = 80) {
    const [d0, d1] = view;
    const pxPerSec = width / (d1 - d0);
    const step = TICK_STEPS.find((s) => s * pxPerSec >= minPx) || TICK_STEPS[TICK_STEPS.length - 1];
    const out = [];
    for (let t = ceilTo(d0, step); t <= d1; t += step) {
        const iso = new Date(t * 1000).toISOString();
        const label = step < DAY && t % DAY !== 0 ? `${iso.slice(11, 13)}:00` : iso.slice(5, 10);
        out.push({ t, label, major: t % DAY === 0 });
    }
    return out;
}

/** Snap to whole UTC hours. */
export const snap = (t) => Math.round(t / HOUR) * HOUR;

/** Normalize a dragged range: ordered, snapped, at least one hour, inside the domain. */
export function normalizeSelection(a, b, domain) {
    let start = snap(Math.min(a, b));
    let end = snap(Math.max(a, b));
    if (end - start < HOUR) end = start + HOUR;
    start = Math.max(domain[0], start);
    end = Math.min(domain[1], end);
    if (end <= start) start = end - HOUR;
    return { start, end };
}

/** Move a selection without changing its length, kept inside the domain. */
export function shiftSelection(sel, delta, domain) {
    const len = sel.end - sel.start;
    const start = Math.min(Math.max(domain[0], sel.start + delta), domain[1] - len);
    return { start, end: start + len };
}

/** Earlier promotions that overlap a selection (their events would be indexed again). */
export function overlapping(sel, promotions) {
    if (!sel) return [];
    return promotions.filter((r) => r.start < sel.end && r.end > sel.start);
}

/** Data segments that overlap a selection, with files/bytes summed. */
export function coverage(sel, data) {
    const hit = sel ? data.filter((d) => d.start < sel.end && d.end > sel.start) : [];
    return {
        days: hit.length,
        files: hit.reduce((s, d) => s + d.files, 0),
        bytes: hit.reduce((s, d) => s + d.bytes, 0),
    };
}

/** Epoch -> "YYYY-MM-DD HH:MM" (UTC), the format of the start/end inputs. */
export function formatUtc(t) {
    return new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 16);
}

/** "YYYY-MM-DD HH:MM" or "YYYY-MM-DD" (UTC) -> epoch, or null when invalid. */
export function parseUtc(text) {
    const m = String(text || '').trim().match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?$/);
    if (!m) return null;
    const [, y, mo, d, h = '00', mi = '00'] = m;
    const t = Date.UTC(+y, +mo - 1, +d, +h, +mi) / 1000;
    const back = new Date(t * 1000);
    if (back.getUTCMonth() !== +mo - 1 || back.getUTCDate() !== +d || +h > 23 || +mi > 59) return null;
    return t;
}

export function formatSpan(seconds) {
    const h = Math.round(seconds / HOUR);
    if (h < 48) return `${h} hour${h === 1 ? '' : 's'}`;
    const d = Math.floor(h / 24);
    const rest = h % 24;
    return rest ? `${d} days ${rest} h` : `${d} days`;
}
