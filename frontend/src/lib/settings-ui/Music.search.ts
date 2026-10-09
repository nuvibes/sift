// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Music pane's words, and what somebody can type to find what it adds. */
import type { Searchable } from './search';

export const COPY = {
	/* The lookup task's row, drawn while the lookup is on. */
	lookup: {
		name: 'When songs are looked up on AcoustID',
		label: 'When songs are looked up',
		owed: (files: number) =>
			`${files.toLocaleString()} ${files === 1 ? 'file has' : 'files have'} a music fingerprint and no song yet.`,
		/* Files AcoustID was asked about and did not know. Asked again only by Ask again below. */
		notKnown: (files: number) =>
			`AcoustID didn't know the song in ${files.toLocaleString()} ${files === 1 ? 'file' : 'files'}.`,
		/* Its presses are on Tasks, like every task's: this says where. */
		runIn: 'Run it in'
	},
	/* Asking AcoustID again about the files it did not know. */
	again: {
		name: 'Ask AcoustID again',
		label: 'Ask AcoustID again',
		action: 'Ask again',
		help: (files: number, days: number) =>
			files > 0
				? `${files.toLocaleString()} ${files === 1 ? 'file' : 'files'} AcoustID didn't know can be asked again. A file waits ${days} days after it was last asked, because AcoustID learns new songs over weeks. A file's own menu asks about it at any time.`
				: `No file is waiting to be asked again. A file AcoustID didn't know waits ${days} days after it was last asked, because AcoustID learns new songs over weeks. A file's own menu asks about it at any time.`
	},
	lede: "Sift can recognize the song in a video by its sound. It matches files that use the same song, and can look up the song's name for you.",
	fingerprints: {
		heading: 'Music fingerprints',
		help: "A fingerprint is a short summary of a file's sound, made on this device. It's what lets Sift match files that share a song, and nothing is sent anywhere to make one.",
		when: {
			name: 'When music fingerprints are made',
			label: 'When it runs',
			help: 'Choose when new files get their music fingerprints, or run it now.'
		}
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.lookup.name,
		key: 'music.acoustid-when',
		section: 'music',
		keywords: 'music song names acoustid lookup look up schedule when run'
	},
	{
		name: COPY.again.name,
		key: 'music.acoustid-again',
		section: 'music',
		keywords: 'music song names acoustid ask again unknown not known retry look up'
	},
	{
		name: COPY.fingerprints.when.name,
		key: 'music.fingerprints',
		section: 'music',
		help: COPY.fingerprints.when.help,
		keywords: 'music song track audio sound fingerprint chromaprint acoustid match schedule when'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.fingerprints.heading,
		section: 'music',
		keywords: 'music fingerprints acoustid identify songs tracks'
	}
];
