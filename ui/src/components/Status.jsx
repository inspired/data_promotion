import React from 'react';
import styled from 'styled-components';
import ArrowsCircularDouble from '@splunk/react-icons/ArrowsCircularDouble';
import CheckCircle from '@splunk/react-icons/CheckCircle';
import ControlStopCircle from '@splunk/react-icons/ControlStopCircle';
import CrossCircle from '@splunk/react-icons/CrossCircle';
import ExclamationTriangle from '@splunk/react-icons/ExclamationTriangle';
import Hourglass from '@splunk/react-icons/Hourglass';
import QuestionCircle from '@splunk/react-icons/QuestionCircle';
import { variables } from '@splunk/themes';
import { agentStatusMeta, statusMeta, taskStatusMeta } from '../lib/promotion';

/** Tone -> theme content color (readable on the page and table backgrounds, light and dark). */
export const TONE_COLOR = {
    positive: variables.contentColorPositive,
    warning: variables.contentColorWarning,
    negative: variables.contentColorNegative,
    info: variables.contentColorInfo,
    neutral: variables.contentColorMuted,
};

const ICONS = {
    ok: CheckCircle,
    warning: ExclamationTriangle,
    error: CrossCircle,
    progress: ArrowsCircularDouble,
    waiting: Hourglass,
    stopped: ControlStopCircle,
    unknown: QuestionCircle,
};

const Wrap = styled.span`
    display: inline-flex;
    align-items: center;
    gap: ${variables.spacingXSmall};
    white-space: nowrap;
    color: ${variables.contentColorDefault};
`;
const IconSlot = styled.span`
    display: inline-flex;
    color: ${(p) => TONE_COLOR[p.$tone] || TONE_COLOR.neutral};
    font-size: 16px;
`;

/** Status as colored icon + text: the status pattern of the Design System Table. */
export function StatusIndicator({ meta }) {
    const Icon = ICONS[meta.icon] || ICONS.unknown;
    return (
        <Wrap data-status-tone={meta.tone}>
            <IconSlot $tone={meta.tone} aria-hidden="true"><Icon variant="filled" /></IconSlot>
            {meta.label}
        </Wrap>
    );
}

export const JobStatus = ({ status }) => <StatusIndicator meta={statusMeta(status)} />;
export const TaskStatus = ({ status }) => <StatusIndicator meta={taskStatusMeta(status)} />;
export const AgentStatus = ({ status }) => <StatusIndicator meta={agentStatusMeta(status)} />;
