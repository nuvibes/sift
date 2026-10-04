// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Backup pane's words, and what somebody can type to find them.
 *
 * ONE COPY MODULE PER PANE. `Backup.svelte` draws every word it adds from `COPY`, and the search
 * entries below are built from the same objects, so a heading cannot read one way on the screen
 * and another in a search result. The registered rows (how many to keep, the folder, the detected
 * faces, and the schedule drawn on Tasks) are not here: the registry is their copy, and it feeds
 * the search index by itself.
 *
 * Taking a backup, restoring one and opening another library are not preferences stored anywhere,
 * so the registry could not describe them. The Database Switcher is not a preference stored in any
 * one library either (which library is open is a fact about the running Sift); the words
 * "Libraries on this computer" find it here too. */
import { counted } from '$lib/entity/entity-counts';
import type { Searchable } from './search';

/**
 * How the two rules on automatic backups read together, in one sentence somebody can predict the
 * folder from: the newest `keep`, and none older than `days` (zero is never). Said under the two
 * rows and again when they are saved.
 */
export function keptSaid(keep: number, days: number): string {
	const newest =
		keep === 1 ? 'only the newest automatic backup' : `the newest ${keep} automatic backups`;
	if (days <= 0) return `Sift keeps ${newest}, however old.`;
	return `Sift keeps ${newest}, and none older than ${days === 1 ? '1 day' : `${days} days`}.`;
}

export const COPY = {
	/* What a person needs before pressing, once each: what is in a backup, what is not, how a
	   library comes back, and what needs the admin password. A fact a row already says beside its
	   switch (the detected faces) is that row's, not this paragraph's. */
	holds:
		'A backup holds your Sift library: tags, ratings, People, collections and settings. It also holds the faces you confirmed, the covers you uploaded and the fingerprints that link them to your files.',
	notMedia: "It doesn't copy your media files.",
	mediaYours: 'They stay where they are, and backing them up is up to you.',
	comesBack:
		'To get everything back, restore the file and scan your folders again. Thumbnails and previews are generated again from your media. Saved Site cookies and tunnels come back only under the admin password they were saved with.',
	now: {
		name: 'Save a backup now',
		help: 'Save one file with your whole Sift library in the backup folder.',
		row: 'Your whole library, in one file',
		button: 'Save a backup',
		/* Where the press put it, said as the computer running Sift names the folder. */
		savedIn: (folder: string) => `Saved in ${folder}.`,
		/* The Sift app on that computer opens the folder; anywhere else, a copy comes to this device. */
		show: 'Show in folder',
		copy: 'Download a copy',
		copyLabel: (name: string) => `Download a copy of ${name}`
	},
	/** What the next file holds, read from the disk: `parts` is empty until the sizes are in. Both
	 * halves of the press in one sentence: where it goes, and how another device gets a copy. */
	fileHolds: (parts: string) =>
		`One file in the backup folder, beside the automatic backups, which Sift never deletes by itself. It holds the database${parts ? `, plus ${parts}` : ''}. On another device, you can download a copy once it's saved.`,
	automatic: 'Automatic backups',
	notLoaded: "Couldn't load these settings. Refresh the page to try again.",
	howOften: { before: 'Choose how often backups run under', link: 'Tasks', after: '.' },
	besideData:
		"Backups are saved in the same folder as Sift's own data, on the same disk they protect. A folder on another drive survives that disk failing: choose one above, outside your libraries.",
	/* The Backup folder row: what an empty folder means, and the way back to it. */
	folderEmpty: "With Sift's own data",
	folderReset: "Use Sift's own folder",
	apply: {
		label: 'Save backup settings',
		help: 'Saves the settings above together.',
		action: 'Save'
	},
	/** The backups in the folder whose names carry no library's mark. No rule deletes them. */
	unmarked: {
		name: 'Backups Sift leaves alone',
		says: "Sift never deletes these by itself: you saved them, or their names don't say which library made them.",
		action: 'Delete',
		actionLabel: (day: string) => `Delete the backup from ${day}`,
		ask: 'Delete this backup?',
		binned: (name: string) => `${name} goes to the Recycle Bin. You can put it back from there.`,
		forGood: (name: string) =>
			`${name} is deleted permanently: the backup folder's drive has no Recycle Bin.`,
		confirm: 'Delete backup'
	},
	restore: {
		name: 'Restore from a backup',
		row: 'A backup file',
		help: "Restoring replaces your whole Sift library with what is in the file. Your media files aren't touched.",
		choose: 'Choose a backup file'
	},
	confirm: {
		title: 'Restore from this backup?',
		consequence: (file: string) =>
			`Your tags, ratings, People, collections, settings, confirmed faces and uploaded covers are replaced by what is in ${file}. Your media files aren't touched, and this can't be undone.`,
		confirm: 'Restore'
	},
	/**
	 * The libraries on this device, drawn at the foot of this pane by `DatabaseSwitcher.svelte`.
	 * Headed "Libraries", in the word every row under it uses ("Sift opens one library at a time",
	 * "New library", "Duplicate this library") rather than a word for the machinery. "switcher" and
	 * "database" stay in its search words, so either still finds it.
	 */
	switcher: {
		name: 'Libraries',
		help: 'Create a new library, open a different one, or import a database file.',
		lede: 'Sift opens one library at a time. Opening another restarts Sift on it for everyone using it, and signs you out. Nothing is moved or deleted.',
		byHand:
			"This copy of Sift was started by hand, so it can't restart itself on another library. Open the library the way this copy was started.",
		starting: (name: string) => `Sift is starting on ${name}\u2026`,
		open: 'Open',
		marks: {
			current: 'Open right now',
			older: 'Created by an older Sift',
			newer: 'Created by a newer Sift',
			empty: 'Missing',
			unreadable: "Can't be read"
		},
		upgradeAsk: (name: string) => `Open ${name} and upgrade it?`,
		upgradeSays: (name: string) =>
			`${name} was last opened by an older Sift. Opening it upgrades it in one direction; the older Sift can't open it afterwards. Sift saves a backup copy beside it first.`,
		upgrade: 'Open and upgrade',
		cancel: 'Cancel',
		forget: 'Remove from this list',
		/** The door beside a library's Open, and what is behind it. */
		more: (name: string) => `More for ${name}`,
		opensAtStart: 'Opens when Sift starts',
		openAtStart: 'Open this one when Sift starts',
		openLastAtStart: 'Open the last one used instead',
		cannotChoose: "Couldn't save which library opens at start. Try again in a moment.",
		cannotForget: "Couldn't take that library off the list. Try again in a moment.",
		/** Deleting a library in the libraries folder: to the Recycle Bin, its name typed first. */
		remove: {
			begin: 'Delete\u2026',
			ask: (name: string) => `Delete ${name}?`,
			says: (name: string) =>
				`${name} goes to the Recycle Bin, with its database, the faces you confirmed, the covers you uploaded and every picture Sift made for it. The media files it shows you are yours, and they stay exactly where they are.`,
			typeLabel: (name: string) => `Type ${name} to delete it`,
			confirm: 'Delete library',
			done: (name: string) => `${name} is in the Recycle Bin. You can put it back from there.`,
			failed: "Couldn't delete that library. Try again in a moment."
		},
		none: 'No other libraries yet. Create one below.',
		newLabel: 'New library',
		newPlaceholder: 'Name',
		create: 'Create and open',
		createdHelp: 'It starts empty, with you as its admin and the same password.',
		createdIn: 'Created in ',
		chooseFile: 'Choose a database file\u2026',
		chooseSays:
			"A library's own sift.sqlite3 opens where it is. Any other Sift database, such as a backup, is copied into a new library folder beside it, named after the file. The file itself isn't changed.",
		importFile: 'Import a database file',
		/* From another computer: the picker of the computer running Sift opens only on its screen. */
		pickThere: (machine: string | null) =>
			`To choose a database file from ${machine ?? 'that computer'}'s own folders, use the Sift app on ${machine ?? 'the computer running Sift'}. Its file picker opens there and can't be opened from here.`,
		importChoose: 'Choose a file\u2026',
		importName: (file: string) => `Name for the library created from ${file}`,
		importOpen: 'Import and open',
		importSays:
			"A backup or a Sift database becomes a new library of its own, and the library open now isn't changed. The file is uploaded to the device Sift runs on.",
		cannotList: "Couldn't load the list of libraries. Refresh the page to try again.",
		stillSwitching:
			"Sift hasn't finished switching libraries yet. It may still be starting; refresh this page in a moment.",
		cannotSwitch: "Couldn't open that library. Try again in a moment.",
		/** Duplicate this library: a second library made from this one, beside "New library". */
		duplicate: {
			name: 'Duplicate this library',
			help: 'Create a second library from this one, with the same Users and passwords.',
			lede: "Creates a second library from this one: every Tag, rating, Person, Collection, confirmed face and setting, and the same Users and passwords. It's created on this device, beside your other libraries. You stay on this one; open the copy from the list whenever you want.",
			begin: 'Duplicate\u2026',
			nameLabel: 'Name for the copy',
			pictures: 'Copy the pictures Sift made',
			picturesHelp: (amount: string | null) =>
				`${amount === null ? '' : `${amount}. `}Without them, Sift generates the pictures again for the copy, which takes hours.`,
			warning:
				'The copy uses the same folders of files. Moving, renaming or deleting a file from disk in either library changes the real file for both.',
			room: (free: string) => `${free} free on the drive your libraries are on.`,
			start: 'Duplicate',
			cancel: 'Cancel',
			copying: (percent: number) => `Duplicating\u2026 ${percent}%`,
			ready: (name: string) => `${name} is ready. Open it from the list whenever you want.`,
			notFinished: "The copy didn't finish. Activity says why.",
			lost: "Couldn't follow the copy. Activity shows how it's going.",
			cannotMeasure: "Couldn't measure this library. Refresh the page to try again.",
			cannotStart: "Couldn't start the copy. Try again in a moment."
		},
		/** Bringing a Stash library in: read its database, then bring it into this library. */
		stash: {
			name: 'Migrate from Stash',
			help: "Choose Stash's database, stash-go.sqlite, in a folder you've added to Sift. A copy of it is read, and nothing changes until you say so. Stash itself is left as it was.",
			choose: 'Choose\u2026',
			nothingChosen: 'Nothing chosen yet',
			pickerTitle: "Choose Stash's folder",
			pickerList: 'Folders Sift has',
			pickerHelp: 'Click through to the folder that holds stash-go.sqlite, then choose it.',
			pickerAtTop:
				'Click into a folder first: this is the list of folders Sift has, not a folder itself.',
			useFolder: 'Use this folder',
			read: 'Read it',
			readHeading: 'What the Stash library holds',
			readSays: (version: string) => `A Stash library, schema version ${version}. It holds:`,
			people: (n: number) => `${counted(n)} ${n === 1 ? 'person' : 'People'}`,
			sites: (n: number, under: number) =>
				`${counted(n)} ${n === 1 ? 'Site' : 'Sites'}, ${counted(under)} of them part of another`,
			tags: (n: number, one: number, several: number) =>
				`${counted(n)} ${n === 1 ? 'Tag' : 'Tags'}: ${counted(one)} filed under one parent, and ${counted(several)} under several, which come across at the top`,
			scenes: (n: number, rated: number) =>
				`${counted(n)} ${n === 1 ? 'file' : 'files'}, ${counted(rated)} of them rated`,
			images: (n: number, zipped: number) =>
				`${counted(n)} ${n === 1 ? 'picture' : 'pictures'}, ${counted(zipped)} of them inside zip files, which are found where they are`,
			galleries: (n: number) =>
				n === 1
					? '1 gallery, which becomes a Photo Set'
					: `${counted(n)} galleries, which become Photo Sets`,
			markers: (n: number) =>
				n === 1
					? '1 marker, which becomes a Loop on its video'
					: `${counted(n)} markers, which become Loops on their videos`,
			intoNew: 'Import into a new library\u2026',
			newName: 'Name for the new library',
			newSays:
				"Sift creates a new library in the same location as this one, with the folders it matched, and opens it. Stash comes across once that library's first scan finishes, so its files are there to fill in.",
			create: 'Create and import',
			defaultName: 'From Stash',
			opening: (name: string) => `Opening ${name}\u2026`,
			stillOpening:
				"Sift hasn't finished opening the new library yet. It may still be starting; refresh this page in a moment.",
			cannotCreate: "Couldn't create the new library. Try again in a moment.",
			/* Only the kinds that have any: "0 Sites and 0 Tags" is a count of nothing said twice. */
			favorites: (people: number, sites: number, tags: number) => {
				const kinds = [
					[people, 'People'],
					[sites, 'Sites'],
					[tags, 'Tags']
				].filter(([n]) => Number(n) > 0);
				if (kinds.length === 0) return 'No favorites to bring across';
				const said = kinds.map(([n, kind]) => `${counted(Number(n))} ${kind}`);
				const list =
					said.length === 1 ? said[0] : `${said.slice(0, -1).join(', ')} and ${said.at(-1)}`;
				return `Favorites on ${list}, which stay favorites`;
			},
			matchedIds: (people: number, sites: number, scenes: number) =>
				`${counted(people)} People, ${counted(sites)} Sites and ${counted(scenes)} files matched to a stash-box, whose People and Sites stay linked`,
			filters: (all: number, over: number) =>
				`${counted(all)} saved filters, ${counted(over)} of them over files, which become saved searches where Sift can say them the same way`,
			matched: (n: number) =>
				`${n === 1 ? '1 of its folders matches a folder' : `${counted(n)} of its folders match folders`} in this library by name. Anything else is found by its fingerprint where it can be.`,
			noneMatched:
				'None of its folders match a folder in this library by name, so files are found by their fingerprints alone.',
			/* Everything that stays behind, said in full, one thing a line, under a fold on the pane.
			   Kept to what the run really leaves: a line here is a promise about what it does. */
			notComingFold: 'What stays behind in Stash',
			notComing: [
				'When anything was played or given an O. Stash made most of those dates up, so only the counts come across, with where you left off and how long you watched.',
				"What Stash kept for a file this library doesn't have. It waits, and comes across once the file is in Sift.",
				'The pictures on People, Sites and Tags, unless you choose to bring them.',
				'The cover Stash chose for a file. Sift picks its own.',
				"A gallery's own date, rating, Site, People, Tags, links, chapters and custom fields. Its pictures become a Photo Set with its name.",
				"A group's own picture, date, rating, Site, director, description, Tags, links, custom fields and the groups inside it. Its files become a Collection, in its order.",
				"Stash-box links on files. Sift finds a file's stash-box match itself.",
				"Saved filters over anything but files and pictures, and those Sift can't say the same way.",
				"Stash's own settings, scrapers and plugins, and its ignore auto-tag marks.",
				'A field Sift has no place for, such as a director, a weight or a custom field, isn\'t lost. Sift writes it into the details, on one line that starts "From Stash:".'
			],
			bringIn: 'Import into this library\u2026',
			bringInAgain: 'Import again\u2026',
			ran: (when: string) => `It came across ${when}.`,
			changes:
				"This fills in the files this library holds, with the People, Sites and Tags on them. What Stash kept for a file this library doesn't have waits, and Sift brings it across once that file is here. It never changes what's already written here, and it never adds a file.",
			/** What waits for its file, listed after a run: each one by name and by where Stash had it. */
			waitingFold: (n: number) => `Show all ${counted(n)} waiting for their files`,
			waitingLede:
				'Sift brings each across once its file is here, with what is listed under it. The paths are where Stash had them.',
			waitingNoun: 'waiting',
			/** What one waiting row is: a picture, or else a file (Stash's own word for it is not ours). */
			kindOf: (kind: string) => (kind === 'image' ? 'Picture' : 'File'),
			stars: (n: number) => (n === 1 ? '1 star' : `${counted(n)} stars`),
			markerAt: (title: string, at: string) => (title ? `${title} at ${at}` : `A marker at ${at}`),
			markersAre: (list: string) => `Markers: ${list}`,
			peopleAre: (list: string) => `People: ${list}`,
			sitesAre: (list: string) => `Sites: ${list}`,
			tagsAre: (list: string) => `Tags: ${list}`,
			cannotListWaiting: "Couldn't load what waits for its files. Refresh the page to try again.",
			confirm: 'Import',
			cancel: 'Cancel',
			running: (percent: number) => `Importing\u2026 ${percent}%`,
			finished: 'Finished. Activity says what came across.',
			lost: "Couldn't follow it. Activity shows how it's going.",
			cannotRead: "Couldn't read that file. Try again in a moment.",
			cannotRun: "Couldn't start the import. Try again in a moment."
		}
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.now.name,
		key: 'backup.now',
		section: 'backup',
		help: COPY.now.help,
		keywords: 'backup back up export save copy snapshot archive protect'
	},
	{
		name: COPY.restore.name,
		key: 'backup.restore',
		section: 'backup',
		help: COPY.restore.help,
		keywords: 'restore import recover undo move to another computer migrate'
	},
	{
		name: COPY.switcher.name,
		key: 'backup.switcher',
		section: 'backup',
		help: COPY.switcher.help,
		keywords:
			'switch switcher change libraries library database sqlite file another second archive external drive open browse backup copy new create make empty import upload delete remove recycle bin forget default startup start launch first'
	},
	{
		name: COPY.switcher.duplicate.name,
		key: 'backup.duplicate',
		section: 'backup',
		help: COPY.switcher.duplicate.help,
		keywords: 'duplicate copy clone second library same users test try experiment sandbox'
	},
	{
		name: COPY.switcher.stash.name,
		key: 'backup.stash',
		section: 'backup',
		help: COPY.switcher.stash.help,
		keywords: 'stash migrate migration import move from stash performers studios scenes markers'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.now.row,
		section: 'backup',
		keywords: 'save backup now whole library one file export download copy show folder'
	},
	{
		name: 'Automatic backups',
		section: 'backup',
		keywords: 'automatic backups schedule rotate keep how many every day'
	},
	{
		name: COPY.unmarked.name,
		key: 'backup.unmarked',
		section: 'backup',
		help: COPY.unmarked.says,
		keywords:
			'old older backups more than kept why ten extra unmarked unrecognized another library delete remove clean up folder'
	},
	{
		name: COPY.restore.row,
		section: 'backup',
		keywords: 'restore backup file upload bring back'
	},
	{
		name: COPY.switcher.duplicate.pictures,
		section: 'backup',
		keywords: 'duplicate copy pictures thumbnails previews generated'
	},
	{
		name: COPY.switcher.importFile,
		key: 'backup.import',
		section: 'backup',
		keywords: 'import database file sqlite library open'
	}
];
