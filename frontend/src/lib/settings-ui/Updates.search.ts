// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Updates pane's words, and what somebody can type to find them.
 *
 * ONE COPY MODULE PER PANE. `Updates.svelte` draws every word it adds from `COPY`, and the search
 * entry below is built from the same object, so the pane and a search result cannot say different
 * things. The registered hidden notice is the registry's copy, which feeds the search by itself,
 * except that it is drawn here as the action its value stands for, under the registry's own label.
 *
 * Installing a new version is not a preference, so nothing registered describes it, and neither are
 * the version running and the licence and source at the pane's foot: those are what this pane adds
 * to the index. `About` stays a word that finds the version. */
import type { Searchable } from './search';

export const COPY = {
	thisCopy: 'This copy of Sift is',
	elsewhere: 'The library you are looking at is on another device, running',
	onlyHere:
		"Updating here changes only this device. The library keeps its own version until it's updated on the device it lives on.",
	running: 'You are running',
	fromSource: 'from source',
	available: (version: string) => `Sift ${version} is available.`,
	newest: 'This is the newest version.',
	unchecked: "Sift hasn't been able to check for a newer version. Everything else works as normal.",
	lastChecked: (when: string) => `Last checked ${when}.`,
	/* The one switch over the update check, and its press. */
	checking: {
		label: 'Check for new versions automatically',
		on: 'You hear about a new version within a few hours. The check reads a public list and sends nothing about you or your library.',
		off: 'Sift checks only when you press Check now, and makes no other request to the internet.',
		now: 'Check now'
	},
	notes: "What's new",
	install: {
		name: 'How to update',
		help: 'Download and install a new version of Sift.'
	},
	app: 'Sift can download this for you. It checks that the download came from Sift before anything opens, and then the installer asks you to confirm. Your library is untouched: nothing is moved, renamed or deleted, and your tags, ratings and collections stay as they are.',
	downloading: 'Downloading\u2026',
	downloadInstall: (version: string) => `Download and install Sift ${version}`,
	windowsWarns: {
		before:
			"Windows may warn you that it doesn't recognize the file. That's expected: the installer isn't signed with a paid certificate, and a new one has no reputation yet. Choose",
		moreInfo: 'More info',
		and: 'and then',
		runAnyway: 'Run anyway',
		after:
			'. Sift checks the download against its own signature first, which is what proves where it came from.'
	},
	elsewhereInstall:
		'Install it from the Sift app on the device that runs this library. Your library is untouched: nothing is moved, renamed or deleted.',
	/* Installing on the computer running Sift, from another computer, where the Sift app there
	   answers through the server. */
	there: {
		says: (machine: string | null) =>
			`Sift can download this on ${machine ?? 'the computer running Sift'} for you. It checks that the download came from Sift, then opens the installer there. Your library is untouched: nothing is moved, renamed or deleted.`,
		action: (version: string, machine: string | null) =>
			`Install Sift ${version} on ${machine ?? 'that computer'}`,
		checking: 'Downloading and checking\u2026',
		agreeThere: (machine: string | null) =>
			`The installer is open on ${machine ?? 'the computer running Sift'}. Confirm it there: it can't be confirmed from here. Sift opens again when it has finished.`,
		slow: "Sift hasn't come back yet. Finish the installer on that computer and reload this page."
	},
	releasePage: 'Read about this release',
	hide: 'Hide this notice for this version',
	hidden: 'You hid this notice. Unhide it below.',
	unavailable: "This section isn't available.",
	/** The block holding the notice somebody hid, while there is one to bring back. */
	notice: 'Update notice',
	/* Said when a search or a link names the hidden notice's row while nothing is hidden. */
	nothingHidden: "No update notice is hidden, so there's nothing to unhide.",
	hiddenRow: {
		label: 'Hidden update notice',
		help: (version: string) =>
			`You hid the notice about version ${version}. Unhide it to read it again.`,
		button: 'Unhide'
	},
	refusals: {
		unreachable: "Sift couldn't reach the release page. Check your connection and try again.",
		incomplete: 'That release has no installer yet. Try again shortly.',
		unverified:
			"That release didn't match Sift's signature, so nothing was installed. Try again, and if it happens twice, don't install it by hand.",
		none: "That release isn't newer than this copy of Sift, so nothing was installed.",
		failed: "The installer downloaded but wouldn't open"
	},
	opening: 'The installer is opening. Follow it and Sift restarts on the new version.',
	/* The version, for whoever the check's answer is not drawn for, while it is being read. */
	readingVersion: 'Checking\u2026',
	/* The id a swap tells another Sift about this install, and its reset. */
	deviceId: {
		heading: 'Swaps',
		help: 'A swap sends files to another Sift, and brings theirs here, through one of your tunnels. The other Sift is shown this id.',
		label: 'Your device id',
		locked: "Your saved keys are locked, so there's no device id to show yet.",
		notYet: 'Sift creates it the first time you start or join a swap.',
		copy: 'Copy',
		copied: 'Device id copied',
		more: 'More for your device id',
		reset: {
			press: 'Reset device id',
			title: 'Reset device id?',
			consequence: 'Swaps you start after this show a new id.'
		},
		wasReset: 'Device id reset',
		cannotLoad: "Your device id couldn't be loaded. Reload the page to try again.",
		cannotReset: "The device id couldn't be reset."
	},
	license: {
		heading: 'License and source code',
		help: 'Sift is free software. You are free to run it, study it, change it and share it, and anyone you run it for gets the same freedoms.',
		label: 'License',
		name: 'GNU AGPL-3.0-or-later',
		source: 'Source code',
		notices: 'Third-party notices',
		noticesHelp:
			'The tools Sift comes with, and the code it builds on, each under its own license.',
		noticesLink: 'Read the notices'
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: 'Version',
		key: 'updates.version',
		section: 'updates',
		help: 'Which version of Sift is running.',
		keywords: 'about sift version build number release running installed'
	},
	{
		name: COPY.deviceId.label,
		key: 'updates.device_id',
		section: 'updates',
		help: 'The id a swap shows another Sift, and resetting it.',
		keywords: 'swap device id reset another sift token code share exchange recognized'
	},
	{
		name: COPY.deviceId.heading,
		key: 'updates.swaps',
		section: 'updates',
		help: COPY.deviceId.help,
		keywords: 'swap swaps another sift device id exchange'
	},
	{
		name: COPY.license.heading,
		key: 'updates.license',
		section: 'updates',
		help: COPY.license.help,
		keywords: 'licence license agpl gpl free software open source code github notices terms'
	},
	{
		name: COPY.checking.label,
		key: 'updates.check_for_new_versions',
		section: 'updates',
		help: COPY.checking.on,
		keywords: 'update check automatic internet outbound request offline silent version release'
	},
	{
		name: COPY.install.name,
		section: 'updates',
		help: COPY.install.help,
		keywords: 'update upgrade new version install installer release download latest'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: "What's new",
		key: 'notes-heading',
		section: 'updates',
		keywords: 'whats new release notes changelog changes'
	},
	{
		name: COPY.notice,
		key: 'updates.notice',
		section: 'updates',
		keywords: 'update notice tell me new version'
	},
	{
		name: COPY.license.label,
		section: 'updates',
		keywords: 'license agpl open source terms'
	},
	{
		name: COPY.license.source,
		section: 'updates',
		keywords: 'source code github repository'
	},
	{
		name: COPY.license.notices,
		section: 'updates',
		keywords: 'third party notices licenses dependencies credits'
	}
];
