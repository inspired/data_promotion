/**
 * Splunk access for the Data Promotion page: the app's REST API (/services/data_promotion),
 * the same one SOAR and scripts use. See docs/api.md.
 */
import { createRESTURL } from '@splunk/splunk-utils/url';
import { getDefaultFetchInit } from '@splunk/splunk-utils/fetch';

const enc = encodeURIComponent;

function apiURL(path, params) {
    const qs = new URLSearchParams();
    Object.entries(params || {}).forEach(([k, v]) => {
        if (v === undefined || v === null || v === '') return;
        if (Array.isArray(v)) v.forEach((x) => qs.append(k, x));
        else qs.append(k, String(v));
    });
    const q = qs.toString();
    return `${createRESTURL(`data_promotion/${path}`)}${q ? `?${q}` : ''}`;
}

/** Call the API; resolves with the JSON body, rejects with the API's error message. */
async function call(method, path, { params, body } = {}) {
    const init = getDefaultFetchInit();
    const res = await fetch(apiURL(path, params), {
        ...init,
        method,
        headers: { ...init.headers, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try {
        data = await res.json();
    } catch (e) {
        data = null;
    }
    if (!res.ok) {
        // A 404 without our JSON error: splunkd doesn't know the endpoint yet.
        const missing = res.status === 404 && !(data && data.error);
        const msg = missing
            ? 'The Data Promotion API is not available. Restart Splunk after installing or upgrading the app.'
            : (data && (data.error || (data.messages && data.messages[0] && data.messages[0].text)))
                || `${res.status} ${res.statusText}`;
        const err = new Error(msg);
        err.status = res.status;
        err.field = data && data.field;
        throw err;
    }
    return data;
}

const items = (data) => (data && Array.isArray(data.items) ? data.items : []);

export const fetchJobs = (limit = 200) => call('GET', 'jobs', { params: { limit } }).then(items);

/** Available data per sourcetype and UTC day, summed over all forwarders. */
export const fetchCatalog = () => call('GET', 'catalog').then(items);

/** One forwarder's catalog rows (one per sourcetype and UTC day). */
export const fetchForwarderCatalog = (guid) => call('GET', 'catalog', { params: { forwarder: guid } }).then(items);

/** Every forwarder: what it holds, status, add-on version and size limit. */
export const fetchForwarders = () => call('GET', 'forwarders').then(items);

/** Sampled indexes and indexed field names of one sourcetype, one row per forwarder. */
export const fetchFieldSamples = (sourcetype) => call('GET', 'fields', { params: { sourcetype } }).then(items);

/** Every forwarder's task for one job. */
export const fetchTasks = (jobId) => call('GET', `jobs/${enc(jobId)}/tasks`).then(items);

/** Pending jobs are cancelled at once; running jobs stop on every forwarder after the current file. */
export const cancelJob = (jobId) => call('POST', `jobs/${enc(jobId)}/cancel`);

/** Indexed events of a job by index, sourcetype and host: { items, events }. */
export const verifyJob = (jobId) => call('GET', `jobs/${enc(jobId)}/verify`);

/**
 * Create a job. earliest/latest: epochs or Splunk time modifiers. forwarders: GUIDs, [] for all.
 * Resolves with { job, estimate, warnings }; rejects with the reason when the job is not valid.
 */
export const submitPromotion = ({ sourcetype, indexFilter, forwarders, targetIndex, earliest, latest }) => call(
    'POST', 'jobs', {
        body: {
            sourcetype,
            index_filter: indexFilter || '*',
            forwarders: forwarders || [],
            target_index: targetIndex || '',
            earliest: String(earliest),
            latest: String(latest),
        },
    },
);

/**
 * Size estimate for a job: { forwarders, bytes, seconds, largest, over_limit, per_forwarder }.
 * forwarders: GUIDs, [] for all.
 */
export const fetchSizeEstimate = (sourcetype, forwarders, earliest, latest) => call('GET', 'estimate', {
    params: { sourcetype, forwarders: forwarders && forwarders.length ? forwarders.join(',') : '', earliest, latest },
});

/** The user's Splunk theme preference ("light", "dark", ...), or null if unavailable. */
export async function fetchThemePreference() {
    const url = createRESTURL('data/user-prefs/general', { app: '-', owner: '-' });
    const res = await fetch(`${url}?output_mode=json`, getDefaultFetchInit());
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    const data = await res.json();
    return (data && data.entry && data.entry[0] && data.entry[0].content && data.entry[0].content.theme) || null;
}
