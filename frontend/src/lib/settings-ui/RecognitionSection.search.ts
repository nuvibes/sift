// SPDX-License-Identifier: AGPL-3.0-or-later
/* The three Recognition switches, as a search finds them on Importing.
 *
 * Each switch is a registered setting and is found under its own section (Faces, Smart Search,
 * Watermarks) by the registry's own words. It is also drawn on Importing, in the block beside the
 * other stages, so a search for one lists that door as well. */
import type { Searchable } from './search';

export const SEARCHABLE: Searchable[] = [
	{
		name: 'Recognition switches',
		key: 'importing.recognition',
		section: 'importing',
		help: 'Recognizing faces, describing files for Smart Search and reading watermarks, each turned on or off in one place.',
		keywords:
			'recognize recognition faces face smart search natural language similarity describe watermarks watermark site on off switch identify'
	}
];
