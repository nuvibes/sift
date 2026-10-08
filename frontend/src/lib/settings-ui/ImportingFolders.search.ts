// SPDX-License-Identifier: AGPL-3.0-or-later
/* One folder answering differently from the rest of the library: its words, and what somebody can
 * type to find it.
 *
 * ONE COPY MODULE PER PANE. `ImportingFolders.svelte` draws every word it adds from `COPY`, and the
 * search entry is built from the same object.
 *
 * The controls on that sub-page are built from what the server says a folder may answer, so there
 * is no declared setting for the search index to find, and the row that opens it is a button
 * rather than a preference. Both are why this exists: somebody looking for "don't build previews
 * for this one folder" has a real question and nothing on the pane spells it in those words.
 */
import type { Searchable } from './search';
import { counted } from '$lib/entity/entity-counts';

export const COPY = {
	name: 'Folder-specific import settings',
	help: 'Give one library folder its own settings, or let it follow the default.',
	heading: 'Folder-specific import settings',
	intro:
		"Any folder can have its own settings. For example, a scratch folder that never needs previews, or one library where faces are recognized and another where they aren't.",
	pageHelp:
		'A setting left on Follow the default uses the setting under Import tasks, and changes when that does.',
	follow: 'Follow the default',
	on: 'On',
	off: 'Off',
	follows: 'Follows the default',
	none: "No library folders yet. Add one on Folders, and it's listed here with its own settings.",
	own: (n: number) => `${counted(n)} ${n === 1 ? 'setting' : 'settings'} of its own`,
	/** The fold the folders wait behind, so the pane reads as its own rows first: how many, as
	 *  every fold on a pane says ("Show all 26 Sites"). */
	fold: (n: number) =>
		n === 1 ? 'Show the library folder' : `Show all ${counted(n)} library folders`,
	edit: 'Edit',
	/** The Edit button's accessible name: one per folder, so a screen reader can tell them apart. */
	editNamed: (folder: string) => `Edit ${folder}`
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.name,
		key: 'importing.folders',
		section: 'tasks',
		help: COPY.help,
		keywords:
			'folder root per-folder override exclude skip only this library scratch different answer'
	}
];
