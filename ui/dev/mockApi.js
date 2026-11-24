// Mock data for the visual harness (dev only): many agents and forwarders, jobs in every state.
const G = (i) => `${String(i).padStart(8, '0')}-AAAA-4BBB-8CCC-${String(i).padStart(12, '0')}`;
const now = Math.floor(Date.now() / 1000);
const sts = ['splunkd', 'syslog', 'aws:cloudtrail', 'pan:traffic', 'WinEventLog'];
const host = (i) => `hf-${['fra', 'ams', 'osl', 'iad'][i % 4]}-${String(i).padStart(4, '0')}.corp.example`;
const N = 1500;

export const fetchCatalog = async () => sts.flatMap((st, i) => [0, 1, 2].map((d) => ({
    sourcetype: st, day: `2026-11-${19 + d}`, files: String(24 * (40 + i * 30)), bytes: String(2.1e10 * (i + 1)),
    earliest: String(1795046400 + d * 86400), latest: String(1795132800 + d * 86400),
    forwarders: String(300 * (i + 1)), agents: String(300 * (i + 1)), updated_at: String(now - 240),
})));
// Every forwarder promotes its own files.
export const fetchSources = async () => [
    ...Array.from({ length: N }, (_, i) => ({
        agent: G(i), guid: G(i), sourcetypes: sts.slice(0, 1 + (i % 5)), days: 3, files: 72,
        bytes: 1.4e9 + i * 1e6, earliest: 1795046400, latest: 1795305600,
    })),
];
export const fetchAgents = async () => [
    ...Array.from({ length: N }, (_, i) => ({
        guid: G(i), hostname: host(i), agent_version: '1.2.0', splunk_version: '10.4.1',
        backend: 'filesystem', forwarders: 1, files: 72, bytes: 1.4e9 + i * 1e6, sourcetypes: sts.slice(0, 1 + (i % 5)),
        last_seen: i % 300 === 5 ? now - 7200 : now - (i % 5) * 60,
    })),
];
export const fetchForwarderNames = async () => [];
export const fetchFieldSamples = async (st) => Array.from({ length: 40 }, (_, i) => ({
    agent: G(i), events_sampled: 600, sampled_at: now - 300,
    indexes: st === 'splunkd' ? ['_internal', ...(i % 3 ? [] : ['_audit'])] : ['main', ...(i % 4 ? [] : [`${st.split(':')[0]}_archive`])],
    indexed_fields: i % 2 ? ['node_guid'] : [],
}));
const base = { sourcetype: 'splunkd', index_filter: '*', earliest: 1795046400, latest: 1795179600, submitted_by: 'admin' };
export const fetchJobs = async () => [
    { ...base, _key: 'j1', job_id: '20261123-142538-99255a', status: 'completed', target_index: 'promotion_test', forwarders: '*', agents_expected: 1500, agents_reported: 1500, files_total: 108000, files_done: 108000, bytes_read: 2.1e12, events_staged: 844426000, events_indexed: 844426000, submitted_at: now - 4000, started_at: now - 3990, finished_at: now - 3000, completed_at: now - 2800, message: '844426000 of 844426000 events indexed' },
    { ...base, _key: 'j2', job_id: '20261123-150101-a1b2c3', status: 'running', sourcetype: 'aws:cloudtrail', forwarders: `${G(1)} ${G(2)} ${G(3)}`, agents_expected: 3, agents_reported: 3, agents_running: 1, files_total: 120, files_done: 47, bytes_read: 9.1e9, events_staged: 1203344, submitted_at: now - 300, started_at: now - 280, message: '2 of 3 agents finished' },
    { ...base, _key: 'j3', job_id: '20261123-150455-d4e5f6', status: 'indexing', sourcetype: 'syslog', forwarders: G(7), agents_expected: 1, agents_reported: 1, files_total: 24, files_done: 24, events_staged: 500000, events_indexed: 312000, submitted_at: now - 200, started_at: now - 190, finished_at: now - 100, message: '312000 of 500000 events indexed so far' },
    { ...base, _key: 'j4', job_id: '20261123-150700-0f0f0f', status: 'pending', sourcetype: 'pan:traffic', forwarders: '*', agents_expected: 600, submitted_at: now - 20, message: 'waiting for agents' },
    { ...base, _key: 'j5', job_id: '20261123-120000-bad000', status: 'failed', forwarders: G(4), agents_expected: 1, agents_reported: 1, agents_failed: 1, submitted_at: now - 9000, message: 'hf-fra-0004.corp.example: selected 61000000000 bytes exceeds max_bytes_per_job=53687091200' },
    { ...base, _key: 'j6', job_id: '20261123-110000-inc000', status: 'incomplete', forwarders: '*', agents_expected: 1500, agents_reported: 1495, agents_failed: 2, files_total: 3, files_done: 3, events_staged: 1000, events_indexed: 1000, submitted_at: now - 12000, message: '1000 of 1000 events indexed; 2 agent(s) failed (hf-iad-0011.corp.example: not enough free disk space (free=1073741824, need=2147483648)) 5 agent(s) did not report' },
];
export const fetchTasks = async (jobId) => Array.from({ length: jobId.endsWith('a1b2c3') ? 3 : 45 }, (_, i) => ({
    _key: `${jobId}|${G(i)}`, agent: G(i), agent_host: host(i),
    status: i === 0 ? 'running' : i === 4 ? 'failed' : 'done', files_total: 40, files_done: i === 0 ? 7 : 40,
    events_staged: 401000 + i, message: i === 4 ? 'not enough free disk space (free=1073741824, need=2147483648)' : i === 0 ? 'promoting' : '401000 events from 40 files handed to the batch input',
}));
export const fetchThemePreference = async () => null;
export const cancelJob = async () => ({});
export const runSearch = async () => [{ index: '_internal', sourcetype: 'splunkd', host: 'splunk1', count: '807600' }, { index: '_internal', sourcetype: 'splunkd', host: 'ip-172-31-28-22', count: '36826' }];
export const submitPromotion = async ({ earliest, latest }) => ({ job_id: '20261123-160000-new000', status: 'pending', earliest, latest, message: 'queued' });
export const fetchAgentCatalog = async (guid) => {
    const i = parseInt(guid.slice(0, 8), 10) || 0;
    return sts.slice(0, 1 + (i % 5)).flatMap((st, k) => [0, 1, 2].filter((d) => d !== k % 3 || k === 0).map((d) => ({
        agent: guid, sourcetype: st, day: `2026-11-${19 + d}`, files: String(24 - k * 3), bytes: String(4.6e8 / (k + 1)),
        earliest: String(1795046400 + d * 86400 + k * 3600), latest: String(1795132800 + d * 86400 - k * 7200),
        forwarders: '1', updated_at: String(now - 240),
    })));
};
