import React from 'react';
import presets from '@splunk/react-time-range/presets';
// Stand-in for SplunkwebConnector outside Splunk Web.
export default function Passthrough({ children, presetsTransform = (p) => p }) {
    return React.cloneElement(children, { presets: presetsTransform(presets) });
}
