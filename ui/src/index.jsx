import React from 'react';
import layout from '@splunk/react-page/18';
import { getUserTheme } from '@splunk/splunk-utils/themes';
import DataReplay from './DataReplay';
import { fetchThemePreference } from './lib/api';
import { resolveColorScheme } from './lib/replay';

/**
 * The user's theme from Settings > User preferences (user-prefs `theme`). Splunk's
 * get_theme API reports the page template's theme, which is always light for
 * splunk_ui_app.html pages, so the preference is read directly.
 */
async function userColorScheme() {
    try {
        const pref = await fetchThemePreference();
        if (pref) return resolveColorScheme(pref);
    } catch (e) {
        // fall through to Splunk's theme API
    }
    return resolveColorScheme(await getUserTheme());
}

userColorScheme()
    .then((theme) => {
        layout(<DataReplay />, { theme, pageTitle: 'Data Replay', hideFooter: true });
    })
    .catch((e) => {
        const el = document.createElement('pre');
        el.textContent = `Failed to load the Data Replay page: ${e}`;
        document.body.appendChild(el);
    });
