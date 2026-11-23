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
import { submitReplay } from '../lib/api';
import {
    filterForwarders,
    formatBytes,
    formatCount,
    forwardersWithSourcetype,
    validateIndexFilter,
    validateSourcetype,
    validateTimeRange,
} from '../lib/replay';
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
            placeholder={`All forwarders (${formatCount(forwarders.length)})`}
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

/** Form for submitting a replay job. Submission goes through the replay_submit macro. */
export default function NewReplay({ catalog, forwarders, onSubmitted, onOpenTimeline }) {
    const [sourcetype, setSourcetype] = useState('');
    const [earliest, setEarliest] = useState('-24h@h');
    const [latest, setLatest] = useState('@h');
    const [indexFilter, setIndexFilter] = useState('*');
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
    const formError = validateSourcetype(sourcetype) || validateTimeRange(earliest, latest) || indexError;

    const submit = async () => {
        setBusy(true);
        setResult(null);
        try {
            const row = await submitReplay({ sourcetype, indexFilter, forwarders: selected, earliest, latest });
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
            ? `${formatCount(selected.length)} of ${formatCount(candidates.length)} forwarders selected.`
            : `All ${formatCount(candidates.length)} forwarders that wrote ${sourcetype}.`;

    return (
        <Card>
            <Card.Header
                title="New replay"
                subtitle="Events go back to their original index, host, source and sourcetype."
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
                                    : 'From the catalog published by the replay agent.'}
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
                            <ControlGroup label="Forwarders" labelPosition="top" help={forwarderHelp}>
                                <ForwarderPicker forwarders={candidates} values={selected} onChange={setSelected} />
                            </ControlGroup>
                        </Field>
                        <Field $flex="1 1 200px">
                            <ControlGroup
                                label="Index filter"
                                labelPosition="top"
                                help={indexError || 'Wildcards allowed; * replays all indexes.'}
                                error={!!indexError}
                            >
                                <Text value={indexFilter} onChange={(e, { value }) => setIndexFilter(value)} />
                            </ControlGroup>
                        </Field>
                        <ButtonSlot>
                            <Button
                                appearance="primary"
                                label={busy ? 'Submitting…' : 'Replay'}
                                disabled={busy || !!formError}
                                onClick={submit}
                            />
                        </ButtonSlot>
                    </Row>
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
