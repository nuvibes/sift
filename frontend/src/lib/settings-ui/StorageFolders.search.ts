// SPDX-License-Identifier: AGPL-3.0-or-later
/* Where Sift keeps its own files, and where a file saved out of Sift lands: the words, and what
 * somebody can type to find them. */
import type { Searchable } from './search';

export const COPY = {
	name: 'Sift data',
	help: 'Sift keeps its data in two folders, separate from your media. Sift is unavailable while they move.',
	library: {
		label: 'Your library',
		help: (folder: string) =>
			`The database and everything that can't be generated again. This is the folder to back up. ${folder}`
	},
	generated: {
		label: 'Generated files',
		help: (folder: string) =>
			`Thumbnails, hover previews and converted copies. If they're deleted, Sift generates them again, and no file is lost. ${folder}`
	},
	move: 'Move both',
	/* In place of a size not measured yet. */
	measuring: 'Measuring',
	moving: 'Moving Sift data',
	moved: 'Sift is using the new folder',
	/* Said when a search or a link lands on either block from a browser, which draws neither. */
	inTheApp:
		'Where Sift keeps its data and where saved files go are set in the Sift app on your computer.',
	/* The two folders of the computer running Sift, from another computer. */
	server: {
		help: (machine: string | null) =>
			`Sift keeps its data in two folders on ${machine ?? 'the computer running Sift'}, separate from your media. Sift is unavailable while they move.`,
		title: 'Move Sift data',
		pickerHelp: 'Click through to an empty folder on that computer, then move both folders there.',
		drives: 'Drives on that computer',
		here: 'Move them here',
		cancel: 'Cancel',
		atTop: 'Click into a folder first: this is the list of drives, not a folder itself.',
		moving: (machine: string | null) =>
			`Sift is moving its data on ${machine ?? 'the computer running it'}. It opens again once the folders are there, which takes as long as copying them.`,
		lastFailed: (reason: string | null) =>
			reason === null ? "The last move didn't finish." : `The last move didn't finish. ${reason}`,
		cannot: "Sift couldn't be asked to move its data.",
		slow: "Sift hasn't come back yet. A large library takes a while to copy. Reload this page in a few minutes."
	},
	saveFolder: {
		name: 'Saved files',
		lede: 'Ctrl+S on whatever you are watching, and every Save to device, saves the file here without asking where.',
		label: 'Save files to',
		help: 'Your Downloads folder unless you choose another. Remembered on this device only.',
		disclosure: 'If the folder no longer exists, files go to Downloads instead.',
		choose: 'Choose',
		reset: 'Reset to Downloads'
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.name,
		key: 'library.storage_folders',
		section: 'library',
		help: COPY.help,
		keywords:
			"storage location move disk drive space thumbnails previews database path where sift's files are kept"
	},
	{
		name: COPY.saveFolder.label,
		key: 'library.save_folder',
		section: 'library',
		help: COPY.saveFolder.help,
		keywords: 'save to device ctrl+s saved files folder location keep a copy export'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.library.label,
		section: 'library',
		keywords: 'your library folders where files are'
	},
	{
		name: COPY.generated.label,
		section: 'library',
		keywords: 'generated files thumbnails previews cache where'
	},
	{
		name: 'Saved files',
		section: 'library',
		keywords: 'saved files screenshots clips save folder'
	}
];
