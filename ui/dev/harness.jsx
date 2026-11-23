import React from 'react';
import { createRoot } from 'react-dom/client';
import { createGlobalStyle } from 'styled-components';
import SplunkThemeProvider from '@splunk/themes/SplunkThemeProvider';
import { pageBase } from '@splunk/themes/mixins';
import DataReplay from '../src/DataReplay';

// Same theming react-page applies in Splunk Web: SplunkThemeProvider + pageBase().
const GlobalStyles = createGlobalStyle`html, body { ${pageBase()} }`;
const q = new URLSearchParams(window.location.search);
createRoot(document.getElementById('root')).render(
    <SplunkThemeProvider family={q.get('family') || 'enterprise'} colorScheme={q.get('scheme') || 'light'}>
        <GlobalStyles />
        <DataReplay />
    </SplunkThemeProvider>
);
