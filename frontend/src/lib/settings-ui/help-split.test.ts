import { describe, expect, it } from 'vitest';
import { splitHelp } from './help-split';

describe('a row help of one or two sentences', () => {
	it('keeps two sentences as the help and folds the rest', () => {
		const said = splitHelp(
			'Sift still holds a record of these. Removing them is permanent. Plug a drive back in first. A folder removed lately waits.'
		);
		expect(said.help).toBe('Sift still holds a record of these. Removing them is permanent.');
		expect(said.more).toBe('Plug a drive back in first. A folder removed lately waits.');
	});

	it('folds nothing from help that is already short', () => {
		expect(splitHelp('One sentence. And a second.')).toEqual({
			help: 'One sentence. And a second.',
			more: null
		});
	});

	it('does not break inside a figure or after a lower-case word', () => {
		const said = splitHelp('Sift keeps 2.5 GB free. It checks e.g. often. Then it stops. Last.');
		expect(said.help).toBe('Sift keeps 2.5 GB free. It checks e.g. often.');
		expect(said.more).toBe('Then it stops. Last.');
	});
});
