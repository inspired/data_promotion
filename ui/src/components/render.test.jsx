/**
 * @jest-environment jsdom
 */
import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import SplunkThemeProvider from '@splunk/themes/SplunkThemeProvider';
import { ForwardersTable, PartitionsTable } from './Catalog';
import NewReplay from './NewReplay';
import JobPanel from './JobPanel';
import JobsTable from './JobsTable';
import ReplayPlanner from './ReplayPlanner';

// SplunkwebConnector talks to splunkd (time parsing, capabilities); pass children through.
jest.mock('@splunk/react-time-range/SplunkwebConnector', () => ({
    __esModule: true,
    default: ({ children }) => {
        const React = require('react'); // eslint-disable-line global-require
        const presets = require('@splunk/react-time-range/presets').default; // eslint-disable-line global-require
        return React.cloneElement(children, { presets });
    },
}));

jest.mock('../lib/api', () => ({
    runSearch: jest.fn(() => Promise.resolve([])),
    submitReplay: jest.fn(() => Promise.resolve({ job_id: 'new1', status: 'pending' })),
    cancelJob: jest.fn(() => Promise.resolve({})),
    fetchTasks: jest.fn(() => Promise.resolve([
        { _key: 't1', agent_host: 'hf-osl-0001', status: 'done', files_done: 3, files_total: 3, events_staged: 1000, message: 'ok' },
        { _key: 't2', agent_host: 'hf-ams-0002', status: 'failed', files_done: 0, files_total: 2, events_staged: 0, message: 'disk full' },
    ])),
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const job = {
    _key: '20261123-142538-99255a',
    job_id: '20261123-142538-99255a',
    status: 'completed',
    forwarders: '5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8',
    sourcetype: 'splunkd',
    index_filter: '*',
    earliest: 1795042800,
    latest: 1795179600,
    files_total: 7,
    files_done: 7,
    bytes_read: 283505189,
    events_staged: 844426,
    events_indexed: 844426,
    submitted_at: 1795439138,
    submitted_by: 'admin',
    started_at: 1795439140,
    finished_at: 1795439182,
    completed_at: 1795439300,
    agents_expected: 2,
    agents_reported: 2,
    message: '844426 of 844426 events indexed',
};

const mounted = [];

async function render(el) {
    const div = document.createElement('div');
    document.body.appendChild(div);
    const root = createRoot(div);
    mounted.push({ root, div });
    await act(async () => {
        root.render(<SplunkThemeProvider family="enterprise" colorScheme="light">{el}</SplunkThemeProvider>);
    });
    return { root, text: () => document.body.textContent };
}

// Unmount instead of wiping document.body: react-ui keeps a shared layer container
// (used by SidePanel / Modal portals) attached to the body across renders.
afterEach(async () => {
    while (mounted.length) {
        const { root, div } = mounted.pop();
        await act(async () => root.unmount()); // eslint-disable-line no-await-in-loop
        div.remove();
    }
});

test('JobsTable renders a job row', async () => {
    const { text } = await render(<JobsTable jobs={[job, { ...job, _key: 'b', job_id: 'b', status: 'running' }]} onSelect={() => {}} />);
    expect(text()).toContain('20261123-142538-99255a');
    expect(text()).toContain('Completed');
    expect(text()).toContain('Running');
    expect(text()).toContain('844,426 of 844,426 events indexed');
});

test('JobsTable empty state', async () => {
    const { text } = await render(<JobsTable jobs={[]} onSelect={() => {}} />);
    expect(text()).toContain('No replay jobs yet');
});

test('PartitionsTable renders partitions', async () => {
    const { text } = await render(<PartitionsTable onPick={() => {}} rows={[{ _key: 'splunkd|2026-11-19', sourcetype: 'splunkd', day: '2026-11-19', files: 7, bytes: 283505189, earliest: 1795068000, latest: 1795093200, backend: 'filesystem', forwarders: 2, updated_at: 1 }]} />);
    expect(text()).toContain('splunkd');
    expect(text()).toContain('270.4 MB');
});

test('JobPanel shows details and actions', async () => {
    const { text } = await render(<JobPanel job={{ ...job, status: 'running' }} onClose={() => {}} onChanged={() => {}} />);
    expect(text()).toContain('Events replayed');
    expect(text()).toContain('2 of 2 forwarders reported');
    expect(text()).toContain('Progress per forwarder');
    expect(text()).toContain('disk full');
    expect(text()).toContain('Cancel job');
    expect(text()).toContain('Replay again');
    expect(text()).toContain('Count now');
});

test('JobPanel cancel opens a confirm dialog', async () => {
    const { text } = await render(<JobPanel job={{ ...job, status: 'pending' }} onClose={() => {}} onChanged={() => {}} />);
    const btn = [...document.querySelectorAll('button')].find((b) => b.textContent === 'Cancel job');
    await act(async () => { btn.click(); });
    expect(text()).toContain('Cancel this replay?');
    expect(text()).toContain('The job has not started and will not run.');
});

test('JobPanel hides cancel for finished jobs', async () => {
    const { text } = await render(<JobPanel job={job} onClose={() => {}} onChanged={() => {}} />);
    expect(text()).not.toContain('Cancel job');
    expect(text()).toContain('844426 of 844426 events indexed');
});

const fwd = [
    { guid: '5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8', hostname: 'splunk1', label: 'splunk1', status: 'stale', version: '1.2.1', sourcetypes: ['splunkd'], files: 7, bytes: 283505189, days: 1, earliest: 1795068000, latest: 1795093200, lastSeen: null },
];

test('ForwardersTable renders forwarders', async () => {
    const { text } = await render(<ForwardersTable forwarders={fwd} />);
    expect(text()).toContain('splunk1');
    expect(text()).toContain('5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8');
    expect(text()).toContain('1 forwarders · 1 not reporting · times in UTC');
    expect(text()).toContain('Not reporting');
    expect(text()).toContain('1.2.1');
});

test('JobPanel lists forwarder names', async () => {
    const { text } = await render(<JobPanel job={job} nameByGuid={{ '5A8D3399-FD7B-41CB-B0A9-23CC0BCE0EF8': 'splunk1' }} onClose={() => {}} onChanged={() => {}} />);
    expect(text()).toContain('splunk1');
});

test('JobsTable paginates', async () => {
    const many = Array.from({ length: 30 }, (_, i) => ({ ...job, _key: `k${i}`, job_id: `job-${i}`, submitted_at: i }));
    const { text } = await render(<JobsTable jobs={many} onSelect={() => {}} />);
    expect(text()).toContain('job-0');
    expect(text()).not.toContain('job-29');
});

test('NewReplay renders the form', async () => {
    const { text } = await render(<NewReplay catalog={[{ sourcetype: 'splunkd', days: 1, files: 7, bytes: 1, firstDay: '2026-11-19', lastDay: '2026-11-19' }]} forwarders={fwd} onSubmitted={() => {}} />);
    expect(text()).toContain('Forwarders');
    expect(text()).toContain('Pick a sourcetype first.');
});

const catalogRows = [
    { sourcetype: 'splunkd', day: '2026-11-19', files: 24, bytes: 1e9, earliest: 1795046400, latest: 1795129200, forwarders: 2 },
    { sourcetype: 'splunkd', day: '2026-11-20', files: 12, bytes: 5e8, earliest: 1795132800, latest: 1795176000, forwarders: 2 },
];

test('PartitionsTable: clicking a day opens the planner for it', async () => {
    const onPick = jest.fn();
    await render(<PartitionsTable rows={catalogRows} onPick={onPick} />);
    const link = [...document.querySelectorAll('a, button')].find((el) => el.textContent === '2026-11-20');
    await act(async () => link.click());
    expect(onPick).toHaveBeenCalledWith('splunkd', '2026-11-20');
});

test('ReplayPlanner shows data, earlier replays and the preselected day', async () => {
    const { text } = await render(
        <ReplayPlanner
            sourcetype="splunkd"
            initialDay="2026-11-19"
            catalogRows={catalogRows}
            jobs={[job]}
            forwarders={fwd}
            onClose={() => {}}
            onSubmitted={() => {}}
            onOpenJob={() => {}}
        />
    );
    expect(text()).toContain('Replay splunkd');
    expect(document.body.innerHTML).toContain('2026-11-19 00:00');
    expect(text()).toContain('24 hours');
    // The completed job (2026-11-19 23:00 → 2026-11-21 09:00) overlaps that day.
    expect(text()).toContain('overlaps 1 earlier replay');
    expect(document.querySelectorAll('[data-job]').length).toBe(1);
});
