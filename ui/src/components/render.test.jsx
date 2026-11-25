/**
 * @jest-environment jsdom
 */
import React from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import SplunkThemeProvider from '@splunk/themes/SplunkThemeProvider';
import { ForwardersTable, PartitionsTable } from './Catalog';
import NewPromotion from './NewPromotion';
import JobPanel from './JobPanel';
import JobsTable from './JobsTable';
import PromotionPlanner from './PromotionPlanner';
import AgentView from './AgentView';
import { fetchAgentCatalog, fetchFieldSamples, submitPromotion } from '../lib/api';
import { IndexFilterField, LongRangeNotice } from './NewPromotion';

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
    submitPromotion: jest.fn(() => Promise.resolve({ job_id: 'new1', status: 'pending' })),
    fetchAgentCatalog: jest.fn(() => Promise.resolve([])),
    fetchFieldSamples: jest.fn(() => Promise.resolve([
        { agent: 'a1', indexes: ['_internal', 'main'], indexed_fields: ['node_guid'], events_sampled: 200, sampled_at: 1 },
        { agent: 'a2', indexes: ['_internal'], indexed_fields: [], events_sampled: 200, sampled_at: 2 },
    ])),
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
    expect(text()).toContain('No promotion jobs yet');
});

test('PartitionsTable renders partitions', async () => {
    const { text } = await render(<PartitionsTable onPick={() => {}} rows={[{ _key: 'splunkd|2026-11-19', sourcetype: 'splunkd', day: '2026-11-19', files: 7, bytes: 283505189, earliest: 1795068000, latest: 1795093200, backend: 'filesystem', forwarders: 2, updated_at: 1 }]} />);
    expect(text()).toContain('splunkd');
    expect(text()).toContain('270.4 MB');
});

test('JobPanel shows details and actions', async () => {
    const { text } = await render(<JobPanel job={{ ...job, status: 'running' }} onClose={() => {}} onChanged={() => {}} />);
    expect(text()).toContain('Events promoted');
    expect(text()).toContain('2 of 2 agents reported');
    expect(text()).toContain('Progress per agent');
    expect(text()).toContain('disk full');
    expect(text()).toContain('Cancel job');
    expect(text()).toContain('Promote again');
    expect(text()).toContain('Count now');
});

test('JobPanel cancel opens a confirm dialog', async () => {
    const { text } = await render(<JobPanel job={{ ...job, status: 'pending' }} onClose={() => {}} onChanged={() => {}} />);
    const btn = [...document.querySelectorAll('button')].find((b) => b.textContent === 'Cancel job');
    await act(async () => { btn.click(); });
    expect(text()).toContain('Cancel this promotion?');
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
    expect(text()).toContain('1 agents · 1 not reporting · times in UTC');
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

test('NewPromotion renders the form', async () => {
    const { text } = await render(<NewPromotion catalog={[{ sourcetype: 'splunkd', days: 1, files: 7, bytes: 1, firstDay: '2026-11-19', lastDay: '2026-11-19' }]} forwarders={fwd} onSubmitted={() => {}} />);
    expect(text()).toContain('Agents');
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

test('PromotionPlanner shows data, earlier promotions and the preselected day', async () => {
    const { text } = await render(
        <PromotionPlanner
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
    expect(text()).toContain('Promote splunkd');
    expect(document.body.innerHTML).toContain('2026-11-19 00:00');
    expect(text()).toContain('24 hours');
    // The completed job (2026-11-19 23:00 → 2026-11-21 09:00) overlaps that day.
    expect(text()).toContain('overlaps 1 earlier promotion');
    expect(document.querySelectorAll('[data-job]').length).toBe(1);
});

test('Status is a colored icon with text, not a badge; tables have no row stripes', async () => {
    await render(<JobsTable jobs={[job, { ...job, _key: 'f', job_id: 'f', status: 'failed' }]} onSelect={() => {}} />);
    expect(document.querySelector('[data-test="badge"]')).toBeNull();
    const tones = [...document.querySelectorAll('[data-status-tone]')].map((el) => [el.textContent, el.dataset.statusTone]);
    expect(tones).toEqual([['Completed', 'positive'], ['Failed', 'negative']]);
    expect(document.querySelectorAll('[data-status-tone] svg').length).toBe(2);
});

test('JobPanel: Open in Search for a completed job, with its destination index', async () => {
    await render(<JobPanel job={{ ...job, target_index: 'promotion_test' }} onClose={() => {}} onChanged={() => {}} />);
    const link = [...document.querySelectorAll('a')].find((a) => a.textContent.startsWith('Open in Search'));
    expect(link).toBeTruthy();
    const url = new URL(link.getAttribute('href'), 'http://x');
    expect(url.pathname).toMatch(/\/app\/search\/search$/);
    expect(url.searchParams.get('q')).toBe('search index=promotion_test promote_job="20261123-142538-99255a"');
    expect(url.searchParams.get('earliest')).toBe('1795042800');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(document.body.textContent).toContain('promotion_test');
});

test('JobPanel: no Open in Search while running', async () => {
    await render(<JobPanel job={{ ...job, status: 'running' }} onClose={() => {}} onChanged={() => {}} />);
    expect(document.body.textContent).not.toContain('Open in Search');
});

function typeInto(input, value) {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
    setter.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
}

test('NewPromotion: destination index shows the notice and is validated', async () => {
    const { text } = await render(<NewPromotion catalog={[]} forwarders={fwd} onSubmitted={() => {}} />);
    expect(text()).toContain('Destination index');
    const input = document.querySelector('input[placeholder="Original index"]');
    await act(async () => typeInto(input, 'promotion_test'));
    expect(text()).toContain('must already exist on the indexers');
    await act(async () => typeInto(input, 'Bad Index'));
    expect(text()).not.toContain('must already exist on the indexers');
    expect(text()).toContain('Use lowercase letters');
});

test('LongRangeNotice warns for a long range to the original index', async () => {
    const { text } = await render(<LongRangeNotice seconds={30 * 86400} targetIndex="" />);
    expect(text()).toContain('about 30 days');
    expect(text()).toContain('destination index used only for promotions');
});

test('LongRangeNotice is silent with a destination index', async () => {
    const { text } = await render(<LongRangeNotice seconds={30 * 86400} targetIndex="promoted" />);
    expect(text()).not.toContain('about 30 days');
});

test('NewPromotion: no long-range warning for the default 24 hours', async () => {
    const { text } = await render(<NewPromotion catalog={[]} forwarders={fwd} onSubmitted={() => {}} />);
    expect(text()).not.toContain('buckets then span weeks');
});

test('IndexFilterField suggests the sampled indexes of the sourcetype', async () => {
    const { text } = await render(<IndexFilterField sourcetype="splunkd" value="*" onChange={() => {}} />);
    expect(fetchFieldSamples).toHaveBeenCalledWith('splunkd');
    expect(text()).toContain('2 indexes seen in sampled events.');
});

test('IndexFilterField keeps working without a sample', async () => {
    fetchFieldSamples.mockResolvedValueOnce([]);
    const { text } = await render(<IndexFilterField sourcetype="syslog" value="ma*" onChange={() => {}} />);
    expect(text()).toContain('Wildcards allowed');
    expect(text()).not.toContain('seen in sampled events');
});

test('AgentView lists the agent datasets and promotes from that agent only', async () => {
    fetchAgentCatalog.mockResolvedValueOnce([
        ...catalogRows.map((r) => ({ ...r, agent: fwd[0].guid })),
        { sourcetype: 'syslog', day: '2026-11-20', files: 3, bytes: 3e6, earliest: 1795132800, latest: 1795140000, agent: fwd[0].guid },
    ]);
    const onSubmitted = jest.fn();
    const forwarder = { ...fwd[0], agents: [fwd[0].guid], sourcetypes: ['splunkd', 'syslog'] };
    const { text } = await render(
        <AgentView forwarder={forwarder} jobs={[job]} onClose={() => {}} onSubmitted={onSubmitted} onOpenJob={() => {}} />
    );
    expect(fetchAgentCatalog).toHaveBeenCalledWith(fwd[0].guid);
    expect(text()).toContain('2 datasets');
    expect(text()).toContain('splunkd on splunk1');
    expect(text()).not.toContain('Agents'); // no agent picker: fixed to this agent
    const syslogRow = [...document.querySelectorAll('tr')].find((tr) => tr.textContent.startsWith('syslog'));
    await act(async () => syslogRow.click());
    expect(text()).toContain('syslog on splunk1');

    const start = document.querySelector('input[placeholder="YYYY-MM-DD HH:MM"]');
    await act(async () => typeInto(start, '2026-11-20 00:00'));
    await act(async () => start.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    const btn = [...document.querySelectorAll('button')].find((b) => b.textContent === 'Promote from splunk1');
    await act(async () => btn.click());
    expect(submitPromotion).toHaveBeenCalledWith(expect.objectContaining({
        sourcetype: 'syslog', forwarders: [fwd[0].guid], targetIndex: '', earliest: 1795132800, latest: 1795219200,
    }));
    expect(onSubmitted).toHaveBeenCalledWith('new1');
});
