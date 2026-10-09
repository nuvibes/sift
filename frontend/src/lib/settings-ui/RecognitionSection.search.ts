// SPDX-License-Identifier: AGPL-3.0-or-later
/* The three Recognition switches, as a search finds them on Importing. */
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
