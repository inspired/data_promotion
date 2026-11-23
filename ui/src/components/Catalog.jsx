import React, { useMemo, useState } from 'react';
import Badge from '@splunk/react-ui/Badge';
import Paginator from '@splunk/react-ui/Paginator';
import Search from '@splunk/react-ui/Search';
import Table from '@splunk/react-ui/Table';
import Typography from '@splunk/react-ui/Typography';
import { formatBytes, formatCount, formatEpochShort, paginate } from '../lib/replay';
import { Field, Row, Stack } from './Layout';

const PAGE_SIZE = 50;

const ago = (epoch) => {
    const s = Math.floor(Date.now() / 1000) - Number(epoch || 0);
    if (!epoch || s < 0) return '–';
    if (s < 120) return `${s}s ago`;
    if (s < 7200) return `${Math.floor(s / 60)}m ago`;
    if (s < 172800) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
};

const isEmpty = (v) => v === null || v === undefined || v === '';

/** Sort by one column; empty values always go last. */
function sortRows(rows, key, dir) {
    const mult = dir === 'desc' ? -1 : 1;
    return [...rows].sort((a, b) => {
        const x = a[key];
        const y = b[key];
        if (isEmpty(x) !== isEmpty(y)) return isEmpty(x) ? 1 : -1;
        if (typeof x === 'number' || typeof y === 'number') return ((x || 0) - (y || 0)) * mult;
        return String(x || '').localeCompare(String(y || '')) * mult;
    });
}

/** A searchable, sortable, paginated table: the Design System table pattern. */
function DataTable({ rows, columns, rowKey, filterKeys, filterPlaceholder, emptyText, summary, initialSort }) {
    const [filter, setFilter] = useState('');
    const [page, setPage] = useState(1);
    const [sort, setSort] = useState(initialSort);

    const filtered = useMemo(() => {
        const f = filter.trim().toLowerCase();
        const list = f ? rows.filter((r) => filterKeys.some((k) => String(r[k] || '').toLowerCase().includes(f))) : rows;
        return sortRows(list, sort.key, sort.dir);
    }, [rows, filter, sort, filterKeys]);
    const { current, totalPages, rows: pageRows } = paginate(filtered, page, PAGE_SIZE);

    const onSort = (e, { sortKey }) => {
        setSort((prev) => ({ key: sortKey, dir: prev.key === sortKey && prev.dir === 'asc' ? 'desc' : 'asc' }));
        setPage(1);
    };

    return (
        <Stack>
            <Row $align="center">
                <Field $flex="0 1 320px">
                    <Search
                        value={filter}
                        placeholder={filterPlaceholder}
                        onChange={(e, { value }) => {
                            setFilter(value);
                            setPage(1);
                        }}
                    />
                </Field>
                <Typography as="span" color="muted">{summary(filtered.length)}</Typography>
            </Row>
            {filtered.length === 0 ? (
                <Typography as="p" color="muted">{rows.length ? 'Nothing matches the filter.' : emptyText}</Typography>
            ) : (
                <Table stripeRows headType="docked">
                    <Table.Head>
                        {columns.map((c) => (
                            <Table.HeadCell
                                key={c.key}
                                align={c.align}
                                sortKey={c.sortable === false ? undefined : c.key}
                                sortDir={sort.key === c.key ? sort.dir : 'none'}
                                onSort={c.sortable === false ? undefined : onSort}
                            >
                                {c.label}
                            </Table.HeadCell>
                        ))}
                    </Table.Head>
                    <Table.Body>
                        {pageRows.map((r) => (
                            <Table.Row key={rowKey(r)}>
                                {columns.map((c) => (
                                    <Table.Cell key={c.key} align={c.align}>
                                        {c.render ? c.render(r) : r[c.key]}
                                    </Table.Cell>
                                ))}
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

const PARTITION_COLUMNS = [
    { key: 'sourcetype', label: 'Sourcetype' },
    { key: 'day', label: 'Day (UTC)' },
    { key: 'forwarders', label: 'Forwarders', align: 'right', render: (r) => formatCount(r.forwarders) },
    { key: 'files', label: 'Files', align: 'right', render: (r) => formatCount(r.files) },
    { key: 'bytes', label: 'Size', align: 'right', render: (r) => formatBytes(r.bytes) },
    { key: 'earliest', label: 'First hour', render: (r) => formatEpochShort(r.earliest) },
    { key: 'latest', label: 'Last hour', render: (r) => formatEpochShort(r.latest) },
];

const mono = (v) => <Typography as="span" variant="monoSmallBody">{v}</Typography>;

const FORWARDER_STATUS = {
    reporting: { label: 'Reporting', type: 'notificationPositive' },
    stale: { label: 'Not reporting', type: 'notificationCaution' },
    unregistered: { label: 'Not registered', type: undefined },
};

const FORWARDER_COLUMNS = [
    {
        key: 'hostname',
        label: 'Forwarder',
        render: (r) => r.hostname || <Typography as="span" color="muted">Name unknown</Typography>,
    },
    {
        key: 'status',
        label: 'Status',
        render: (r) => {
            const s = FORWARDER_STATUS[r.status] || FORWARDER_STATUS.unregistered;
            return <Badge label={s.label} type={s.type} />;
        },
    },
    { key: 'guid', label: 'Server GUID', render: (r) => mono(r.guid) },
    {
        key: 'sourcetypes',
        label: 'Sourcetypes',
        sortable: false,
        render: (r) => (r.sourcetypes.length > 3
            ? `${r.sourcetypes.slice(0, 3).join(', ')} +${r.sourcetypes.length - 3}`
            : r.sourcetypes.join(', ') || '–'),
    },
    { key: 'files', label: 'Files', align: 'right', render: (r) => formatCount(r.files) },
    { key: 'bytes', label: 'Size', align: 'right', render: (r) => formatBytes(r.bytes) },
    {
        key: 'latest',
        label: 'Data (UTC days)',
        render: (r) => (r.earliest ? `${formatEpochShort(r.earliest).slice(0, 10)} – ${formatEpochShort(r.latest).slice(0, 10)}` : '–'),
    },
    { key: 'version', label: 'Version', render: (r) => r.version || '–' },
    { key: 'lastSeen', label: 'Last seen', render: (r) => ago(r.lastSeen) },
];

/** What the forwarders hold: per sourcetype and UTC day. */
export function PartitionsTable({ rows }) {
    const typed = useMemo(() => rows.map((r) => ({
        ...r,
        forwarders: Number(r.forwarders || 0),
        files: Number(r.files || 0),
        bytes: Number(r.bytes || 0),
        earliest: Number(r.earliest || 0),
        latest: Number(r.latest || 0),
    })), [rows]);
    const updated = rows.reduce((m, r) => Math.max(m, Number(r.updated_at || 0)), 0);
    return (
        <DataTable
            rows={typed}
            columns={PARTITION_COLUMNS}
            rowKey={(r) => r._key || `${r.sourcetype}|${r.day}`}
            filterKeys={['sourcetype', 'day']}
            filterPlaceholder="Filter by sourcetype or day"
            emptyText="No forwarder has published its catalog yet."
            summary={(n) => `${formatCount(n)} partitions · times in UTC · catalog updated ${ago(updated)}`}
            initialSort={{ key: 'day', dir: 'desc' }}
        />
    );
}

/** Forwarders that wrote data to the store, with names from the forwarder-names lookup. */
export function ForwardersTable({ forwarders }) {
    return (
        <DataTable
            rows={forwarders}
            columns={FORWARDER_COLUMNS}
            rowKey={(r) => r.guid}
            filterKeys={['hostname', 'guid', 'version']}
            filterPlaceholder="Filter by name, GUID or version"
            emptyText="No forwarder has registered yet. Install TA-replay_agent on the forwarders."
            summary={(n) => {
                const stale = forwarders.filter((f) => f.status !== 'reporting').length;
                return `${formatCount(n)} forwarders${stale ? ` · ${formatCount(stale)} not reporting` : ''} · times in UTC`;
            }}
            initialSort={{ key: 'hostname', dir: 'asc' }}
        />
    );
}
