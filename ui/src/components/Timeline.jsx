import React, { useEffect, useMemo, useRef, useState } from 'react';
import styled from 'styled-components';
import Button from '@splunk/react-ui/Button';
import ButtonGroup from '@splunk/react-ui/ButtonGroup';
import Typography from '@splunk/react-ui/Typography';
import { variables } from '@splunk/themes';
import { formatBytes, formatCount, statusMeta } from '../lib/promotion';
import { TONE_COLOR } from './Status';
import {
    DAY, HOUR, floorTo, formatSpan, formatUtc, makeScale, normalizeSelection, shiftSelection, ticks, zoomTo,
} from '../lib/timeline';

const GUTTER = 76; // left labels
const AXIS_H = 22;
const DATA_H = 26;
const LANE_H = 16;
const LANE_GAP = 4;
const MAX_LANES = 6;
const EDGE_PX = 7; // grab distance for the selection edges
const CLICK_PX = 3; // less movement than this is a click
const MIN_VIEW = 6 * HOUR;

/** Bars use the same tone colors as the status indicators in the tables. */
const promotionColor = (status) => TONE_COLOR[statusMeta(status).tone] || TONE_COLOR.neutral;

const Svg = styled.svg`
    display: block;
    width: 100%;
    user-select: none;
    touch-action: none;
    font-family: inherit;
    font-size: ${variables.fontSizeSmall};
    outline: none;
    &:focus-visible .tl-selection {
        stroke-width: 3;
    }
`;
const Text = styled.text`
    fill: ${variables.contentColorMuted};
`;
const Strong = styled.text`
    fill: ${variables.contentColorDefault};
`;
const GridLine = styled.line`
    stroke: ${variables.borderColorWeak};
`;
const Track = styled.rect`
    fill: ${variables.borderColorWeak};
    opacity: 0.35;
`;
const DataBar = styled.rect`
    fill: ${variables.contentColorMuted};
`;
const PromotionBar = styled.rect`
    fill: ${(p) => p.$color};
    cursor: pointer;
`;
const Selection = styled.rect`
    fill: ${variables.interactiveColorPrimary};
    fill-opacity: 0.16;
    stroke: ${variables.interactiveColorPrimary};
    stroke-width: 2;
    pointer-events: none;
`;
const Handle = styled.rect`
    fill: ${variables.interactiveColorPrimary};
    pointer-events: none;
`;
const HoverLine = styled.line`
    stroke: ${variables.contentColorDefault};
    stroke-dasharray: 2 3;
    pointer-events: none;
`;
const Wrapper = styled.div`
    border: 1px solid ${variables.borderColorWeak};
    border-radius: ${variables.borderRadius};
    padding: ${variables.spacingSmall} ${variables.spacingSmall} ${variables.spacingXSmall};
`;
const Footer = styled.div`
    display: flex;
    align-items: center;
    gap: ${variables.spacingSmall};
`;
const Readout = styled.div`
    flex: 1 1 0;
    min-width: 0;
    padding-left: ${GUTTER}px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
`;

/** Width of an element, following resizes. */
function useWidth(ref) {
    const [width, setWidth] = useState(800);
    useEffect(() => {
        const el = ref.current;
        if (!el) return undefined;
        const update = () => setWidth(Math.max(240, el.getBoundingClientRect().width));
        update();
        if (typeof ResizeObserver === 'undefined') return undefined;
        const ro = new ResizeObserver(update);
        ro.observe(el);
        return () => ro.disconnect();
    }, [ref]);
    return width;
}

const clampView = (v, domain) => {
    const span = Math.min(Math.max(v[1] - v[0], MIN_VIEW), domain[1] - domain[0]);
    const start = Math.min(Math.max(domain[0], v[0]), domain[1] - span);
    return [start, start + span];
};

function describeHover(t, data, promotions) {
    const parts = [`${formatUtc(t)} UTC`];
    const d = data.find((s) => t >= s.start && t < s.end);
    parts.push(d
        ? `data ${d.day}: ${formatCount(d.files)} files, ${formatBytes(d.bytes)}`
        : 'no data');
    const r = promotions.filter((s) => t >= s.start && t < s.end);
    if (r.length) parts.push(`promoted by ${r.map((s) => s.jobId).join(', ')}`);
    return parts.join(' · ');
}

/**
 * Interactive timeline for one sourcetype.
 * - Data track: one bar per UTC day with files, shaded by file count.
 * - Promotion lanes: earlier jobs over their time range, colored like their status;
 *   click one to open it.
 * - Selection: drag to select, drag inside to move, drag an edge to resize, click to
 *   select a whole UTC day. Arrow keys move it by an hour (Shift: resize the end).
 * - Zoom: buttons, or Ctrl/⌘ + wheel (trackpad pinch); horizontal scroll pans.
 */
export default function Timeline({ data, promotions, domain, selection, onChange, onOpenJob }) {
    const wrapRef = useRef(null);
    const svgRef = useRef(null);
    const width = useWidth(wrapRef);
    const plotW = Math.max(100, width - GUTTER - 4);
    const [view, setView] = useState(domain);
    const [drag, setDrag] = useState(null);
    const [hoverT, setHoverT] = useState(null);
    const [hoverMode, setHoverMode] = useState('create');

    // New sourcetype / data: show everything, or zoom to a preselected range.
    const domainKey = `${domain[0]}-${domain[1]}`;
    useEffect(() => {
        setView(selection ? zoomTo(selection, domain) : domain);
    }, [domainKey]); // eslint-disable-line react-hooks/exhaustive-deps

    // A selection typed into the inputs that lies outside the view: bring it into view.
    useEffect(() => {
        if (!drag && selection && (selection.start < view[0] || selection.end > view[1])) {
            setView(clampView(zoomTo(selection, domain), domain));
        }
    }, [selection && selection.start, selection && selection.end]); // eslint-disable-line react-hooks/exhaustive-deps

    const scale = useMemo(() => makeScale(view, plotW), [view, plotW]);
    const axis = useMemo(() => ticks(view, plotW), [view, plotW]);
    const maxFiles = Math.max(1, ...data.map((d) => d.files));
    const lanes = Math.min(MAX_LANES, promotions.reduce((m, r) => Math.max(m, r.lane + 1), 0));
    const hiddenPromotions = promotions.filter((r) => r.lane >= MAX_LANES).length;
    const laneTop = AXIS_H + DATA_H + 10;
    const height = laneTop + Math.max(1, lanes) * (LANE_H + LANE_GAP) + 4;
    const clipX = (x) => Math.min(plotW, Math.max(0, x));

    const tAt = (clientX) => {
        const rect = svgRef.current.getBoundingClientRect();
        return scale.t(clientX - rect.left - GUTTER);
    };

    const modeAt = (clientX) => {
        if (!selection) return 'create';
        const rect = svgRef.current.getBoundingClientRect();
        const x = clientX - rect.left - GUTTER;
        const x0 = scale.x(selection.start);
        const x1 = scale.x(selection.end);
        if (Math.abs(x - x0) <= EDGE_PX) return 'start';
        if (Math.abs(x - x1) <= EDGE_PX) return 'end';
        if (x > x0 && x < x1) return 'move';
        return 'create';
    };

    const onPointerDown = (e) => {
        if (e.button !== 0) return;
        svgRef.current.setPointerCapture(e.pointerId);
        const job = e.target.getAttribute('data-job');
        setDrag({ mode: job ? 'create' : modeAt(e.clientX), x0: e.clientX, t0: tAt(e.clientX), sel: selection, job, moved: false });
    };

    const onPointerMove = (e) => {
        const t = tAt(e.clientX);
        setHoverT(t >= view[0] && t <= view[1] ? t : null);
        if (!drag) {
            setHoverMode(e.target.getAttribute('data-job') ? 'job' : modeAt(e.clientX));
            return;
        }
        const moved = drag.moved || Math.abs(e.clientX - drag.x0) >= CLICK_PX;
        if (!moved) return;
        if (!drag.moved) setDrag({ ...drag, moved: true });
        if (drag.mode === 'create') onChange(normalizeSelection(drag.t0, t, domain));
        else if (drag.mode === 'move') onChange(shiftSelection(drag.sel, Math.round((t - drag.t0) / HOUR) * HOUR, domain));
        else if (drag.mode === 'start') onChange(normalizeSelection(t, drag.sel.end, domain));
        else if (drag.mode === 'end') onChange(normalizeSelection(drag.sel.start, t, domain));
    };

    const onPointerUp = (e) => {
        if (!drag) return;
        if (!drag.moved) {
            if (drag.job && onOpenJob) onOpenJob(drag.job);
            else if (drag.mode === 'create') {
                const day = floorTo(drag.t0, DAY);
                onChange(normalizeSelection(day, day + DAY, domain));
            }
        }
        setDrag(null);
        if (svgRef.current.hasPointerCapture(e.pointerId)) svgRef.current.releasePointerCapture(e.pointerId);
    };

    // Wheel: Ctrl/⌘ (and trackpad pinch) zooms around the pointer, horizontal scroll pans.
    // Plain vertical scrolling is left to the page.
    useEffect(() => {
        const el = svgRef.current;
        const onWheel = (e) => {
            const horizontal = Math.abs(e.deltaX) > Math.abs(e.deltaY) || e.shiftKey;
            if (!e.ctrlKey && !e.metaKey && !horizontal) return;
            e.preventDefault();
            setView((v) => {
                const span = v[1] - v[0];
                if (e.ctrlKey || e.metaKey) {
                    const rect = el.getBoundingClientRect();
                    const frac = Math.min(1, Math.max(0, (e.clientX - rect.left - GUTTER) / plotW));
                    const at = v[0] + frac * span;
                    const next = span * Math.exp(e.deltaY * 0.01);
                    return clampView([at - frac * next, at + (1 - frac) * next], domain);
                }
                const delta = ((e.shiftKey ? e.deltaY : e.deltaX) / plotW) * span;
                return clampView([v[0] + delta, v[1] + delta], domain);
            });
        };
        el.addEventListener('wheel', onWheel, { passive: false });
        return () => el.removeEventListener('wheel', onWheel);
    }, [domain, plotW]);

    const onKeyDown = (e) => {
        if (!selection) return;
        const step = e.altKey ? DAY : HOUR;
        const dir = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (!dir) return;
        e.preventDefault();
        if (e.shiftKey) onChange(normalizeSelection(selection.start, selection.end + dir * step, domain));
        else onChange(shiftSelection(selection, dir * step, domain));
    };

    const zoom = (factor) => setView((v) => {
        const mid = (v[0] + v[1]) / 2;
        const half = ((v[1] - v[0]) * factor) / 2;
        return clampView([mid - half, mid + half], domain);
    });
    const pan = (dir) => setView((v) => clampView([v[0] + dir * (v[1] - v[0]) * 0.5, v[1] + dir * (v[1] - v[0]) * 0.5], domain));

    const CURSORS = { create: 'crosshair', job: 'pointer', move: 'grab', start: 'col-resize', end: 'col-resize' };
    let cursor = CURSORS[hoverMode] || 'crosshair';
    if (drag) cursor = drag.mode === 'move' ? 'grabbing' : drag.mode === 'create' ? 'crosshair' : 'col-resize';

    const selX0 = selection ? clipX(scale.x(selection.start)) : 0;
    const selX1 = selection ? clipX(scale.x(selection.end)) : 0;
    const showSel = selection && selection.end > view[0] && selection.start < view[1];
    const atAll = view[0] === domain[0] && view[1] === domain[1];

    return (
        <Wrapper ref={wrapRef}>
            <Svg
                ref={svgRef}
                height={height}
                viewBox={`0 0 ${width} ${height}`}
                role="slider"
                tabIndex={0}
                aria-label="Promotion time range"
                aria-valuetext={selection
                    ? `${formatUtc(selection.start)} to ${formatUtc(selection.end)} UTC`
                    : 'No range selected'}
                style={{ cursor }}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerLeave={() => setHoverT(null)}
                onKeyDown={onKeyDown}
            >
                <Strong x={0} y={AXIS_H + DATA_H / 2 + 4}>Data</Strong>
                <Strong x={0} y={laneTop + LANE_H - 3}>Promotions</Strong>
                {hiddenPromotions > 0 && (
                    <Text x={0} y={laneTop + LANE_H * 2}>{`+${hiddenPromotions} more`}</Text>
                )}
                <g transform={`translate(${GUTTER},0)`}>
                    <rect x={0} y={0} width={plotW} height={height} fill="transparent" />
                    {axis.map((tk) => (
                        <g key={tk.t}>
                            <GridLine x1={scale.x(tk.t)} x2={scale.x(tk.t)} y1={AXIS_H - 4} y2={height} strokeWidth={tk.major ? 1 : 0.5} />
                            <Text x={scale.x(tk.t) + 3} y={12}>{tk.label}</Text>
                        </g>
                    ))}
                    <Track x={0} y={AXIS_H} width={plotW} height={DATA_H} />
                    {data.filter((d) => d.end > view[0] && d.start < view[1]).map((d) => {
                        const x0 = clipX(scale.x(d.start));
                        const x1 = clipX(scale.x(d.end));
                        return (
                            <DataBar
                                key={d.day}
                                x={x0}
                                y={AXIS_H}
                                width={Math.max(1, x1 - x0 - 1)}
                                height={DATA_H}
                                opacity={0.35 + 0.55 * (d.files / maxFiles)}
                            >
                                <title>{`${d.day}: ${formatCount(d.files)} files, ${formatBytes(d.bytes)}, ${formatCount(d.forwarders)} forwarders`}</title>
                            </DataBar>
                        );
                    })}
                    {lanes === 0 && (
                        <Text x={4} y={laneTop + LANE_H - 3}>No earlier promotions of this sourcetype</Text>
                    )}
                    {promotions.filter((r) => r.lane < MAX_LANES && r.end > view[0] && r.start < view[1]).map((r) => {
                        const x0 = clipX(scale.x(r.start));
                        const x1 = clipX(scale.x(r.end));
                        return (
                            <PromotionBar
                                key={r.key}
                                data-job={r.jobId}
                                $color={promotionColor(r.status)}
                                x={x0}
                                y={laneTop + r.lane * (LANE_H + LANE_GAP)}
                                width={Math.max(2, x1 - x0)}
                                height={LANE_H}
                                rx={2}
                                opacity={0.85}
                            >
                                <title>{`${r.jobId} · ${statusMeta(r.status).label} · ${formatUtc(r.start)} → ${formatUtc(r.end)} UTC (click to open)`}</title>
                            </PromotionBar>
                        );
                    })}
                    {showSel && (
                        <>
                            <Selection className="tl-selection" x={selX0} y={AXIS_H - 4} width={Math.max(2, selX1 - selX0)} height={height - AXIS_H + 2} rx={2} />
                            <Handle x={selX0 - 2} y={AXIS_H + DATA_H / 2 - 8} width={4} height={16} rx={2} />
                            <Handle x={selX1 - 2} y={AXIS_H + DATA_H / 2 - 8} width={4} height={16} rx={2} />
                        </>
                    )}
                    {hoverT !== null && !drag && (
                        <HoverLine x1={scale.x(hoverT)} x2={scale.x(hoverT)} y1={AXIS_H - 4} y2={height} />
                    )}
                </g>
            </Svg>
            <Footer>
                <Readout>
                    <Typography as="span" variant="smallBody" color="muted" style={{ whiteSpace: 'nowrap' }}>
                        {hoverT !== null
                            ? describeHover(hoverT, data, promotions)
                            : `Showing ${formatUtc(view[0]).slice(0, 10)} – ${formatUtc(view[1] - 1).slice(0, 10)} UTC (${formatSpan(view[1] - view[0])}). Drag to select; click a day to select it.`}
                    </Typography>
                </Readout>
                <ButtonGroup>
                    <Button appearance="subtle" label="◀" aria-label="Earlier" disabled={view[0] <= domain[0]} onClick={() => pan(-1)} />
                    <Button appearance="subtle" label="−" aria-label="Zoom out" disabled={atAll} onClick={() => zoom(2)} />
                    <Button appearance="subtle" label="+" aria-label="Zoom in" disabled={view[1] - view[0] <= MIN_VIEW} onClick={() => zoom(0.5)} />
                    <Button appearance="subtle" label="▶" aria-label="Later" disabled={view[1] >= domain[1]} onClick={() => pan(1)} />
                    <Button appearance="subtle" label="Selection" disabled={!selection} onClick={() => setView(clampView(zoomTo(selection, domain), domain))} />
                    <Button appearance="subtle" label="All" disabled={atAll} onClick={() => setView(domain)} />
                </ButtonGroup>
            </Footer>
        </Wrapper>
    );
}
