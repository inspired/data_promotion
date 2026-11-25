import {
    buildForwarders,
    canOpenInSearch,
    eventsSearch,
    jobsForForwarder,
    searchParams,
    validateTargetIndex,
    resolveColorScheme,
    cancelledStatus,
    describeForwarders,
    filterForwarders,
    forwardersWithSourcetype,
    paginate,
    parseForwarders,
    canCancel,
    formatBytes,
    indexOptions,
    mergeFieldSamples,
    formatCount,
    formatDuration,
    formatEpoch,
    progressOf,
    sortJobs,
    statusMeta,
    submitSearch,
    summarizeCatalog,
    validateIndexFilter,
    validateSourcetype,
    validateTimeRange,
    verifySearch,
    approxTime,
    rangeSeconds,
    isLongRangeToOriginalIndex,
    LONG_RANGE_SECONDS,
} from './promotion';

const G1 = '5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8';
const G2 = 'BF6301FD-4484-45FA-802E-F657F1AFB5F7';

describe('range length', () => {
    const NOW = 1795068000;
    test('reads epochs and relative times', () => {
        expect(approxTime('1795068000', NOW)).toBe(1795068000);
        expect(approxTime(1795068000.5, NOW)).toBe(1795068000.5);
        expect(approxTime('now', NOW)).toBe(NOW);
        expect(approxTime('@h', NOW)).toBe(NOW);
        expect(approxTime('-24h@h', NOW)).toBe(NOW - 86400);
        expect(approxTime('-30d@d', NOW)).toBe(NOW - 30 * 86400);
        expect(approxTime('-1mon', NOW)).toBe(NOW - 30 * 86400);
        expect(approxTime('-w', NOW)).toBe(NOW - 7 * 86400);
        expect(approxTime('-5parsecs', NOW)).toBeNull();
    });

    test('range length and the long-range warning', () => {
        expect(rangeSeconds('-24h@h', '@h', NOW)).toBe(86400);
        expect(rangeSeconds('-30d@d', 'now', NOW)).toBe(30 * 86400);
        expect(rangeSeconds('garbage', 'now', NOW)).toBeNull();
        expect(isLongRangeToOriginalIndex(LONG_RANGE_SECONDS, '')).toBe(false);
        expect(isLongRangeToOriginalIndex(LONG_RANGE_SECONDS + 1, '')).toBe(true);
        expect(isLongRangeToOriginalIndex(LONG_RANGE_SECONDS + 1, ' ')).toBe(true);
        expect(isLongRangeToOriginalIndex(LONG_RANGE_SECONDS + 1, 'promoted')).toBe(false);
        expect(isLongRangeToOriginalIndex(null, '')).toBe(false);
    });
});

describe('validation and SPL builders', () => {
    test('sourcetype', () => {
        expect(validateSourcetype('splunkd')).toBeNull();
        expect(validateSourcetype('aws:cloudtrail')).toBeNull();
        expect(validateSourcetype('')).toMatch(/Pick/);
        expect(validateSourcetype('a b')).toMatch(/unsupported/);
        expect(validateSourcetype('../etc')).toMatch(/unsupported/);
        expect(validateSourcetype('x)` | delete')).toMatch(/unsupported/);
    });

    test('index filter', () => {
        expect(validateIndexFilter('*')).toBeNull();
        expect(validateIndexFilter('_int*')).toBeNull();
        expect(validateIndexFilter('')).toBeNull();
        expect(validateIndexFilter('a,b')).toMatch(/Use letters/);
        expect(validateIndexFilter('a" OR 1')).toMatch(/Use letters/);
    });

    test('time range', () => {
        expect(validateTimeRange('-24h@h', '@h')).toBeNull();
        expect(validateTimeRange('1795068000', '1795071600')).toBeNull();
        expect(validateTimeRange('0', 'now')).toMatch(/bounded/);
        expect(validateTimeRange('', 'now')).toMatch(/bounded/);
        expect(validateTimeRange(null, 'now')).toMatch(/bounded/);
        expect(validateTimeRange('rt-5m', 'rt')).toMatch(/Real-time/);
    });

    test('submitSearch', () => {
        expect(submitSearch('splunkd', '*')).toBe('| `promote_submit(splunkd, *, *, *)`');
        expect(submitSearch('splunkd', '', [])).toBe('| `promote_submit(splunkd, *, *, *)`');
        expect(submitSearch('splunkd', '*', [G1.toLowerCase(), G2]))
            .toBe(`| \`promote_submit(splunkd, *, ${G1} ${G2}, *)\``);
        expect(submitSearch('splunkd', '*', [G1], ' promotion_test ')).toBe(`| \`promote_submit(splunkd, *, ${G1}, promotion_test)\``);
        expect(() => submitSearch('splunkd', '*', [], 'x) | delete')).toThrow(/Destination index/);
        expect(() => submitSearch('splunkd', '*', ['x) | delete'])).toThrow(/Invalid agent/);
        expect(() => submitSearch('bad st', '*')).toThrow();
        expect(() => submitSearch('splunkd', 'x,y')).toThrow();
    });

    test('destination index', () => {
        expect(validateTargetIndex('')).toBeNull();
        expect(validateTargetIndex('  ')).toBeNull();
        expect(validateTargetIndex('promotion_test-1')).toBeNull();
        expect(validateTargetIndex('_internal')).toBeNull();
        expect(validateTargetIndex('Main')).toMatch(/lowercase/);
        expect(validateTargetIndex('-x')).toMatch(/lowercase/);
        expect(validateTargetIndex('ma*')).toMatch(/lowercase/);
        expect(validateTargetIndex('a'.repeat(81))).toMatch(/lowercase/);
    });

    test('open in search', () => {
        const job = { job_id: '20261123-142538-99255a', status: 'completed', earliest: '1795046400', latest: 1795050000.5 };
        expect(eventsSearch(job)).toBe('(index=* OR index=_*) promote_job="20261123-142538-99255a"');
        expect(eventsSearch({ ...job, target_index: 'promotion_test' })).toBe('index=promotion_test promote_job="20261123-142538-99255a"');
        expect(searchParams(job)).toEqual({
            q: 'search (index=* OR index=_*) promote_job="20261123-142538-99255a"', earliest: '1795046400', latest: '1795050002',
        });
        expect(() => eventsSearch({ job_id: 'x" OR 1' })).toThrow();
        expect(canOpenInSearch(job)).toBe(true);
        expect(canOpenInSearch({ ...job, status: 'running' })).toBe(false);
        expect(canOpenInSearch({ ...job, status: 'incomplete', events_indexed: '0' })).toBe(false);
        expect(canOpenInSearch({ ...job, status: 'incomplete', events_indexed: '12' })).toBe(true);
    });

    test('jobsForForwarder', () => {
        const jobs = [{ job_id: 'a', forwarders: '*' }, { job_id: 'b', forwarders: `${G1} ${G2}` }, { job_id: 'c', forwarders: G2 }];
        expect(jobsForForwarder(jobs, G1.toLowerCase()).map((j) => j.job_id)).toEqual(['a', 'b']);
    });

    test('verifySearch', () => {
        expect(verifySearch('20261123-142538-99255a')).toContain('promote_job="20261123-142538-99255a"');
        expect(() => verifySearch('x" OR index=*')).toThrow();
    });
});

describe('status', () => {
    test('meta falls back for unknown status', () => {
        expect(statusMeta('completed').label).toBe('Completed');
        expect(statusMeta('weird').label).toBe('weird');
    });

    test('cancel rules', () => {
        expect(canCancel({ status: 'pending' })).toBe(true);
        expect(canCancel({ status: 'running' })).toBe(true);
        expect(canCancel({ status: 'staged' })).toBe(false);
        expect(cancelledStatus('pending')).toBe('cancelled');
        expect(cancelledStatus('running')).toBe('cancel_requested');
        expect(cancelledStatus('completed')).toBeNull();
    });

    test('progress uses the unit of the current phase', () => {
        expect(progressOf({ status: 'pending' }).percentage).toBe(0);
        expect(progressOf({ status: 'running', files_total: '4', files_done: '2' }))
            .toEqual({ percentage: 50, label: '2 of 4 files promoted' });
        expect(progressOf({ status: 'staged', events_staged: '100', events_indexed: '0' }).percentage).toBe(0);
        expect(progressOf({ status: 'indexing', events_staged: '100', events_indexed: '50' }))
            .toEqual({ percentage: 50, label: '50 of 100 events indexed' });
        expect(progressOf({ status: 'completed', events_staged: '10', events_indexed: '10' }).percentage).toBe(100);
        expect(progressOf({ status: 'staged', events_staged: '0' }).label).toBe('No events to index');
        expect(progressOf({ status: 'failed' })).toBeNull();
    });
});

describe('theme', () => {
    test('resolveColorScheme', () => {
        expect(resolveColorScheme('dark')).toBe('dark');
        expect(resolveColorScheme('enterpriseDark')).toBe('dark');
        expect(resolveColorScheme('light')).toBe('light');
        expect(resolveColorScheme('enterprise')).toBe('light');
        expect(resolveColorScheme(null)).toBe('light');
        expect(resolveColorScheme('system', () => true)).toBe('dark');
        expect(resolveColorScheme('system', () => false)).toBe('light');
    });
});

describe('formatting', () => {
    test('bytes and counts', () => {
        expect(formatBytes(283505189)).toBe('270.4 MB');
        expect(formatBytes('512')).toBe('512 B');
        expect(formatBytes(undefined)).toBe('–');
        expect(formatCount('844426')).toBe('844,426');
    });

    test('epoch is UTC', () => {
        expect(formatEpoch(1795068000)).toBe('2026-11-19 06:00:00');
        expect(formatEpoch(0)).toBe('–');
    });

    test('duration', () => {
        expect(formatDuration(44)).toBe('44s');
        expect(formatDuration(125)).toBe('2m 5s');
        expect(formatDuration(3725)).toBe('1h 2m');
    });
});

describe('collections', () => {
    test('sortJobs newest first', () => {
        const out = sortJobs([{ _key: 'a', submitted_at: 1 }, { _key: 'b', submitted_at: '3' }, { _key: 'c' }]);
        expect(out.map((j) => j._key)).toEqual(['b', 'a', 'c']);
    });

    test('summarizeCatalog', () => {
        const rows = [
            { sourcetype: 'splunkd', day: '2026-11-19', files: 7, bytes: 100, earliest: 10, latest: 20 },
            { sourcetype: 'splunkd', day: '2026-11-18', files: '3', bytes: '50', earliest: '5', latest: '9' },
            { sourcetype: 'access', day: '2026-11-19', files: 1, bytes: 1, earliest: 1, latest: 2 },
        ];
        const [a, s] = summarizeCatalog(rows);
        expect(a.sourcetype).toBe('access');
        expect(s).toMatchObject({ days: 2, files: 10, bytes: 150, firstDay: '2026-11-18', lastDay: '2026-11-19', earliest: 5, latest: 20 });
    });
});

describe('forwarders', () => {
    const sources = [
        { agent: G1, guid: G1.toLowerCase(), sourcetypes: ['splunkd', 'syslog'], files: 3, bytes: 10, days: 1 },
        { agent: G1, guid: G2, sourcetypes: 'splunkd', files: 1, bytes: 5, days: 1 },
    ];

    test('buildForwarders: name, version and heartbeat from the forwarder\'s own registration', () => {
        const f = buildForwarders(sources, [{ guid: G1, hostname: 'splunk1', agent_version: '1.2.1', last_seen: 1000 }], [], 1100);
        expect(f.map((x) => x.label)).toEqual([G2, 'splunk1']);
        expect(f[1]).toMatchObject({ guid: G1, hostname: 'splunk1', status: 'reporting', version: '1.2.1', sourcetypes: ['splunkd', 'syslog'] });
        expect(f[0]).toMatchObject({ sourcetypes: ['splunkd'], hostname: null, status: 'unregistered' });
        // G2's files are held by agent G1 (shared store); G1 holds its own.
        expect(f.map((x) => x.agents)).toEqual([[G1], [G1]]);
    });

    test('buildForwarders: stale heartbeat, metric-name fallback, registered without data', () => {
        const f = buildForwarders(sources, [{ guid: G1, hostname: 'splunk1', last_seen: 100 }, { guid: 'C0FFEE00-0000-0000-0000-000000000000', hostname: 'empty-hf', last_seen: 100 }],
            [{ guid: G2, hostname: 'hf9' }], 100 + 16 * 60);
        expect(f.find((x) => x.guid === G1).status).toBe('stale');
        expect(f.find((x) => x.guid === G2).hostname).toBe('hf9');
        expect(f.find((x) => x.hostname === 'empty-hf')).toMatchObject({ files: 0, sourcetypes: [] });
    });

    test('forwardersWithSourcetype', () => {
        const f = buildForwarders(sources, []);
        expect(forwardersWithSourcetype(f, 'syslog').map((x) => x.guid)).toEqual([G1]);
        expect(forwardersWithSourcetype(f, '').length).toBe(2);
    });

    test('filterForwarders caps the list and keeps selected', () => {
        const many = Array.from({ length: 500 }, (_, i) => ({
            guid: `00000000-0000-0000-0000-${String(i).padStart(12, '0')}`,
            label: `hf${String(i).padStart(4, '0')}`,
        }));
        const r = filterForwarders(many, '', [many[499].guid], 100);
        expect(r.options).toHaveLength(101);
        expect(r.options[0].guid).toBe(many[499].guid);
        expect(r).toMatchObject({ total: 500, truncated: true });
        const k = filterForwarders(many, 'hf004', [], 100);
        expect(k.options.map((x) => x.label)).toEqual(Array.from({ length: 10 }, (_, i) => `hf004${i}`));
        expect(k.truncated).toBe(false);
    });

    test('parse and describe', () => {
        expect(parseForwarders('*')).toEqual([]);
        expect(parseForwarders(`${G1.toLowerCase()} ${G2}`)).toEqual([G1, G2]);
        expect(describeForwarders({ forwarders: '*' })).toBe('All agents');
        expect(describeForwarders({ forwarders: G1 }, { [G1]: 'splunk1' })).toBe('splunk1');
        expect(describeForwarders({ forwarders: `${G1} ${G2}` })).toBe('2 agents');
    });

    test('paginate clamps the page', () => {
        expect(paginate([1, 2, 3, 4, 5], 2, 2)).toEqual({ current: 2, totalPages: 3, rows: [3, 4] });
        expect(paginate([1, 2, 3], 9, 2)).toEqual({ current: 2, totalPages: 2, rows: [3] });
        expect(paginate([], 1, 25)).toEqual({ current: 1, totalPages: 1, rows: [] });
    });
});

test('every status has a tone, an icon and a label', () => {
    // eslint-disable-next-line global-require
    const { STATUS, TASK_STATUS, AGENT_STATUS, statusMeta, agentStatusMeta } = require('./promotion');
    const tones = new Set(['positive', 'warning', 'negative', 'info', 'neutral']);
    [STATUS, TASK_STATUS, AGENT_STATUS].forEach((map) => Object.values(map).forEach((m) => {
        expect(tones.has(m.tone)).toBe(true);
        expect(m.icon).toBeTruthy();
        expect(m.label).toBeTruthy();
    }));
    expect(statusMeta('weird')).toMatchObject({ label: 'weird', tone: 'neutral' });
    expect(agentStatusMeta(undefined).label).toBe('Not registered');
});

describe('field samples', () => {
    const rows = [
        { agent: 'a1', indexes: ['main', '_internal'], indexed_fields: ['node_guid'], events_sampled: 200, sampled_at: 10 },
        { agent: 'a2', indexes: ['_internal'], indexed_fields: 'node_guid', events_sampled: '50', sampled_at: '20' },
        { agent: 'a3', indexes: ['ignored'], events_sampled: 0, sampled_at: 30 },
    ];

    test('merges agents and ranks by how many saw a value', () => {
        const m = mergeFieldSamples(rows);
        expect(m.indexes).toEqual([{ name: '_internal', agents: 2 }, { name: 'main', agents: 1 }]);
        expect(m.fields).toEqual([{ name: 'node_guid', agents: 2 }]);
        expect([m.agents, m.events, m.sampledAt]).toEqual([2, 250, 20]);
    });

    test('nothing sampled gives null', () => {
        expect(mergeFieldSamples([])).toBeNull();
        expect(mergeFieldSamples([rows[2]])).toBeNull();
    });

    test('index options follow the typed filter', () => {
        const idx = mergeFieldSamples(rows).indexes;
        expect(indexOptions(idx, '*').map((i) => i.name)).toEqual(['_internal', 'main']);
        expect(indexOptions(idx, '').length).toBe(2);
        expect(indexOptions(idx, 'ma').map((i) => i.name)).toEqual(['main']);
        expect(indexOptions(idx, '_*nal').map((i) => i.name)).toEqual(['_internal']);
        expect(indexOptions(null, 'x')).toEqual([]);
    });
});
