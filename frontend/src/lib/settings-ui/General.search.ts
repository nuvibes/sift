// SPDX-License-Identifier: AGPL-3.0-or-later
/* The General pane's words, and what somebody can type to find the rows it draws itself.
 *
 * General is what the application does on THIS device, and the choices about the whole app that
 * belong to no one feature. The browser choice and the close button keep their own modules beside
 * their components (`LinksOpenIn.search.ts`, `ClosingTheWindow.search.ts`), and so does network
 * sharing (`NetworkSharing.search.ts`); the rest is drawn by the pane itself, so its words are
 * here. The two confirmations keep the row ids they had on Editing, so an old link still rings
 * them. */
import type { Searchable } from './search';

export const COPY = {
	/* In a browser, the three device rows cannot be answered. */
	inABrowser:
		'Which browser links open in, what the close button does and starting with Windows are set in the Sift app on your computer.',
	/* Said when a search or a link lands on a row only the app draws, from a browser. */
	setupInTheApp:
		'Setting up Sift again and restarting it are done in the Sift app on your computer.',
	sharingInTheApp:
		'Sharing this library on your network is set in the Sift app on the computer that holds it.',
	/* The app in client mode: the rows about how this window behaves are this computer's own. */
	thisWindow: {
		heading: (machine: string | null) =>
			machine === null ? 'This window' : `This window, on ${machine}`,
		help: 'Which browser links open in, what the close button does and starting with Windows are set for this device. The computer your library is on keeps its own.'
	},
	startup: 'Starting up',
	startWithWindows: {
		name: 'Start Sift when Windows starts',
		help: "It's off to begin with. When on, Sift starts each time you sign in to Windows, so scans, downloads and sharing carry on without opening it first."
	},
	confirmations: 'Confirmations',
	askDelete: {
		name: 'Ask before deleting from disk',
		help: "When off, Sift deletes from disk straight away, without asking a second time. The delete sheet's own box turns this off; this turns it back on."
	},
	askRemove: {
		name: 'Ask before removing from a file',
		help: "When off, the cross on a person, Site, collection, Photo Set or tag chip removes it from the file straight away. The chip's own box turns this off; this turns it back on, wherever you sign in."
	},
	location: {
		name: 'Library location',
		help: "Whether Sift keeps the library on this device or connects to another device that does. Changing it doesn't move or delete anything; your library stays where it is."
	},
	again: {
		label: 'Run setup again',
		help: "The next time you open Sift, it asks whether this device keeps the library or connects to another. It doesn't ask where your library is, and nothing is moved or deleted.",
		action: 'Run setup',
		done: 'Sift will ask how to set up the next time it opens'
	},
	restart: {
		label: 'Restart Sift',
		help: 'Sift closes and opens again. After Run setup again, this takes you to the setup questions now.',
		action: 'Restart',
		byHand: 'To see the setup questions now, close Sift and open it again.',
		failed: "Sift couldn't restart itself. Close it and open it again."
	},
	/* The computer running Sift, reached from another computer (the app in client mode, or a
	   browser): what is changed here changes there. */
	server: {
		heading: 'The computer running Sift',
		help: (machine: string | null) =>
			machine === null
				? 'What you change here changes the computer your library is on, not this one.'
				: `What you change here changes ${machine}, the computer your library is on, not this one.`,
		restartHelp:
			'Sift closes and opens again on that computer. Everyone using it waits a few seconds, then carries on.',
		restarting: (machine: string | null) =>
			`Sift is restarting on ${machine ?? 'the computer running it'}\u2026`,
		cannot: "Sift couldn't be asked to restart.",
		slow: "Sift hasn't come back yet. Wait a moment, then reload this page.",
		firewall: {
			label: 'Firewall',
			open: (port: number) => `Windows lets other devices reach Sift on port ${port}.`,
			closed: 'Windows is blocking other devices from reaching Sift.',
			unknown: "Sift couldn't ask Windows on that computer.",
			action: 'Open the firewall port',
			waiting: (machine: string | null) =>
				`Waiting for approval on ${machine ?? 'the computer running Sift'}\u2026`,
			approveThere:
				"Approve this on the computer running Sift. Windows shows its own prompt there, and it can't be approved from here.",
			unchanged: "Nothing changed. Windows on that computer didn't get permission to open the port."
		},
		/* The sharing switch of the computer running Sift, from another computer. Its name is the
		   Network sharing section's own (`NETWORK_SHARING.name`). */
		sharing: {
			help: (address: string | null) =>
				address === null
					? 'Other devices on its network can open Sift.'
					: `Other devices on its network open Sift at ${address}.`,
			restarting: (machine: string | null) =>
				`Sift is restarting on ${machine ?? 'the computer running it'} to change who can reach it\u2026`,
			offTitle: 'Stop sharing this library?',
			offSays: (machine: string | null) =>
				`Sift restarts on ${machine ?? 'the computer running it'} and stops answering other devices. This window is one of them, so it can't reach Sift afterwards. Sharing is turned back on in the Sift app on that computer.`,
			offConfirm: 'Stop sharing',
			cutOff: (machine: string | null) =>
				`Sift has stopped sharing. To reach it from here again, turn sharing back on in the Sift app on ${machine ?? 'the computer running Sift'}.`,
			cannot: "Sift couldn't be asked to change sharing."
		}
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		...COPY.startWithWindows,
		key: 'general.start_with_windows',
		section: 'general',
		keywords: 'startup login sign in boot launch automatically autostart windows'
	},
	{
		name: COPY.askDelete.name,
		key: 'editing.delete.ask',
		section: 'general',
		help: COPY.askDelete.help,
		keywords: 'delete confirm confirmation permanent ask again prompt disk remove'
	},
	{
		name: COPY.askRemove.name,
		key: 'editing.remove.ask',
		section: 'general',
		help: COPY.askRemove.help,
		keywords: 'chip cross remove take off confirm ask again prompt tag person'
	},
	{
		name: COPY.location.name,
		key: 'general.library_location',
		section: 'general',
		help: COPY.location.help,
		keywords: 'setup set up again first run mode connect another device client server library'
	},
	{
		name: COPY.restart.label,
		key: 'general.restart',
		section: 'general',
		help: COPY.restart.help,
		keywords: 'restart relaunch reopen close open again reload app'
	},
	{
		name: COPY.server.heading,
		key: 'general.server',
		section: 'general',
		help: COPY.server.help(null),
		keywords: 'server remote other computer client mode restart start with windows host'
	},
	{
		/* No key: the heading is drawn only in the app on another computer, so a result lands on
		   the pane, where the rows it names are drawn wherever this window can answer them. */
		name: COPY.thisWindow.heading(null),
		section: 'general',
		help: COPY.thisWindow.help,
		keywords: 'this device window client mode local app laptop desktop own computer'
	},
	{
		name: COPY.server.firewall.label,
		section: 'general',
		keywords: 'firewall windows port open block other devices network server remote'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.startup,
		section: 'general',
		keywords: 'start startup windows login boot launch open'
	},
	{
		name: COPY.confirmations,
		key: 'general.confirmations',
		section: 'general',
		keywords: 'confirm ask before delete remove dialogs'
	},
	{
		name: COPY.again.label,
		key: 'general.run_setup',
		section: 'general',
		keywords: 'setup first run wizard again start over'
	}
];
