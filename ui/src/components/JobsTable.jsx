import React, { useState } from 'react';
import Paginator from '@splunk/react-ui/Paginator';
import Progress from '@splunk/react-ui/Progress';
import Table from '@splunk/react-ui/Table';
import Typography from '@splunk/react-ui/Typography';
import {
    describeForwarders,
    formatEpochShort,
    formatRangeShort,
    paginate,
    progressOf,
} from '../lib/promotion';
import { Row, Stack } from './Layout';
import { JobStatus } from './Status';

const PAGE_SIZE = 25;


/** Progress bar for block layouts (the details panel). */
export function JobProgress({ job }) {
    const p = progressOf(job);
    if (!p) return null;
    const type = { completed: 'success', failed: 'error' }[job.status] || 'info';
    return <Progress percentage={p.percentage} type={type} tooltip={p.label} />;
}

/** Progress as text, for table cells. */
function ProgressText({ job }) {
    const p = progressOf(job);
    return <Typography as="span" color={p ? 'default' : 'muted'}>{p ? p.label : '–'}</Typography>;
}

/** Promotion jobs, newest first. Clicking a row opens its details. */
export default function JobsTable({ jobs, nameByGuid, onSelect, selectedKey }) {
    const [page, setPage] = useState(1);
    if (!jobs.length) {
        return <Typography as="p" color="muted">No promotion jobs yet. Submit one above.</Typography>;
    }
    const { current, totalPages, rows } = paginate(jobs, page, PAGE_SIZE);
    return (
        <Stack>
            <Table headType="docked">
                <Table.Head>
                    <Table.HeadCell>Status</Table.HeadCell>
                    <Table.HeadCell>Job</Table.HeadCell>
                    <Table.HeadCell>Sourcetype</Table.HeadCell>
                    <Table.HeadCell>Forwarders</Table.HeadCell>
                    <Table.HeadCell>Time range (UTC)</Table.HeadCell>
                    <Table.HeadCell>Progress</Table.HeadCell>
                    <Table.HeadCell>Submitted (UTC)</Table.HeadCell>
                </Table.Head>
                <Table.Body>
                    {rows.map((job) => (
                        <Table.Row
                            key={job._key}
                            data={job}
                            onClick={(e, data) => onSelect(data)}
                            selected={job._key === selectedKey}
                        >
                            <Table.Cell><JobStatus status={job.status} /></Table.Cell>
                            <Table.Cell>
                                <Typography as="span" variant="monoSmallBody">{job.job_id}</Typography>
                            </Table.Cell>
                            <Table.Cell>
                                {job.sourcetype}
                                {job.index_filter && job.index_filter !== '*' ? ` (${job.index_filter})` : ''}
                            </Table.Cell>
                            <Table.Cell>{describeForwarders(job, nameByGuid)}</Table.Cell>
                            <Table.Cell>{formatRangeShort(job.earliest, job.latest)}</Table.Cell>
                            <Table.Cell><ProgressText job={job} /></Table.Cell>
                            <Table.Cell>
                                {formatEpochShort(job.submitted_at)}
                                {job.submitted_by ? ` by ${job.submitted_by}` : ''}
                            </Table.Cell>
                        </Table.Row>
                    ))}
                </Table.Body>
            </Table>
            {totalPages > 1 && (
                <Row $justify="flex-end">
                    <Paginator current={current} totalPages={totalPages} onChange={(e, { page: p }) => setPage(p)} />
                </Row>
            )}
        </Stack>
    );
}
