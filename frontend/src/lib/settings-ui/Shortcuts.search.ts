// SPDX-License-Identifier: AGPL-3.0-or-later
/* The keyboard list. It is a table of what the keys already do rather than anything to change, and
 * a search for a key name has to reach it. */
import type { Searchable } from './search';

export const SEARCHABLE: Searchable[] = [
	{
		name: 'Keyboard shortcuts',
		key: 'shortcuts.keys',
		section: 'shortcuts',
		help: 'Every key Sift listens for.',
		keywords: 'keys hotkeys keybindings ctrl space escape arrow what does this key do'
	}
];
