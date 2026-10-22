/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */

/* AI-hint: MiOS-Portal branding prefs, a Surfer copy-patch over the pref/firefox-branding.js that Surfer's branding step generates (it hardcodes zen-browser.app URLs). Rendered by mios-dotfiles-render from mios.toml [desktop.portal.gecko].
   AI-related: usr/share/mios/mios.toml */

// First run and after an image update open the Portal, never a vendor page.
pref("startup.homepage_override_url", "http://localhost:8700/");
pref("startup.homepage_welcome_url", "http://localhost:8700/");
pref("startup.homepage_welcome_url.additional", "");

// The image is the updater (DisableAppUpdate): no download or release-notes pages.
pref("app.update.url.manual", "");
pref("app.update.url.details", "");
pref("app.releaseNotesURL", "");
pref("app.releaseNotesURL.aboutDialog", "");
pref("app.releaseNotesURL.prompt", "");
pref("app.vendorURL", "http://localhost:8700/");

// Number of usages of the web console.
// If this is less than 5, then pasting code into the web console is disabled
pref("devtools.selfxss.count", 5);
