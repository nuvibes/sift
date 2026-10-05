// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Faces pane's own words, and its search entries built from the same objects, so a heading on
 * the pane and the result a search finds cannot say two different things. The thresholds around
 * them are registered settings and find themselves; the switch's section and deleting what was
 * collected are not. */
import type { Searchable } from './search';
import { counted } from '$lib/entity/entity-counts';
import type { FaceSettings } from '$lib/people/faces.svelte';
import { NOT_ENOUGH_TO_SAY, onRecord } from '$lib/shell/when';
import { describeWait } from '$lib/jobs/waiting';
import { heldFaces } from '$lib/people/thin-fingerprints';

/** The top of the pane: the switch and the status line. */
const RECOGNIZE_FACES = {
	name: 'Recognize faces',
	help: 'Find and group the faces in your library, so you name a person once.'
};

/** Deleting every face and everything learned from them. */
export const DELETE_FACE_DATA = {
	name: 'Delete face data',
	help: "Delete every face Sift found and everything it learned. Your files aren't touched."
};

/** Who Sift can recognize, and the two ways to teach it many people at once. */
const PEOPLE_KNOWN = {
	name: 'People Sift can recognize',
	help: "Everyone with confirmed faces or starter pictures, so you can check before adding someone. A person with neither isn't listed yet."
};

/* What the table's Confirmed faces column counts, folded under the table: the three facts that
   change what anybody does, said once where the numbers are rather than as a group of its own. */
const CONFIRMED_FACES =
	"A confirmed face is one you confirmed as that person. Sift compares every new face with them, and the colored bar on the person's page is based on them. Faces Recognized by Sift aren't counted here. Once someone has 20 confirmed faces, Sift also learns from the ones it recognizes most surely. Those are marked on that person's Recognized by Sift tab. A face too small or blurred to learn from still gets the name, but Sift doesn't learn from it.";

/* The official word for what Sift learns from a face. The code still says "pack" for the file
   that carries them; the screen never does. */
const FACIAL_FINGERPRINTS = {
	name: 'Facial fingerprints',
	help: 'What Sift learned from the faces of the people it can recognize. They move between Sift libraries as one file, without any of your files.'
};

/* The group is named for what it makes; its one row is the folder import, the address kept. */
const CREATING_FINGERPRINTS = {
	name: 'Creating facial fingerprints',
	help: 'Teach Sift many people together from photos you already keep, one subfolder for each person.'
};

/* Held entries no face in the library matches yet: who, how many faces, and from which file. */
const WAITING_FOR_A_FACE = {
	name: 'Waiting for a matching face',
	help: 'People from a facial fingerprints file or a folder that no face in your library matches yet. Create a person for any of these people now, or remove one.'
};

/**
 * The words the pane writes itself, in one place: the status line, the two pages one level in, and
 * the rows on them that are not registered settings. The registered rows carry their own words.
 */
export const COPY = {
	status: {
		off: 'Turned off. The faces Sift already found are kept.',
		notReady: "On, but the models aren't downloaded yet.",
		notRunning: (problem: string) => `Not running. ${problem}`,
		ready: (device: string) => `Ready. Running on the ${device}.`,
		never: (count: number) =>
			`${count.toLocaleString()} ${count === 1 ? 'file' : 'files'} not scanned for faces yet.`,
		older: (count: number) =>
			`${count.toLocaleString()} ${count === 1 ? 'file was' : 'files were'} scanned with older settings.`,
		last: (when: string) => `Last scan ended ${when}.`,
		lastCanceled: (when: string) => `Last scan was canceled ${when}.`
	},
	more: {
		label: 'More settings',
		help: 'The device and models, the lowest face quality, the longest time on one file, and grouping or scanning everything again.',
		action: 'Edit'
	},
	models: {
		label: 'Models',
		help: "Sift doesn't include the models. It downloads them once from their publisher, or you can copy the files to this device yourself.",
		download: 'Download the models',
		downloading: 'Downloading\u2026',
		bar: 'Downloading the models',
		again: 'Download the models again',
		/** The press beside that row: the row already names what it downloads. */
		againPress: 'Download',
		againHelp: 'The models are on this device. Download them again only if a file is damaged.',
		/* Said when a search or a link lands on a row drawn only once the models are here: the
		   press that brings them is rung in its place. */
		notReady: (row: string) =>
			`\u201c${row}\u201d is shown once the models are on this device. \u201cDownload the models\u201d puts them there.`,
		size: 'The download is a few hundred megabytes. You can leave this screen and follow or cancel it in Activity. A canceled download keeps what arrived, so starting again downloads only the rest.',
		couldNotStart:
			"Couldn't start the download. Check that this device is connected to the internet, or copy the model files to this device yourself."
	},
	sweep: {
		label: 'Identifying all files again',
		cancel: 'Cancel scan',
		canceling: 'Canceling\u2026',
		leave:
			'The scan continues if you leave this screen, and you can follow or cancel it in Activity. Canceling keeps every face already found.',
		/* Said when a search or a link lands on the row while no such scan runs: the row is drawn
		   only during one, and the press that starts one is rung in its place. */
		notRunning:
			'\u201cIdentifying all files again\u201d is shown only while every file is being identified again. \u201cIdentify all files again\u201d starts one.'
	},
	forget: {
		label: 'Face data',
		help: 'Deletes every face Sift found and everything it learned from them, and never touches your files.'
	},
	lists: {
		people: PEOPLE_KNOWN,
		confirmed: CONFIRMED_FACES,
		packs: FACIAL_FINGERPRINTS,
		folder: CREATING_FINGERPRINTS,
		waiting: WAITING_FOR_A_FACE
	},
	pack: {
		/** The line under the export: who the file would carry, recognized and waiting. */
		known: (people: number, waiting = 0) => {
			const more = `${waiting.toLocaleString()} ${waiting === 1 ? 'person' : 'people'}`;
			if (people === 0) {
				return waiting === 0
					? "Sift can recognize nobody yet and nobody is waiting for a matching face, so there's nothing to export."
					: `Sift can recognize nobody yet. The file will carry the ${more} waiting for a matching face.`;
			}
			const known = `Sift can recognize ${people.toLocaleString()} ${people === 1 ? 'person' : 'people'}`;
			if (waiting === 0) return `${known}.`;
			return `${known}, and ${waiting.toLocaleString()} more ${waiting === 1 ? 'is' : 'are'} waiting for a matching face.`;
		},
		/* Said while recognition is off: the switch's registered name goes between, as a link. */
		exportNeedsSwitch: ['Export works while ', ' is on.'] as const,
		couldNotRead: "Couldn't read who Sift can recognize. Reload the page to try again.",
		/** Why a person in the Choose people sheet stays out: the marks a swap refuses too. */
		keptOut: (mark: 'local' | 'swap') =>
			mark === 'local'
				? "Kept local, so they aren't exported"
				: "Kept out of swaps, so they aren't exported",
		/* One row: choosing the file is the press, as with any import. */
		import: 'Add people Sift can recognize from a facial fingerprints file to your library',
		importHelp:
			'Choose a file another Sift library exported. Sift can then recognize the people in it here, and nothing is added to your library. Importing the same file twice adds nothing the second time.',
		importAction: 'Import file',
		export: "Export your library's facial fingerprints",
		exportHelp:
			'Saves what Sift learned about the faces of everyone it can recognize as one file, to import into another Sift library. None of your files go in it.',
		exportAction: 'Export',
		/* The row's press sends everyone; this opens the sheet that chooses, either way round. */
		choose: 'Choose people',
		pickTitle: 'Choose who goes in the export',
		/** The two ways the sheet starts, chosen at its head and remembered for the account. */
		wayLabel: 'Who goes in the file',
		ways: {
			except: 'Include everyone, except the people you pick',
			only: 'Include only the people you pick'
		},
		/** The line under the title: who is in the file as the sheet opens, and what a tick does. */
		pickSubject: (way: 'except' | 'only', everyone: number) =>
			way === 'except'
				? `All ${everyone.toLocaleString()} ${everyone === 1 ? 'person is' : 'people are'} in the file. Untick anyone you want to leave out.`
				: 'Nobody is in the file yet. Tick everyone you want in it.',
		/** The sheet's own press, saying how many the file will carry. */
		pickConfirm: (going: number) =>
			going === 1 ? 'Export 1 person' : `Export ${going.toLocaleString()} people`,
		pictures: 'Include their face pictures',
		picturesHelp:
			"Facial fingerprints are numbers made by one face model, and a Sift set to a different model can't compare them. With the pictures in the file, the other library measures the faces again with its own model, so the file works anywhere. Without them, no picture of a real face leaves this device.",
		/** The saved file's name: the library's, what it holds, and the day it was made. */
		fileName: (library: string, day: string) => `${library} facial fingerprints ${day}.zip`,
		notOne: "That file isn't a file of facial fingerprints.",
		couldNotAdd: "Couldn't add the people from that file.",
		couldNotSave: "Couldn't save the file.",
		nobodyLeft: "Everyone was left out, so there's nothing to export.",
		/* Said after a file taken in while recognition is off: the People are kept and wait. The
		   switch's own registered name goes between the two halves, as a link to it. */
		waitsForSwitch: ["Sift won't recognize anyone until ", ' is on.'] as const,
		busy: 'Importing\u2026'
	},
	folder: {
		/* One row: choosing the folder is the press. */
		import: 'Import a structured folder to create facial fingerprints',
		importHelp:
			'Choose a folder that holds one subfolder of photos for each person, named after them. Sift keeps the face it learns from each photo and never adds the photo to your library. Importing the same folder twice adds nothing the second time.',
		importAction: 'Import folder',
		busy: 'Importing\u2026'
	},
	waiting: {
		/** How many faces, and how many confirmed faces the file said they had: "4 faces",
		 *  "3 confirmed faces", "64 faces of 210 confirmed". See `heldFaces`. */
		faces: heldFaces,
		/** Where an entry came from and when: "From Studio Faces, today". */
		from: (source: string, when: string) => `From ${source}, ${when}`,
		none: 'No one is waiting.',
		/** The box over the list, named for a screen reader; it shows "Type to filter". */
		search: 'Search people waiting for a matching face',
		noMatch: (typed: string) => `No one matches "${typed}".`,
		/** How many the list holds as the box leaves it, as the list above says its own. */
		count: (people: number) => `${people.toLocaleString()} ${people === 1 ? 'person' : 'people'}.`,
		create: 'Create a person',
		created: (name: string) => `Created ${name}.`,
		open: 'Open',
		remove: 'Remove',
		removeTitle: (name: string) => `Remove ${name}?`,
		removeConsequence:
			'Sift forgets their facial fingerprints and stops looking for this person. Nothing in your library changes.',
		couldNotCreate: "Couldn't create that person.",
		couldNotRemove: "Couldn't remove them."
	},
	/** The mark on a person Sift created from facial fingerprints, naming the file or folder. */
	fromFingerprints: (source: string) => `Created from facial fingerprints in ${source}`,
	groups: {
		device: 'Device and models',
		finding: 'Finding faces'
	},
	regroup: {
		label: 'Group faces again',
		help: "Regroups every unnamed face using the settings on this page. Discarded groups stay discarded, and named faces don't change.",
		action: 'Run now',
		busy: 'Running\u2026',
		started: 'Grouping faces again. The new groups appear in Organize shortly.',
		failed: "Couldn't group the faces again"
	},
	starters: {
		label: (people: number) =>
			`Use stash-box pictures as starters for ${people.toLocaleString()} ${people === 1 ? 'person' : 'people'}`,
		help: 'For People linked to a stash-box who have no confirmed faces. Sift keeps up to five of their stash-box pictures that each show one clear face.',
		/* Folded under More about this: how the starters behave once they are there. */
		more: "Sift checks each picture the way it checks an imported folder. A face that looks like the person waits under Needs your input, because Sift never names a face from a starter without you. Sift stops using the starters once you confirm one of the person's faces, or answer No about one.",
		none: 'Everyone linked to a stash-box already has confirmed faces or starter pictures.',
		/* The fold under the row that names them: "Show all 120 people". */
		who: (people: number) =>
			people === 1 ? 'Show this person' : `Show all ${people.toLocaleString()} people`,
		action: 'Run',
		started: (people: number) =>
			`Adding starter pictures for ${people.toLocaleString()} ${people === 1 ? 'person' : 'people'}. Activity shows the task.`,
		failed: "Couldn't add the starter pictures"
	},
	/** How long a scan of every file still has to go, by the one estimate formatter: its own words
	    below the sample, a window after it. Opens a sentence of its own, so it opens with a capital. */
	timeLeft: (seconds: number | null): string => {
		const said = seconds === null ? NOT_ENOUGH_TO_SAY : describeWait(seconds);
		return said.charAt(0).toUpperCase() + said.slice(1);
	},
	rescan: {
		label: 'Identify all files again',
		help: 'Looks for faces in every file again, whatever settings it was identified with. Use it when the results look wrong, not just out of date. It takes as long as the first time.',
		action: 'Identify'
	}
} as const;

/**
 * Where recognition stands, in one line under its switch, every number the server's. Shared by the
 * Faces pane and the Recognition switches on Importing, so the two say the same thing.
 *
 * The backlog is split by reason (a file never looked at, one looked at under older settings), or a
 * library four-fifths unscanned would read like a finished one. The last scan says when it ENDED,
 * and says so when it was canceled: a start time would read a canceled scan as the last look.
 *
 * INTERIM until the wire types are regenerated: the route sends `last_run_canceled` and the
 * generated type does not name it yet, so it is read through a widened type rather than cast away.
 */
export function facesStatus(
	feature: (FaceSettings & { last_run_canceled?: boolean }) | null,
	enabled: boolean,
	device: string
): string | null {
	if (feature === null) return null;
	if (!enabled) return COPY.status.off;
	if (feature.device_problem) return COPY.status.notRunning(feature.device_problem);
	if (!feature.ready) return COPY.status.notReady;
	const parts: string[] = [COPY.status.ready(device)];
	if ((feature.never_scanned ?? 0) > 0) parts.push(COPY.status.never(feature.never_scanned ?? 0));
	if ((feature.scanned_under_older_rules ?? 0) > 0)
		parts.push(COPY.status.older(feature.scanned_under_older_rules ?? 0));
	if (feature.last_run_at) {
		/* The one moment on the wire in milliseconds; every other is seconds. Mid-sentence, so
		   `inline`: "Last scan ended today", never "ended Today". */
		const said = onRecord(feature.last_run_at / 1000, { inline: true });
		parts.push(
			feature.last_run_canceled === true ? COPY.status.lastCanceled(said) : COPY.status.last(said)
		);
	}
	return parts.join(' ');
}

export const SEARCHABLE: Searchable[] = [
	{
		...RECOGNIZE_FACES,
		key: 'faces.scan',
		section: 'faces',
		keywords: 'identify recognition faces people scan start find who'
	},
	{
		name: COPY.more.label,
		key: 'faces.more',
		section: 'faces',
		help: COPY.more.help,
		keywords: 'device gpu cpu models quality regroup rescan advanced expert'
	},
	/* On the page behind More settings, filed under it: the page claims them, so each opens it and
	   rings its row. */
	{
		name: COPY.regroup.label,
		key: 'faces.regroup',
		section: 'faces',
		page: COPY.more.label,
		help: COPY.regroup.help,
		keywords: 'faces group regroup cluster again unnamed'
	},
	{
		name: COPY.rescan.label,
		key: 'faces.rescan',
		section: 'faces',
		page: COPY.more.label,
		help: COPY.rescan.help,
		keywords: 'faces scan rescan again everything from scratch'
	},
	{
		name: COPY.models.again,
		key: 'faces.models',
		section: 'faces',
		page: COPY.more.label,
		help: COPY.models.againHelp,
		keywords: 'faces models download again fresh copy damaged repair'
	},
	{
		...PEOPLE_KNOWN,
		key: 'faces.people',
		section: 'faces',
		keywords: 'import people references who recognize confirmed faces list'
	},
	{
		...FACIAL_FINGERPRINTS,
		key: 'faces.packs',
		section: 'faces',
		keywords: 'facial fingerprints pack import export add share people faces zip move'
	},
	{
		...CREATING_FINGERPRINTS,
		key: 'faces.folder',
		section: 'faces',
		keywords: 'folder import people photos subfolder gallery references create facial fingerprints'
	},
	{
		...WAITING_FOR_A_FACE,
		key: 'faces.waiting',
		section: 'faces',
		keywords: 'held waiting facial fingerprints people file folder no match create remove'
	},
	{
		name: 'Use stash-box pictures as starters',
		key: 'faces.starters',
		section: 'faces',
		help: COPY.starters.help,
		keywords: 'stash-box stashdb fansdb starter pictures references people recognize cover'
	},
	{
		...DELETE_FACE_DATA,
		key: 'faces.forget',
		section: 'faces',
		keywords: 'reset clear delete forget retrain start over recognition wrong'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.pack.export,
		key: 'faces.pack-export',
		section: 'faces',
		keywords:
			'share export facial fingerprints people recognize file another library exclude pictures'
	},
	{
		name: COPY.pack.pictures,
		key: 'faces.pack-pictures',
		section: 'faces',
		help: COPY.pack.picturesHelp,
		keywords: 'export include face pictures crops model facial fingerprints'
	},
	{
		name: COPY.pack.import,
		key: 'faces.pack-import',
		section: 'faces',
		keywords: 'import add facial fingerprints file people recognize'
	},
	{
		name: COPY.folder.import,
		key: 'faces.folder-import',
		section: 'faces',
		keywords: 'import folder photos subfolder per person faces learn'
	},
	{
		name: COPY.sweep.label,
		key: 'faces.sweep',
		section: 'faces',
		keywords: 'scan again every file faces rescan all'
	},
	{
		name: COPY.forget.label,
		section: 'faces',
		keywords: 'face data delete forget remove everything faces'
	},
	{
		name: COPY.models.label,
		section: 'faces',
		keywords: 'models recognition face model download'
	}
];
