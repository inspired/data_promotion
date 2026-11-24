import React, { useEffect, useMemo, useRef, useState } from 'react';
import Button from '@splunk/react-ui/Button';
import ControlGroup from '@splunk/react-ui/ControlGroup';
import Message from '@splunk/react-ui/Message';
import Modal from '@splunk/react-ui/Modal';
import Text from '@splunk/react-ui/Text';
import Typography from '@splunk/react-ui/Typography';
import { submitPromotion } from '../lib/api';
import {
    formatBytes, formatCount, forwardersWithSourcetype, statusMeta, validateIndexFilter, validateTargetIndex,
} from '../lib/promotion';
import {
    DAY, coverage, dataSegments, domainOf, formatSpan, formatUtc, overlapping, parseUtc, promotionSegments,
} from '../lib/timeline';
import { ForwarderPicker, IndexFilterField, TargetIndexField, TargetIndexNotice } from './NewPromotion';
import { Field, Row, Stack } from './Layout';
import Timeline from './Timeline';

/** Start/end input: free text in UTC, applied on blur or Enter. */
function UtcInput({ label, value, onCommit }) {
    const [draft, setDraft] = useState(value === null ? '' : formatUtc(value));
    const [bad, setBad] = useState(false);
    useEffect(() => {
        setDraft(value === null ? '' : formatUtc(value));
        setBad(false);
    }, [value]);
    const commit = () => {
        const t = parseUtc(draft);
        if (t === null) {
            setBad(draft.trim() !== '');
            return;
        }
        setBad(false);
        onCommit(t);
    };
    return (
        <ControlGroup
            label={label}
            labelPosition="top"
            help={bad ? 'Use YYYY-MM-DD HH:MM (UTC).' : 'YYYY-MM-DD HH:MM, UTC'}
            error={bad}
        >
            <Text
                value={draft}
                placeholder="YYYY-MM-DD HH:MM"
                onChange={(e, { value: v }) => setDraft(v)}
                onBlur={commit}
                onKeyDown={(e) => {
                    if (e.key === 'Enter') commit();
                }}
            />
        </ControlGroup>
    );
}

/**
 * Promote one sourcetype with its time range picked on a timeline that shows the data
 * the forwarders hold and the earlier promotions of that sourcetype.
 *
 * With `agent` (a forwarder row), the planner is for that one agent: catalogRows and jobs
 * are that agent's, the promotion goes to that agent only, and `children` (its datasets)
 * are shown above the timeline.
 */
export default function PromotionPlanner({
    sourcetype, initialDay, catalogRows, jobs, forwarders, agent = null, title, subtitle, children,
    onClose, onSubmitted, onOpenJob,
}) {
    const data = useMemo(() => dataSegments(catalogRows, sourcetype), [catalogRows, sourcetype]);
    const promotions = useMemo(() => promotionSegments(jobs, sourcetype), [jobs, sourcetype]);
    const domain = useMemo(() => domainOf(data, promotions), [data, promotions]);

    const [selection, setSelection] = useState(() => {
        const d = initialDay && data.find((s) => s.day === initialDay);
        return d ? { start: Math.floor(d.start / DAY) * DAY, end: Math.floor(d.start / DAY) * DAY + DAY } : null;
    });
    // The link or button that opened the planner gets focus back on close.
    const opener = useRef(typeof document === 'undefined' ? null : document.activeElement);
    const [indexFilter, setIndexFilter] = useState('*');
    const [targetIndex, setTargetIndex] = useState('');
    const [selected, setSelected] = useState([]);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);

    const candidates = useMemo(() => forwardersWithSourcetype(forwarders, sourcetype), [forwarders, sourcetype]);
    const cover = coverage(selection, data);
    const overlaps = overlapping(selection, promotions);
    const indexError = validateIndexFilter(indexFilter) || validateTargetIndex(targetIndex);
    const rangeError = selection && selection.end <= selection.start ? 'End must be after start.' : null;

    const setStart = (t) => setSelection((s) => ({ start: t, end: s && s.end > t ? s.end : t + DAY }));
    const setEnd = (t) => setSelection((s) => ({ start: s && s.start < t ? s.start : t - DAY, end: t }));

    const submit = async () => {
        setBusy(true);
        setError(null);
        try {
            const row = await submitPromotion({
                sourcetype,
                indexFilter,
                targetIndex,
                forwarders: agent ? [agent.guid] : selected,
                earliest: selection.start,
                latest: selection.end,
            });
            onSubmitted(row.job_id);
            if (row.status === 'rejected') setError(`Rejected: ${row.message}`);
            else onClose();
        } catch (e) {
            setError(e.message);
        } finally {
            setBusy(false);
        }
    };

    return (
        <Modal
            open
            onRequestClose={onClose}
            closeOnClickAway={false}
            returnFocus={() => opener.current && opener.current.focus && opener.current.focus()}
            style={{ width: 'min(1100px, 96vw)' }}
        >
            <Modal.Header
                title={title || `Promote ${sourcetype}`}
                subtitle={subtitle || 'Pick a time range on the timeline. Times are UTC and snap to whole hours.'}
            />
            <Modal.Body>
                <Stack>
                    {children}
                    {agent && sourcetype && (
                        <Typography as="p" variant="body" weight="semiBold">
                            {`${sourcetype} on ${agent.label}`}
                        </Typography>
                    )}
                    <Timeline
                        data={data}
                        promotions={promotions}
                        domain={domain}
                        selection={selection}
                        onChange={setSelection}
                        onOpenJob={onOpenJob}
                    />
                    <Row>
                        <Field $flex="0 1 220px">
                            <UtcInput label="Start (UTC)" value={selection ? selection.start : null} onCommit={setStart} />
                        </Field>
                        <Field $flex="0 1 220px">
                            <UtcInput label="End (UTC)" value={selection ? selection.end : null} onCommit={setEnd} />
                        </Field>
                        <Field $flex="1 1 260px">
                            <ControlGroup label="Selected" labelPosition="top">
                                <Typography as="p" variant="body">
                                    {selection
                                        ? `${formatSpan(selection.end - selection.start)} · ${cover.days
                                            ? `data on ${cover.days} day${cover.days === 1 ? '' : 's'} (${formatCount(cover.files)} files, ${formatBytes(cover.bytes)} in those days)`
                                            : 'no data in this range'}`
                                        : 'Nothing selected yet.'}
                                </Typography>
                            </ControlGroup>
                        </Field>
                    </Row>
                    {overlaps.length > 0 && (
                        <Message type="warning">
                            {`This range overlaps ${overlaps.length} earlier promotion${overlaps.length === 1 ? '' : 's'} (`}
                            {overlaps.slice(0, 3).map((r) => `${r.jobId}, ${statusMeta(r.status).label.toLowerCase()}`).join('; ')}
                            {overlaps.length > 3 ? `; +${overlaps.length - 3} more` : ''}
                            {'). Events that are still indexed will be indexed again; tell them apart with promote_job.'}
                        </Message>
                    )}
                    <Row>
                        {!agent && (
                            <Field $flex="3 1 360px">
                                <ControlGroup
                                    label="Agents"
                                    labelPosition="top"
                                    help={selected.length
                                        ? `${formatCount(selected.length)} of ${formatCount(candidates.length)} agents selected.`
                                        : `All ${formatCount(candidates.length)} agents that wrote ${sourcetype}.`}
                                >
                                    <ForwarderPicker forwarders={candidates} values={selected} onChange={setSelected} />
                                </ControlGroup>
                            </Field>
                        )}
                        <Field $flex="1 1 200px">
                            <IndexFilterField sourcetype={sourcetype} value={indexFilter} onChange={setIndexFilter} />
                        </Field>
                        <Field $flex="1 1 200px">
                            <TargetIndexField value={targetIndex} onChange={setTargetIndex} />
                        </Field>
                    </Row>
                    <TargetIndexNotice value={targetIndex} />
                    {error && <Message type="error" onRequestRemove={() => setError(null)}>{error}</Message>}
                </Stack>
            </Modal.Body>
            <Modal.Footer>
                <Button appearance="secondary" label="Close" onClick={onClose} />
                <Button
                    appearance="primary"
                    label={busy ? 'Submitting…' : agent ? `Promote from ${agent.label}` : 'Promote'}
                    disabled={busy || !sourcetype || !selection || !!rangeError || !!indexError}
                    onClick={submit}
                />
            </Modal.Footer>
        </Modal>
    );
}
