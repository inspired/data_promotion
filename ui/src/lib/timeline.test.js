import {
    DAY, HOUR, coverage, dataSegments, domainOf, formatSpan, formatUtc, normalizeSelection, overlapping,
    parseUtc, replaySegments, shiftSelection, ticks, zoomTo,
} from './timeline';

const D0 = Date.UTC(2026, 10, 19) / 1000; // 2026-11-19 00:00 UTC

const rows = [
    { sourcetype: 'splunkd', day: '2026-11-20', files: '12', bytes: '100', earliest: String(D0 + DAY + 1800), latest: String(D0 + DAY + 5 * HOUR), forwarders: '2' },
    { sourcetype: 'splunkd', day: '2026-11-19', files: '24', bytes: '200', earliest: String(D0), latest: String(D0 + 23 * HOUR) },
    { sourcetype: 'syslog', day: '2026-11-19', files: '1', bytes: '1', earliest: String(D0), latest: String(D0 + HOUR) },
];

const jobs = [
    { job_id: 'a', sourcetype: 'splunkd', status: 'completed', earliest: D0, latest: D0 + 10 * HOUR },
    { job_id: 'b', sourcetype: 'splunkd', status: 'running', earliest: String(D0 + 5 * HOUR), latest: String(D0 + 12 * HOUR) },
    { job_id: 'c', sourcetype: 'splunkd', status: 'failed', earliest: D0 + 10 * HOUR, latest: D0 + 11 * HOUR },
    { job_id: 'd', sourcetype: 'splunkd', status: 'rejected', earliest: D0, latest: D0 + HOUR },
    { job_id: 'e', sourcetype: 'syslog', status: 'completed', earliest: D0, latest: D0 + HOUR },
];

test('dataSegments: one sorted segment per day of the sourcetype, widened to whole hours', () => {
    const segs = dataSegments(rows, 'splunkd');
    expect(segs.map((s) => s.day)).toEqual(['2026-11-19', '2026-11-20']);
    expect(segs[1]).toMatchObject({ start: D0 + DAY, end: D0 + DAY + 5 * HOUR, files: 12, bytes: 100, forwarders: 2 });
});

test('replaySegments: skips rejected and other sourcetypes, stacks overlaps on lanes', () => {
    const segs = replaySegments(jobs, 'splunkd');
    expect(segs.map((s) => [s.jobId, s.lane])).toEqual([['a', 0], ['b', 1], ['c', 0]]);
});

test('domainOf: whole UTC days around data and replays; last 7 days when empty', () => {
    expect(domainOf(dataSegments(rows, 'splunkd'), [])).toEqual([D0, D0 + 2 * DAY]);
    expect(domainOf([], [], D0 + 3 * HOUR)).toEqual([D0 - 6 * DAY, D0 + DAY]);
});

test('normalizeSelection: ordered, snapped to hours, at least an hour, inside the domain', () => {
    const dom = [D0, D0 + 2 * DAY];
    expect(normalizeSelection(D0 + 5 * HOUR + 100, D0 + 2 * HOUR - 100, dom)).toEqual({ start: D0 + 2 * HOUR, end: D0 + 5 * HOUR });
    expect(normalizeSelection(D0 + 100, D0 + 200, dom)).toEqual({ start: D0, end: D0 + HOUR });
    expect(normalizeSelection(D0 - DAY, D0 + 9 * DAY, dom)).toEqual({ start: D0, end: D0 + 2 * DAY });
});

test('shiftSelection keeps the length and stays inside the domain', () => {
    const dom = [D0, D0 + DAY];
    expect(shiftSelection({ start: D0, end: D0 + 2 * HOUR }, 30 * HOUR, dom)).toEqual({ start: D0 + 22 * HOUR, end: D0 + DAY });
    expect(shiftSelection({ start: D0 + HOUR, end: D0 + 2 * HOUR }, -5 * HOUR, dom)).toEqual({ start: D0, end: D0 + HOUR });
});

test('overlapping and coverage', () => {
    const sel = { start: D0 + 11 * HOUR, end: D0 + DAY + 2 * HOUR };
    expect(overlapping(sel, replaySegments(jobs, 'splunkd')).map((r) => r.jobId)).toEqual(['b']);
    expect(coverage(sel, dataSegments(rows, 'splunkd'))).toEqual({ days: 2, files: 36, bytes: 300 });
    expect(overlapping(null, [])).toEqual([]);
});

test('zoomTo pads the selection and stays in the domain', () => {
    const v = zoomTo({ start: D0 + 10 * HOUR, end: D0 + 11 * HOUR }, [D0, D0 + 2 * DAY]);
    expect(v[0]).toBeLessThan(D0 + 10 * HOUR);
    expect(v[1] - v[0]).toBeGreaterThanOrEqual(6 * HOUR);
    expect(zoomTo({ start: D0, end: D0 + DAY }, [D0, D0 + DAY])).toEqual([D0, D0 + DAY]);
});

test('ticks: hour labels when zoomed in, day labels when zoomed out', () => {
    expect(ticks([D0, D0 + 12 * HOUR], 1000)[1].label).toBe('01:00');
    const days = ticks([D0, D0 + 30 * DAY], 1000);
    expect(days[0]).toMatchObject({ label: '11-19', major: true });
    expect(days.every((t) => t.t % DAY === 0)).toBe(true);
});

test('UTC text format round-trips and rejects invalid dates', () => {
    expect(formatUtc(D0 + 14 * HOUR + 30 * 60)).toBe('2026-11-19 14:30');
    expect(parseUtc('2026-11-19 14:30')).toBe(D0 + 14 * HOUR + 30 * 60);
    expect(parseUtc('2026-11-19')).toBe(D0);
    expect(parseUtc('2026-02-30 00:00')).toBeNull();
    expect(parseUtc('2026-11-19 24:00')).toBeNull();
    expect(parseUtc('yesterday')).toBeNull();
});

test('formatSpan', () => {
    expect(formatSpan(HOUR)).toBe('1 hour');
    expect(formatSpan(24 * HOUR)).toBe('24 hours');
    expect(formatSpan(3 * DAY + 5 * HOUR)).toBe('3 days 5 h');
});
