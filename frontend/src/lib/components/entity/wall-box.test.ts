import { describe, expect, it } from 'vitest';
import { boxWords } from './wall-box';

/* Eight pixels a letter, near enough the box's face for the arithmetic. */
const width = (text: string) => text.length * 8;

describe('the words in a wall search box', () => {
	it('asks with the verb where the box has room', () => {
		expect(boxWords('sites', 200, width)).toBe('Search sites');
	});

	it('keeps the noun where the verb would cut it off', () => {
		expect(boxWords('files', 70, width)).toBe('Files');
		expect(boxWords('Photo Sets', 70, width)).toBe('Photo Sets');
	});

	it('says the whole thing before the box has been measured', () => {
		expect(boxWords('loops', 0, width)).toBe('Search loops');
	});
});
