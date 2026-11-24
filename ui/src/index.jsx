import React from 'react';
import layout from '@splunk/react-page/18';
import { getUserTheme } from '@splunk/splunk-utils/themes';
import DataPromotion from './DataPromotion';
import { fetchThemePreference } from './lib/api';
import { resolveColorScheme } from './lib/promotion';

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
        layout(<DataPromotion />, { theme, pageTitle: 'Data Promotion', hideFooter: true });
    })
    .catch((e) => {
        const el = document.createElement('pre');
        el.textContent = `Failed to load the Data Promotion page: ${e}`;
        document.body.appendChild(el);
    });
