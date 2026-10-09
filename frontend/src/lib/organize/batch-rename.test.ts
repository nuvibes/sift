/* The words the batch rename sheet says about a plan, and where a pressed word lands in the box. */
import { describe, expect, it } from 'vitest';
import { insertWord, rowNote, summary, type RenamePreview } from './batch-rename';

function plan(over: Partial<RenamePreview> = {}): RenamePreview {
	return {
		rows: [],
		total: 10,
		renaming: 10,
		numbered: 0,
		same: 0,
		clashes: 0,
		refused: 0,
		as_task: false,
		words: {},
		...over
	};
}

describe('a pressed word', () => {
	it('lands where the cursor is, a space from the word beside it', () => {
		expect(insertWord('{name}', 6, 6, 'n')).toEqual({ text: '{name} {n}', caret: 10 });
		expect(insertWord('Trip ', 5, 5, 'n')).toEqual({ text: 'Trip {n}', caret: 8 });
		expect(insertWord('{name}', 0, 0, 'date')).toEqual({ text: '{date} {name}', caret: 6 });
	});
});

describe('what the plan will do, in words', () => {
	it('counts what changes, what is numbered and what is left alone', () => {
		expect(summary(plan(), 'number')).toBe('All 10 files get new names.');
		expect(summary(plan({ renaming: 7, numbered: 2, clashes: 2 }), 'number')).toBe(
			'7 of 10 files get new names. 2 names were taken, so they get the next number.'
		);
		expect(summary(plan({ renaming: 7, clashes: 3, refused: 1 }), 'skip')).toBe(
			"7 of 10 files get new names. 3 names are taken, so those files keep theirs. One file can't be renamed."
		);
		expect(summary(plan({ renaming: 0 }), 'number')).toBe('No file gets a new name.');
	});

	it('says why a file does not simply take the new name, and nothing when it does', () => {
		const row = { asset_id: 'a', before: 'a.mp4', after: 'b.mp4', reason: null };
		expect(rowNote({ ...row, state: 'renamed' })).toBeNull();
		expect(rowNote({ ...row, state: 'numbered' })).toBe('Name taken, numbered');
		expect(
			rowNote({
				...row,
				state: 'refused',
				reason: 'This picture is inside an archive, so it keeps its name.'
			})
		).toBe('This picture is inside an archive, so it keeps its name.');
	});
});
