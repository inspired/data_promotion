/**
 * Layout primitives. Spacing comes from @splunk/themes variables only, so the page
 * follows the active Splunk theme (light / dark, enterprise / prisma) without any
 * hard-coded colors or sizes.
 */
import styled from 'styled-components';
import { variables } from '@splunk/themes';

export const Page = styled.div`
    display: flex;
    flex-direction: column;
    gap: ${variables.spacingLarge};
    padding: ${variables.spacingLarge} ${variables.spacingXLarge};
`;

export const Stack = styled.div`
    display: flex;
    flex-direction: column;
    gap: ${(p) => (p.$gap === 'small' ? variables.spacingSmall : variables.spacingMedium)};
`;

export const Row = styled.div`
    display: flex;
    flex-wrap: wrap;
    align-items: ${(p) => p.$align || 'flex-start'};
    justify-content: ${(p) => p.$justify || 'flex-start'};
    gap: ${variables.spacingMedium};
`;

/** A form field that grows to fill the row and wraps on narrow screens. */
export const Field = styled.div`
    flex: ${(p) => p.$flex || '1 1 260px'};
    min-width: 0;
`;

/** Aligns a button with fields whose labels sit on top. */
export const ButtonSlot = styled.div`
    align-self: flex-end;
    padding-bottom: ${variables.spacingLarge};
`;

export const SidePanelBody = styled.div`
    display: flex;
    flex-direction: column;
    gap: ${variables.spacingLarge};
    padding: ${variables.spacingXLarge};
    width: 600px;
    max-width: 100vw;
    box-sizing: border-box;
`;

/** Keeps numbers like "1,234 of 5,678" on one line in narrow table cells. */
export const Nowrap = styled.span`
    white-space: nowrap;
`;
