import React, { useEffect, useRef, useState } from 'react';
import Button from '@splunk/react-ui/Button';
import DefinitionList, { Term, Description } from '@splunk/react-ui/DefinitionList';
import Heading from '@splunk/react-ui/Heading';
import Message from '@splunk/react-ui/Message';
import Modal from '@splunk/react-ui/Modal';
import Paginator from '@splunk/react-ui/Paginator';
import P from '@splunk/react-ui/Paragraph';
import SidePanel from '@splunk/react-ui/SidePanel';
import Table from '@splunk/react-ui/Table';
import Typography from '@splunk/react-ui/Typography';
import WaitSpinner from '@splunk/react-ui/WaitSpinner';
import { cancelJob, fetchTasks, runSearch } from '../lib/api';
import {
    canCancel,
    describeForwarders,
    formatBytes,
    formatCount,
    formatDuration,
    formatEpoch,
    formatRange,
    isValidJobId,
    paginate,
    parseForwarders,
    statusMeta,
    submitSearch,
    verifySearch,
} from '../lib/replay';
import { JobProgress } from './JobsTable';
import { JobStatus, TaskStatus } from './Status';
import { Nowrap, Row, SIDE_PANEL_INNER, SidePanelBody, SidePanelHeader, Stack } from './Layout';

const num = (v) => (v === undefined || v === null || v === '' ? null : Number(v));
const MAX_LISTED_FORWARDERS = 20;
const TASK_PAGE_SIZE = 20;
const TASK_POLL_MS = 5000;
// Problems first, then work in progress.
const TASK_ORDER = { failed: 0, running: 1, cancelled: 2, done: 3 };

/** Every forwarder's share of the job, read live from replay_tasks. */
function AgentTasks({ job }) {
    const [tasks, setTasks] = useState(null);
    const [error, setError] = useState(null);
    const [page, setPage] = useState(1);
    const active = statusMeta(job.status).active;

    useEffect(() => {
        let stop = false;
        let timer;
        const load = async () => {
            try {
                const rows = await fetchTasks(job.job_id);
                if (!stop) {
                    setTasks(rows.sort((a, b) => (TASK_ORDER[a.status] ?? 9) - (TASK_ORDER[b.status] ?? 9)
                        || String(a.agent_host).localeCompare(String(b.agent_host))));
                    setError(null);
                }
            } catch (e) {
                if (!stop) setError(e.message);
            }
            if (!stop && active) timer = setTimeout(load, TASK_POLL_MS);
        };
        load();
        return () => {
            stop = true;
            clearTimeout(timer);
        };
    }, [job.job_id, active]);

    const expected = num(job.agents_expected);
    const summary = tasks === null ? null : `${formatCount(tasks.length)} of ${expected === null ? '?' : formatCount(expected)} agents reported`;
    const { current, totalPages, rows } = paginate(tasks || [], page, TASK_PAGE_SIZE);
    return (
        <Stack $gap="small">
            <Row $align="center">
                <Heading level={3}>Progress per agent</Heading>
                {summary && <Typography as="span" color="muted">{summary}</Typography>}
                {tasks === null && !error && <WaitSpinner />}
            </Row>
            {error && <Message type="error">{error}</Message>}
            {tasks && tasks.length === 0 && <P>No agent has picked up this job yet.</P>}
            {rows.length > 0 && (
                <Table>
                    <Table.Head>
                        <Table.HeadCell>Agent</Table.HeadCell>
                        <Table.HeadCell>Status</Table.HeadCell>
                        <Table.HeadCell align="right">Files</Table.HeadCell>
                        <Table.HeadCell align="right">Events</Table.HeadCell>
                    </Table.Head>
                    <Table.Body>
                        {rows.map((t) => (
                            <Table.Row key={t._key || t.agent}>
                                <Table.Cell>
                                    <Stack $gap="small">
                                        <Typography as="span">{t.agent_host || t.agent}</Typography>
                                        {t.message && <Typography as="span" variant="smallBody" color="muted">{t.message}</Typography>}
                                    </Stack>
                                </Table.Cell>
                                <Table.Cell><TaskStatus status={t.status} /></Table.Cell>
                                <Table.Cell align="right">
                                    <Nowrap>{formatCount(t.files_done)} of {formatCount(t.files_total)}</Nowrap>
                                </Table.Cell>
                                <Table.Cell align="right"><Nowrap>{formatCount(t.events_staged)}</Nowrap></Table.Cell>
                            </Table.Row>
                        ))}
                    </Table.Body>
                </Table>
            )}
            {totalPages > 1 && (
                <Row $justify="flex-end">
                    <Paginator current={current} totalPages={totalPages} onChange={(e, { page: p }) => setPage(p)} />
                </Row>
            )}
        </Stack>
    );
}

/** Indexed-event breakdown for a job, straight from tstats on replay_job. */
function Verify({ job }) {
    const [state, setState] = useState({ loading: false, rows: null, error: null });

    const run = async () => {
        setState({ loading: true, rows: null, error: null });
        try {
            const earliest = String(Math.floor(num(job.earliest)));
            const latest = String(Math.ceil(num(job.latest)) + 1);
            const rows = await runSearch(verifySearch(job.job_id), earliest, latest);
            setState({ loading: false, rows, error: null });
        } catch (e) {
            setState({ loading: false, rows: null, error: e.message });
        }
    };

    const total = (state.rows || []).reduce((s, r) => s + Number(r.count || 0), 0);
    return (
        <Stack $gap="small">
            <Row $align="center">
                <Heading level={3}>Indexed events</Heading>
                <Button label={state.rows ? 'Refresh' : 'Count now'} onClick={run} disabled={state.loading} />
                {state.loading && <WaitSpinner />}
            </Row>
            {state.error && <Message type="error">{state.error}</Message>}
            {state.rows && state.rows.length === 0 && (
                <P>No events with replay_job={job.job_id} are searchable yet.</P>
            )}
            {state.rows && state.rows.length > 0 && (
                <Table>
                    <Table.Head>
                        <Table.HeadCell>Index</Table.HeadCell>
                        <Table.HeadCell>Sourcetype</Table.HeadCell>
                        <Table.HeadCell>Host</Table.HeadCell>
                        <Table.HeadCell align="right">Events</Table.HeadCell>
                    </Table.Head>
                    <Table.Body>
                        {state.rows.map((r) => (
                            <Table.Row key={`${r.index}|${r.sourcetype}|${r.host}`}>
                                <Table.Cell>{r.index}</Table.Cell>
                                <Table.Cell>{r.sourcetype}</Table.Cell>
                                <Table.Cell>{r.host}</Table.Cell>
                                <Table.Cell align="right">{formatCount(r.count)}</Table.Cell>
                            </Table.Row>
                        ))}
                        <Table.Row key="total">
                            <Table.Cell>
                                <Typography as="span" weight="semiBold">Total</Typography>
                            </Table.Cell>
                            <Table.Cell />
                            <Table.Cell />
                            <Table.Cell align="right">
                                <Typography as="span" weight="semiBold">{formatCount(total)}</Typography>
                            </Table.Cell>
                        </Table.Row>
                    </Table.Body>
                </Table>
            )}
        </Stack>
    );
}

function ForwarderList({ job, nameByGuid }) {
    const guids = parseForwarders(job.forwarders);
    if (!guids.length) return 'All agents';
    const shown = guids.slice(0, MAX_LISTED_FORWARDERS);
    return (
        <Stack $gap="small">
            {shown.map((g) => (
                <Typography as="span" key={g}>
                    {nameByGuid[g] ? `${nameByGuid[g]} ` : ''}
                    <Typography as="span" variant="monoSmallBody" color="muted">{g}</Typography>
                </Typography>
            ))}
            {guids.length > shown.length && (
                <Typography as="span" color="muted">and {formatCount(guids.length - shown.length)} more</Typography>
            )}
        </Stack>
    );
}

/** Side panel with job details and actions (cancel, replay again, verify). */
export default function JobPanel({ job, nameByGuid = {}, onClose, onChanged }) {
    const [confirm, setConfirm] = useState(null); // 'cancel' | 'again'
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const trigger = useRef(null); // focus returns here when the confirm dialog closes
    const ask = (kind) => (e) => {
        trigger.current = e.currentTarget;
        setConfirm(kind);
    };
    useEffect(() => { setError(null); }, [job && job._key]);

    if (!job) return null;

    const perform = (fn) => async () => {
        setBusy(true);
        try {
            await fn();
        } catch (e) {
            setError(e.message);
        } finally {
            setBusy(false);
            setConfirm(null);
        }
    };

    const doCancel = perform(async () => {
        await cancelJob(job._key);
        onChanged();
    });

    const doAgain = perform(async () => {
        const earliest = String(Math.floor(num(job.earliest)));
        const latest = String(Math.ceil(num(job.latest)));
        const spl = submitSearch(job.sourcetype, job.index_filter || '*', parseForwarders(job.forwarders));
        const rows = await runSearch(spl, earliest, latest);
        onChanged(rows[0] && rows[0].job_id);
    });

    const runtime = num(job.finished_at) && num(job.started_at) ? num(job.finished_at) - num(job.started_at) : null;
    const toIndexed = num(job.completed_at) && num(job.finished_at) ? num(job.completed_at) - num(job.finished_at) : null;
    const forwarderText = describeForwarders(job, nameByGuid);
    const fromText = forwarderText === 'All agents' ? 'all agents' : forwarderText;
    const warn = ['failed', 'rejected', 'incomplete'].includes(job.status);

    return (
        <SidePanel open dockPosition="right" onRequestClose={onClose} innerStyle={SIDE_PANEL_INNER}>
            <SidePanelHeader>
                <Row $align="center" $justify="space-between">
                    <Heading level={2}>
                        <Typography as="span" variant="monoBody">{job.job_id}</Typography>
                    </Heading>
                    <Button appearance="subtle" label="Close" onClick={onClose} />
                </Row>
            </SidePanelHeader>
            <SidePanelBody>
                <Row $align="center">
                    <JobStatus status={job.status} />
                </Row>
                <JobProgress job={job} />
                {job.message && <Message type={warn ? 'warning' : 'info'}>{job.message}</Message>}
                {error && <Message type="error" onRequestRemove={() => setError(null)}>{error}</Message>}

                <DefinitionList termWidth="170px">
                    <Term>Sourcetype</Term>
                    <Description>{job.sourcetype}</Description>
                    <Term>Agents</Term>
                    <Description><ForwarderList job={job} nameByGuid={nameByGuid} /></Description>
                    <Term>Index filter</Term>
                    <Description>{job.index_filter || '*'}</Description>
                    <Term>Time range</Term>
                    <Description>{formatRange(job.earliest, job.latest)}</Description>
                    <Term>Files</Term>
                    <Description>{formatCount(job.files_done)} of {formatCount(job.files_total)}</Description>
                    <Term>Read</Term>
                    <Description>{formatBytes(job.bytes_read)}</Description>
                    <Term>Agents done</Term>
                    <Description>
                        {formatCount(job.agents_reported || 0)} of {formatCount(job.agents_expected)} reported
                        {num(job.agents_failed) ? `, ${formatCount(job.agents_failed)} failed` : ''}
                    </Description>
                    <Term>Events replayed</Term>
                    <Description>{formatCount(job.events_staged)}</Description>
                    <Term>Events indexed</Term>
                    <Description>{formatCount(job.events_indexed)}</Description>
                    <Term>Outside range or filtered</Term>
                    <Description>{formatCount(job.events_skipped)}</Description>
                    <Term>Unparseable</Term>
                    <Description>{formatCount(job.events_bad)}</Description>
                    <Term>Submitted</Term>
                    <Description>{formatEpoch(job.submitted_at)} UTC by {job.submitted_by || '–'}</Description>
                    <Term>Replay took</Term>
                    <Description>{formatDuration(runtime)}</Description>
                    <Term>Indexing took</Term>
                    <Description>{formatDuration(toIndexed)}</Description>
                </DefinitionList>

                <Row>
                    {canCancel(job) && <Button appearance="destructive" label="Cancel job" onClick={ask('cancel')} />}
                    <Button label="Replay again" onClick={ask('again')} />
                </Row>

                {isValidJobId(job.job_id) && <AgentTasks job={job} />}

                {isValidJobId(job.job_id) && num(job.earliest) > 0 && <Verify job={job} />}
            </SidePanelBody>

            <Modal open={confirm !== null} onRequestClose={() => setConfirm(null)} returnFocus={trigger}>
                <Modal.Header title={confirm === 'cancel' ? 'Cancel this replay?' : 'Replay again?'} />
                <Modal.Body>
                    {confirm === 'cancel' ? (
                        <P>
                            {job.status === 'pending'
                                ? 'The job has not started and will not run.'
                                : 'Every agent stops after its current file. Events already handed over will still be indexed.'}
                        </P>
                    ) : (
                        <P>
                            A new job replays {job.sourcetype} from {fromText} for{' '}
                            {formatRange(job.earliest, job.latest)}. Events that are already indexed will be duplicated.
                        </P>
                    )}
                </Modal.Body>
                <Modal.Footer>
                    <Button label="Back" onClick={() => setConfirm(null)} disabled={busy} />
                    <Button
                        appearance={confirm === 'cancel' ? 'destructive' : 'primary'}
                        label={busy ? 'Working…' : confirm === 'cancel' ? 'Cancel job' : 'Replay again'}
                        onClick={confirm === 'cancel' ? doCancel : doAgain}
                        disabled={busy}
                    />
                </Modal.Footer>
            </Modal>
        </SidePanel>
    );
}
