// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Documentation pane's words, and what somebody can type to find it. */
import type { Searchable } from './search';

export const COPY = {
	contents: 'Documentation',
	missing: "That page isn't in this version's documentation.",
	failed: "The documentation couldn't be loaded. Reload the page to try again."
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: 'Documentation',
		section: 'documentation',
		help: 'How to use Sift, with a page for every screen and setting. It needs no internet.',
		keywords: 'docs help guide manual how to instructions read learn pages get started offline'
	}
];
