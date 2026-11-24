import React, { useEffect, useMemo, useState } from 'react';
import Message from '@splunk/react-ui/Message';
import P from '@splunk/react-ui/Paragraph';
import Table from '@splunk/react-ui/Table';
import Typography from '@splunk/react-ui/Typography';
import WaitSpinner from '@splunk/react-ui/WaitSpinner';
import styled from 'styled-components';
import { variables } from '@splunk/themes';
import { fetchAgentCatalog } from '../lib/api';
import { formatBytes, formatCount, jobsForForwarder, summarizeCatalog } from '../lib/replay';
import { Nowrap, Row, Stack } from './Layout';
import ReplayPlanner from './ReplayPlanner';
import { AgentStatus } from './Status';

const Scroll = styled.div`
    max-height: 220px;
    overflow-y: auto;
    border: 1px solid ${variables.borderColorWeak};
    border-radius: ${variables.borderRadius};
`;

/** The sourcetypes one agent holds; click a row to show it on the timeline. */
function Datasets({ datasets, value, onChange }) {
    return (
        <Scroll>
            <Table>
                <Table.Head>
                    <Table.HeadCell>Sourcetype</Table.HeadCell>
                    <Table.HeadCell align="right">Days</Table.HeadCell>
                    <Table.HeadCell align="right">Files</Table.HeadCell>
                    <Table.HeadCell align="right">Size</Table.HeadCell>
                    <Table.HeadCell>Data (UTC days)</Table.HeadCell>
                </Table.Head>
                <Table.Body>
                    {datasets.map((d) => (
                        <Table.Row
                            key={d.sourcetype}
                            data={d.sourcetype}
                            onClick={(e, st) => onChange(st)}
                            selected={d.sourcetype === value}
                        >
                            <Table.Cell>
                                <Typography as="span" weight={d.sourcetype === value ? 'semiBold' : undefined}>
                                    {d.sourcetype}
                                </Typography>
                            </Table.Cell>
                            <Table.Cell align="right">{formatCount(d.days)}</Table.Cell>
                            <Table.Cell align="right">{formatCount(d.files)}</Table.Cell>
                            <Table.Cell align="right"><Nowrap>{formatBytes(d.bytes)}</Nowrap></Table.Cell>
                            <Table.Cell><Nowrap>{d.firstDay === d.lastDay ? d.firstDay : `${d.firstDay} – ${d.lastDay}`}</Nowrap></Table.Cell>
                        </Table.Row>
                    ))}
                </Table.Body>
            </Table>
        </Scroll>
    );
}

/**
 * One agent: the datasets (sourcetypes) it holds, each on the replay timeline, and a
 * replay of a picked range from this agent only.
 * forwarder: a buildForwarders row. Its data is read from the catalog of the agent(s)
 * that hold its files, which is the forwarder itself unless files sit in a shared store.
 */
export default function AgentView({ forwarder, jobs, onClose, onSubmitted, onOpenJob }) {
    const [rows, setRows] = useState(null);
    const [error, setError] = useState(null);
    const [sourcetype, setSourcetype] = useState('');

    const agentGuids = forwarder.agents && forwarder.agents.length ? forwarder.agents : [forwarder.guid];
    const shared = agentGuids.some((g) => g !== forwarder.guid);
    const agentKey = agentGuids.join(' ');

    useEffect(() => {
        let stop = false;
        setRows(null);
        setError(null);
        Promise.all(agentKey.split(' ').map((g) => fetchAgentCatalog(g)))
            .then((lists) => { if (!stop) setRows(lists.flat()); })
            .catch((e) => { if (!stop) setError(e.message); });
        return () => { stop = true; };
    }, [agentKey]);

    // A shared store's catalog also holds other forwarders' sourcetypes: keep this one's.
    const ownRows = useMemo(() => {
        if (!rows) return [];
        if (!shared || !forwarder.sourcetypes.length) return rows;
        const own = new Set(forwarder.sourcetypes);
        return rows.filter((r) => own.has(r.sourcetype));
    }, [rows, shared, forwarder.sourcetypes]);
    const datasets = useMemo(() => summarizeCatalog(ownRows), [ownRows]);
    const agentJobs = useMemo(() => jobsForForwarder(jobs, forwarder.guid), [jobs, forwarder.guid]);

    useEffect(() => {
        if (datasets.length && !datasets.some((d) => d.sourcetype === sourcetype)) setSourcetype(datasets[0].sourcetype);
    }, [datasets, sourcetype]);

    return (
        <ReplayPlanner
            sourcetype={sourcetype}
            catalogRows={ownRows}
            jobs={agentJobs}
            forwarders={[forwarder]}
            agent={forwarder}
            title={forwarder.label}
            subtitle="Pick a dataset, then a time range on the timeline. Times are UTC and snap to whole hours. The replay runs on this agent only."
            onClose={onClose}
            onSubmitted={onSubmitted}
            onOpenJob={onOpenJob}
        >
            <Stack $gap="small">
                <Row $align="center">
                    <AgentStatus status={forwarder.status} />
                    <Typography as="span" variant="monoSmallBody" color="muted">{forwarder.guid}</Typography>
                    {forwarder.version && <Typography as="span" color="muted">{`agent ${forwarder.version}`}</Typography>}
                    {rows && (
                        <Typography as="span" color="muted">
                            {`${formatCount(datasets.length)} dataset${datasets.length === 1 ? '' : 's'} · ${formatCount(forwarder.files)} files · ${formatBytes(forwarder.bytes)}`}
                        </Typography>
                    )}
                </Row>
                {forwarder.status === 'stale' && (
                    <Message type="warning">This agent has not reported for 15 minutes. A replay waits up to 30 minutes for it.</Message>
                )}
                {forwarder.status === 'unregistered' && (
                    <Message type="warning">No agent has registered with this GUID, so a replay may not be picked up.</Message>
                )}
                {shared && (
                    <Message type="info">
                        This forwarder&apos;s files are held by another agent (a shared store). Day totals below include every
                        forwarder in that store; the replay still selects only this forwarder&apos;s files.
                    </Message>
                )}
                {error && <Message type="error">{error}</Message>}
                {!rows && !error && <WaitSpinner size="medium" />}
                {rows && datasets.length === 0 && <P>This agent has not published any data yet.</P>}
                {datasets.length > 0 && <Datasets datasets={datasets} value={sourcetype} onChange={setSourcetype} />}
            </Stack>
        </ReplayPlanner>
    );
}
