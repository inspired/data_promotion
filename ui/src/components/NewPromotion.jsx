import React, { useEffect, useMemo, useState } from 'react';
import Button from '@splunk/react-ui/Button';
import Card from '@splunk/react-ui/Card';
import ControlGroup from '@splunk/react-ui/ControlGroup';
import Link from '@splunk/react-ui/Link';
import Message from '@splunk/react-ui/Message';
import Multiselect from '@splunk/react-ui/Multiselect';
import Select from '@splunk/react-ui/Select';
import Text from '@splunk/react-ui/Text';
import TimeRangeDropdown from '@splunk/react-time-range/Dropdown';
import SplunkwebConnector from '@splunk/react-time-range/SplunkwebConnector';
import { submitPromotion } from '../lib/api';
import {
    filterForwarders,
    formatBytes,
    formatCount,
    forwardersWithSourcetype,
    validateIndexFilter,
    validateSourcetype,
    validateTargetIndex,
    validateTimeRange,
} from '../lib/promotion';
import { ButtonSlot, Field, Row, Stack } from './Layout';

const NO_REALTIME = (presets) => presets.filter((p) => !String(p.earliest).startsWith('rt'));
const OPTION_LIMIT = 100;

/** Picker for writing forwarders; handles thousands of entries by filtering on the keyword. */
export function ForwarderPicker({ forwarders, values, onChange }) {
    const [keyword, setKeyword] = useState('');
    const { options, total, truncated } = useMemo(
        () => filterForwarders(forwarders, keyword, values, OPTION_LIMIT),
        [forwarders, keyword, values]
    );
    return (
        <Multiselect
            inline={false}
            style={{ width: '100%' }}
            values={values}
            placeholder={`All agents (${formatCount(forwarders.length)})`}
            controlledFilter
            onFilterChange={(e, { keyword: k }) => setKeyword(k)}
            onChange={(e, { values: v }) => onChange(v)}
            footerMessage={truncated
                ? `Showing ${OPTION_LIMIT} of ${formatCount(total)} matches. Type to narrow the list.`
                : undefined}
        >
            {options.map((f) => (
                <Multiselect.Option
                    key={f.guid}
                    value={f.guid}
                    label={f.label}
                    description={f.hostname ? f.guid : 'Name unknown'}
                />
            ))}
        </Multiselect>
    );
}

/** Destination index input: empty keeps each event's original index. */
export function TargetIndexField({ value, onChange }) {
    const error = validateTargetIndex(value);
    return (
        <ControlGroup
            label="Destination index"
            labelPosition="top"
            help={error || 'Empty: each event goes to its original index.'}
            error={!!error}
        >
            <Text value={value} placeholder="Original index" onChange={(e, { value: v }) => onChange(v)} />
        </ControlGroup>
    );
}

/** Shown while a destination index is set: Splunk cannot check that it exists. */
export function TargetIndexNotice({ value }) {
    const v = (value || '').trim();
    if (!v || validateTargetIndex(v)) return null;
    return (
        <Message type="warning">
            {`Every promoted event goes to the index ${v}. That index must already exist on the indexers: `}
            {'events sent to an index that does not exist are dropped (or go to the last chance index, if one is set).'}
        </Message>
    );
}

/** Form for submitting a promotion job. Submission goes through the promote_submit macro. */
export default function NewPromotion({ catalog, forwarders, onSubmitted, onOpenTimeline }) {
    const [sourcetype, setSourcetype] = useState('');
    const [earliest, setEarliest] = useState('-24h@h');
    const [latest, setLatest] = useState('@h');
    const [indexFilter, setIndexFilter] = useState('*');
    const [targetIndex, setTargetIndex] = useState('');
    const [selected, setSelected] = useState([]);
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState(null);

    const summary = useMemo(() => catalog.find((c) => c.sourcetype === sourcetype), [catalog, sourcetype]);
    const candidates = useMemo(() => forwardersWithSourcetype(forwarders, sourcetype), [forwarders, sourcetype]);

    // Drop selections that do not have the newly chosen sourcetype.
    useEffect(() => {
        const ok = new Set(candidates.map((f) => f.guid));
        setSelected((prev) => prev.filter((g) => ok.has(g)));
    }, [candidates]);

    const indexError = validateIndexFilter(indexFilter);
    const formError = validateSourcetype(sourcetype) || validateTimeRange(earliest, latest) || indexError
        || validateTargetIndex(targetIndex);

    const submit = async () => {
        setBusy(true);
        setResult(null);
        try {
            const row = await submitPromotion({
                sourcetype, indexFilter, targetIndex, forwarders: selected, earliest, latest,
            });
            if (row.status === 'rejected') {
                setResult({ type: 'error', text: `Rejected: ${row.message}` });
            } else {
                setResult({ type: 'success', text: `Job ${row.job_id} queued for ${row.earliest} → ${row.latest}.` });
            }
            onSubmitted(row.job_id);
        } catch (e) {
            setResult({ type: 'error', text: e.message });
        } finally {
            setBusy(false);
        }
    };

    const forwarderHelp = !sourcetype
        ? 'Pick a sourcetype first.'
        : selected.length
            ? `${formatCount(selected.length)} of ${formatCount(candidates.length)} agents selected.`
            : `All ${formatCount(candidates.length)} agents that wrote ${sourcetype}.`;

    return (
        <Card>
            <Card.Header
                title="New promotion"
                subtitle="Events keep their host, source and sourcetype, and go back to their original index unless you set a destination index."
            />
            <Card.Body>
                <Stack>
                    <Row>
                        <Field>
                            <ControlGroup
                                label="Sourcetype"
                                labelPosition="top"
                                help={summary
                                    ? `${summary.firstDay} to ${summary.lastDay} · ${formatCount(summary.files)} files · ${formatBytes(summary.bytes)}`
                                    : 'From the catalog published by the promotion agent.'}
                            >
                                <Select
                                    value={sourcetype}
                                    filter
                                    placeholder="Select a sourcetype"
                                    onChange={(e, { value }) => setSourcetype(value)}
                                >
                                    {catalog.map((c) => (
                                        <Select.Option
                                            key={c.sourcetype}
                                            label={c.sourcetype}
                                            value={c.sourcetype}
                                            description={`${c.days} day${c.days === 1 ? '' : 's'}`}
                                        />
                                    ))}
                                </Select>
                            </ControlGroup>
                        </Field>
                        <Field>
                            <ControlGroup label="Time range" labelPosition="top" help={sourcetype
                                    ? <>Each event is filtered on its own timestamp. <Link to="#" onClick={(e) => { e.preventDefault(); onOpenTimeline(sourcetype); }}>Pick on a timeline</Link></>
                                    : 'Each event is filtered on its own timestamp.'}>
                                <SplunkwebConnector presetsTransform={NO_REALTIME}>
                                    <TimeRangeDropdown
                                        earliest={earliest}
                                        latest={latest}
                                        realTimeDisabled
                                        onChange={(e, { earliest: ne, latest: nl }) => {
                                            setEarliest(ne);
                                            setLatest(nl);
                                        }}
                                    />
                                </SplunkwebConnector>
                            </ControlGroup>
                        </Field>
                    </Row>
                    <Row>
                        <Field $flex="3 1 360px">
                            <ControlGroup label="Agents" labelPosition="top" help={forwarderHelp}>
                                <ForwarderPicker forwarders={candidates} values={selected} onChange={setSelected} />
                            </ControlGroup>
                        </Field>
                        <Field $flex="1 1 200px">
                            <ControlGroup
                                label="Index filter"
                                labelPosition="top"
                                help={indexError || 'Wildcards allowed; * promotes all indexes.'}
                                error={!!indexError}
                            >
                                <Text value={indexFilter} onChange={(e, { value }) => setIndexFilter(value)} />
                            </ControlGroup>
                        </Field>
                        <Field $flex="1 1 200px">
                            <TargetIndexField value={targetIndex} onChange={setTargetIndex} />
                        </Field>
                        <ButtonSlot>
                            <Button
                                appearance="primary"
                                label={busy ? 'Submitting…' : 'Promote'}
                                disabled={busy || !!formError}
                                onClick={submit}
                            />
                        </ButtonSlot>
                    </Row>
                    <TargetIndexNotice value={targetIndex} />
                    {result && (
                        <Message type={result.type} onRequestRemove={() => setResult(null)}>
                            {result.text}
                        </Message>
                    )}
                </Stack>
            </Card.Body>
        </Card>
    );
}
