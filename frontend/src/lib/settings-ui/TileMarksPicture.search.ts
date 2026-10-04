// SPDX-License-Identifier: AGPL-3.0-or-later
/* The picture of what each mark on a tile means, and the controls that change them. */
import type { Searchable } from './search';

/** The block on Appearance, by the words on its heading and in a search result alike. */
export const TILE_MARKS = {
	name: 'Marks on a tile',
	help: 'Which marks appear on a file, and when.'
};

export const SEARCHABLE: Searchable[] = [
	{
		...TILE_MARKS,
		key: 'appearance.tile_marks',
		section: 'appearance',
		keywords: 'badges icons corners heart stars rating overlay hover always never what a tile shows'
	}
];
