import { cancelJob, fetchJobs, fetchSizeEstimate, submitPromotion, verifyJob } from './api';

const respond = (status, body) => Promise.resolve({
    ok: status < 400, status, statusText: 'x', json: () => Promise.resolve(body),
});

beforeEach(() => {
    global.fetch = jest.fn(() => respond(200, { items: [{ job_id: 'a' }] }));
});

const lastCall = () => {
    const [url, init] = global.fetch.mock.calls[global.fetch.mock.calls.length - 1];
    return { url, init, path: url.replace(/^.*\/services\//, '') };
};

test('lists jobs from the API', async () => {
    expect(await fetchJobs(5)).toEqual([{ job_id: 'a' }]);
    expect(lastCall().path).toBe('data_promotion/jobs?limit=5');
    expect(lastCall().init.method).toBe('GET');
});

test('submits a job as JSON', async () => {
    global.fetch.mockImplementationOnce(() => respond(201, { job: { job_id: 'j1' }, warnings: [] }));
    const out = await submitPromotion({
        sourcetype: 'splunkd', indexFilter: '', forwarders: ['G1'], targetIndex: '', earliest: '-24h@h', latest: 'now',
    });
    expect(out.job.job_id).toBe('j1');
    const { path, init } = lastCall();
    expect(path).toBe('data_promotion/jobs');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({
        sourcetype: 'splunkd', index_filter: '*', forwarders: ['G1'], target_index: '', earliest: '-24h@h', latest: 'now',
    });
});

test('API errors reject with the message and field', async () => {
    global.fetch.mockImplementationOnce(() => respond(400, { error: 'no forwarder holds data', field: 'sourcetype' }));
    await expect(submitPromotion({ sourcetype: 'x', earliest: 1, latest: 2 }))
        .rejects.toMatchObject({ message: 'no forwarder holds data', field: 'sourcetype', status: 400 });
    global.fetch.mockImplementationOnce(() => Promise.resolve({
        ok: false, status: 403, statusText: 'Forbidden', json: () => Promise.reject(new Error('not json')),
    }));
    await expect(fetchJobs()).rejects.toThrow('403 Forbidden');
    global.fetch.mockImplementationOnce(() => respond(404, { messages: [{ type: 'ERROR', text: 'Not Found' }] }));
    await expect(fetchJobs()).rejects.toThrow(/Restart Splunk/);
    global.fetch.mockImplementationOnce(() => respond(404, { error: 'no such job' }));
    await expect(verifyJob('x')).rejects.toThrow('no such job');
});

test('job actions and estimate URLs', async () => {
    await cancelJob('20261123-142538-99255a');
    expect(lastCall().path).toBe('data_promotion/jobs/20261123-142538-99255a/cancel');
    expect(lastCall().init.method).toBe('POST');
    await verifyJob('a/b');
    expect(lastCall().path).toBe('data_promotion/jobs/a%2Fb/verify');
    await fetchSizeEstimate('splunkd', ['G1', 'G2'], 100, 200);
    expect(lastCall().path).toBe('data_promotion/estimate?sourcetype=splunkd&forwarders=G1%2CG2&earliest=100&latest=200');
    await fetchSizeEstimate('splunkd', [], 100, 200);
    expect(lastCall().path).toBe('data_promotion/estimate?sourcetype=splunkd&earliest=100&latest=200');
});
