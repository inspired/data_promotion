/**
 * Splunk access for the Data Promotion page.
 * - KV store collections via REST (jobs list, catalog, cancel).
 * - Searches via @splunk/search-job (submitting through the promote_submit macro, verify).
 */
import SearchJob from '@splunk/search-job';
import { createRESTURL } from '@splunk/splunk-utils/url';
import { getDefaultFetchInit, handleResponse, handleError } from '@splunk/splunk-utils/fetch';
import { APP, JOBS, CATALOG, SOURCES, FORWARDER_NAMES, TASKS, AGENTS, CATALOG_SEARCH, cancelledStatus, submitSearch } from './promotion';

function kvURL(collection, key) {
    const path = `storage/collections/data/${collection}${key ? `/${encodeURIComponent(key)}` : ''}`;
    return createRESTURL(path, { app: APP, owner: 'nobody' });
}

async function kvGet(collection, params = {}) {
    const qs = new URLSearchParams({ output_mode: 'json', ...params }).toString();
    const res = await fetch(`${kvURL(collection)}?${qs}`, getDefaultFetchInit())
        .then(handleResponse(200))
        .catch(handleError(`Could not read ${collection}`));
    return Array.isArray(res) ? res : [];
}

async function kvGetOne(collection, key) {
    return fetch(`${kvURL(collection, key)}?output_mode=json`, getDefaultFetchInit())
        .then(handleResponse(200))
        .catch(handleError(`Could not read ${collection}/${key}`));
}

async function kvPut(collection, key, record) {
    const init = getDefaultFetchInit();
    const { _user, ...body } = record; // eslint-disable-line no-unused-vars
    return fetch(kvURL(collection, key), {
        ...init,
        method: 'POST',
        headers: { ...init.headers, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
        .then(handleResponse([200, 201]))
        .catch(handleError(`Could not update ${collection}/${key}`));
}

export function fetchJobs(limit = 200) {
    return kvGet(JOBS, { sort: 'submitted_at:-1', limit: String(limit) });
}

/**
 * Available data per sourcetype and day. Aggregated by a search because the raw
 * collection has one row per agent, sourcetype and day (large with many agents).
 */
export function fetchCatalog() {
    return runSearch(CATALOG_SEARCH, '0', 'now');
}

/** One agent's catalog rows (one per sourcetype and UTC day). */
export function fetchAgentCatalog(agentGuid) {
    return kvGet(CATALOG, {
        query: JSON.stringify({ agent: agentGuid }),
        fields: 'agent,sourcetype,day,files,bytes,earliest,latest,forwarders,updated_at',
        limit: '0',
    });
}

/** Per-agent, per-writer summary published by the agents. */
export function fetchSources() {
    return kvGet(SOURCES, { fields: 'agent,guid,sourcetypes,days,files,bytes,earliest,latest' });
}

/** Registered promotion agents (each agent writes its own row). */
export function fetchAgents() {
    return kvGet(AGENTS);
}

/** Fallback GUID -> hostname from indexer metrics ("Promotion - Forwarder names"). */
export function fetchForwarderNames() {
    return kvGet(FORWARDER_NAMES, { fields: 'guid,hostname,last_seen' });
}

/** Every agent's task for one job. */
export function fetchTasks(jobId) {
    return kvGet(TASKS, { query: JSON.stringify({ job_id: jobId }), sort: 'agent_host' });
}

/** The user's Splunk theme preference ("light", "dark", ...), or null if unavailable. */
export async function fetchThemePreference() {
    const url = createRESTURL('data/user-prefs/general', { app: '-', owner: '-' });
    const res = await fetch(`${url}?output_mode=json`, getDefaultFetchInit()).then(handleResponse(200));
    return (res && res.entry && res.entry[0] && res.entry[0].content && res.entry[0].content.theme) || null;
}

/** Cancel a job: re-read it so we never overwrite progress written by the agent meanwhile. */
export async function cancelJob(jobKey) {
    const current = await kvGetOne(JOBS, jobKey);
    const next = cancelledStatus(current.status);
    if (!next) throw new Error(`Job is ${current.status}; it can no longer be cancelled.`);
    return kvPut(JOBS, jobKey, {
        ...current,
        status: next,
        message: 'cancelled by user',
        finished_at: next === 'cancelled' ? Math.floor(Date.now() / 1000) : current.finished_at,
    });
}

/** Run a search and resolve with its result rows. */
export function runSearch(search, earliest, latest) {
    return new Promise((resolve, reject) => {
        const job = SearchJob.create(
            { search, earliest_time: earliest, latest_time: latest },
            { app: APP, cache: false }
        );
        const sub = job.getResults({ count: 0, output_mode: 'json' }).subscribe({
            next: (data) => {
                const fatal = (data.messages || []).find((m) => m.type === 'FATAL' || m.type === 'ERROR');
                if (fatal) reject(new Error(fatal.text));
                else resolve(data.results || []);
                sub.unsubscribe();
            },
            error: (err) => reject(err instanceof Error ? err : new Error(String(err && err.message ? err.message : err))),
        });
    });
}

/**
 * Submit a promotion through the promote_submit macro and resolve with its result row
 * (job_id, status, message, earliest, latest). earliest/latest: Splunk time modifiers or epochs.
 */
export async function submitPromotion({ sourcetype, indexFilter, forwarders, targetIndex, earliest, latest }) {
    const spl = submitSearch(sourcetype, indexFilter || '*', forwarders, targetIndex);
    const rows = await runSearch(spl, String(earliest), String(latest));
    if (!rows[0]) throw new Error('The submit search returned no result.');
    return rows[0];
}
