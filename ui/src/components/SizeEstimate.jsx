import React, { useEffect, useState } from 'react';
import Message from '@splunk/react-ui/Message';
import Typography from '@splunk/react-ui/Typography';
import { fetchSizeEstimate } from '../lib/api';
import { formatBytes, formatCount, validateTimeRange } from '../lib/promotion';

const DEBOUNCE_MS = 400;
const NAMED = 3;

/**
 * Estimated size of a promotion, per forwarder, from the API. Re-runs (debounced) when the
 * sourcetype, forwarders or range change. forwarders: GUIDs, [] for all.
 * Returns { status: 'idle' | 'loading' | 'ready' | 'error', summary, error }.
 */
export function useSizeEstimate(sourcetype, forwarders, earliest, latest) {
    const [state, setState] = useState({ status: 'idle' });
    const key = (forwarders || []).join(' ');
    useEffect(() => {
        if (!sourcetype || earliest === null || earliest === undefined || latest === null
            || latest === undefined || validateTimeRange(earliest, latest)) {
            setState({ status: 'idle' });
            return undefined;
        }
        let live = true;
        setState((s) => ({ ...s, status: 'loading' }));
        const timer = setTimeout(() => {
            fetchSizeEstimate(sourcetype, key ? key.split(' ') : [], earliest, latest)
                .then((summary) => live && setState({ status: 'ready', summary }))
                .catch((e) => live && setState({ status: 'error', error: e.message }));
        }, DEBOUNCE_MS);
        return () => {
            live = false;
            clearTimeout(timer);
        };
    }, [sourcetype, key, earliest, latest]);
    return state;
}

const plural = (n, word) => `${formatCount(n)} ${word}${n === 1 ? '' : 's'}`;

/** One line with the estimate, and a warning naming the forwarders that would exceed their limit. */
export default function SizeEstimate({ state }) {
    if (!state || state.status === 'idle') return null;
    const { summary } = state;
    if (state.status === 'error') {
        return <Typography as="p" variant="body">{`Size estimate not available: ${state.error}`}</Typography>;
    }
    if (!summary) return <Typography as="p" variant="body">Estimating size…</Typography>;
    if (!summary.forwarders) {
        return <Typography as="p" variant="body">Estimated size: no forwarder holds data in this range.</Typography>;
    }
    const { largest, over_limit: over } = summary;
    return (
        <>
            <Typography as="p" variant="body" data-size-estimate>
                {`Estimated size: about ${formatBytes(summary.bytes)} from ${plural(summary.forwarders, 'forwarder')}`}
                {summary.forwarders > 1 ? `, at most ${formatBytes(largest.bytes)} from one forwarder (${largest.name}).` : '.'}
                {state.status === 'loading' ? ' Updating…' : ''}
            </Typography>
            {over.length > 0 && (
                <Message type="warning">
                    {`${over.length === summary.forwarders ? (over.length === 1 ? 'The forwarder' : `All ${plural(over.length, 'forwarder')}`) : `${formatCount(over.length)} of ${plural(summary.forwarders, 'forwarder')}`} `}
                    {`would read more than ${over.length === 1 ? 'its' : 'their'} limit and promote nothing: `}
                    {over.slice(0, NAMED).map((r) => `${r.name} (${formatBytes(r.bytes)}, limit ${formatBytes(r.limit)}${r.limit_reported ? '' : ' by default'})`).join('; ')}
                    {over.length > NAMED ? `; +${formatCount(over.length - NAMED)} more` : ''}
                    {over.length < summary.forwarders ? '. The other forwarders still promote their share.' : '.'}
                    {' Pick a shorter range and promote the rest as separate jobs, or ask an admin to raise max_bytes_per_job on those forwarders.'}
                </Message>
            )}
        </>
    );
}
