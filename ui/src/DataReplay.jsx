import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Heading from '@splunk/react-ui/Heading';
import Message from '@splunk/react-ui/Message';
import P from '@splunk/react-ui/Paragraph';
import TabLayout from '@splunk/react-ui/TabLayout';
import WaitSpinner from '@splunk/react-ui/WaitSpinner';
import { ForwardersTable, PartitionsTable } from './components/Catalog';
import JobPanel from './components/JobPanel';
import JobsTable from './components/JobsTable';
import { Page, Stack } from './components/Layout';
import NewReplay from './components/NewReplay';
import { fetchAgents, fetchCatalog, fetchForwarderNames, fetchJobs, fetchSources } from './lib/api';
import { buildForwarders, formatCount, sortJobs, statusMeta, summarizeCatalog } from './lib/replay';

const POLL_ACTIVE_MS = 5000;
const POLL_IDLE_MS = 30000;
const CATALOG_REFRESH_MS = 5 * 60 * 1000;

export default function DataReplay() {
    const [tab, setTab] = useState('jobs');
    const [jobs, setJobs] = useState([]);
    const [catalogRows, setCatalogRows] = useState([]);
    const [sources, setSources] = useState([]);
    const [names, setNames] = useState([]);
    const [agentRows, setAgentRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [selectedKey, setSelectedKey] = useState(null);
    const timer = useRef(null);

    const loadJobs = useCallback(async () => {
        try {
            setJobs(sortJobs(await fetchJobs()));
            setError(null);
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    }, []);

    const loadCatalog = useCallback(async () => {
        try {
            // Each source is optional: one failing (e.g. no names yet) must not hide the others.
            const results = await Promise.allSettled([fetchCatalog(), fetchSources(), fetchAgents(), fetchForwarderNames()]);
            const [c, s, a, n] = results.map((r) => (r.status === 'fulfilled' ? r.value : null));
            if (c) setCatalogRows(c);
            if (s) setSources(s);
            if (a) setAgentRows(a);
            if (n) setNames(n);
            const failed = results.find((r, i) => r.status === 'rejected' && i < 3);
            if (failed) setError(failed.reason && failed.reason.message ? failed.reason.message : String(failed.reason));
        } catch (e) {
            setError(e.message);
        }
    }, []);

    useEffect(() => {
        loadJobs();
        loadCatalog();
        const id = setInterval(loadCatalog, CATALOG_REFRESH_MS);
        return () => clearInterval(id);
    }, [loadJobs, loadCatalog]);

    // Poll faster while any job is still moving.
    const anyActive = jobs.some((j) => statusMeta(j.status).active);
    useEffect(() => {
        clearTimeout(timer.current);
        timer.current = setTimeout(loadJobs, anyActive ? POLL_ACTIVE_MS : POLL_IDLE_MS);
        return () => clearTimeout(timer.current);
    }, [jobs, anyActive, loadJobs]);

    const catalog = useMemo(() => summarizeCatalog(catalogRows), [catalogRows]);
    const forwarders = useMemo(() => buildForwarders(sources, agentRows, names), [sources, agentRows, names]);
    const staleForwarders = forwarders.filter((f) => f.status === 'stale').length;
    const nameByGuid = useMemo(() => {
        const m = {};
        forwarders.forEach((f) => {
            if (f.hostname) m[f.guid] = f.hostname;
        });
        return m;
    }, [forwarders]);
    const selected = jobs.find((j) => j._key === selectedKey) || null;

    const afterChange = (newJobId) => {
        if (newJobId) setSelectedKey(newJobId);
        loadJobs();
    };

    return (
        <Page>
            <Stack $gap="small">
                <Heading level={1}>Data Replay</Heading>
                <P>
                    Replay Ingest Actions output by sourcetype, time range and forwarder. Each forwarder replays
                    its own files. Replayed events carry the indexed field replay_job; add NOT replay_job=* to
                    a search to exclude them.
                </P>
            </Stack>

            {error && <Message type="error" onRequestRemove={() => setError(null)}>{error}</Message>}
            {staleForwarders > 0 && (
                <Message type="warning">
                    {formatCount(staleForwarders)} of {formatCount(forwarders.length)} forwarders have not reported for
                    15 minutes. Jobs that need their data wait up to 30 minutes, then finish as incomplete.
                </Message>
            )}

            <NewReplay catalog={catalog} forwarders={forwarders} onSubmitted={afterChange} />

            <TabLayout activePanelId={tab} onChange={(e, { activePanelId }) => setTab(activePanelId)}>
                <TabLayout.Panel label={`Jobs (${formatCount(jobs.length)})`} panelId="jobs">
                    {loading ? (
                        <WaitSpinner size="medium" />
                    ) : (
                        <JobsTable
                            jobs={jobs}
                            nameByGuid={nameByGuid}
                            selectedKey={selectedKey}
                            onSelect={(job) => setSelectedKey(job._key)}
                        />
                    )}
                </TabLayout.Panel>
                <TabLayout.Panel label={`Available data (${formatCount(catalog.length)} sourcetypes)`} panelId="catalog">
                    <PartitionsTable rows={catalogRows} />
                </TabLayout.Panel>
                <TabLayout.Panel label={`Forwarders (${formatCount(forwarders.length)})`} panelId="forwarders">
                    <ForwardersTable forwarders={forwarders} />
                </TabLayout.Panel>
            </TabLayout>

            {selected && (
                <JobPanel
                    job={selected}
                    nameByGuid={nameByGuid}
                    onClose={() => setSelectedKey(null)}
                    onChanged={afterChange}
                />
            )}
        </Page>
    );
}
