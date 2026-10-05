/*
 * The import press's refusals, in the words Settings > Faces shows for each.
 */
import { expect, it } from 'vitest';

import { ApiError } from '$lib/api/client';
import { foldsOf, refusalOfImport } from './folder-import.svelte';

it('asks for face recognition to be turned on when the server says it is off', () => {
	expect(refusalOfImport(new ApiError(409, 'off', 'Face recognition is off.'))).toBe(
		'Turn on face recognition before importing a folder.'
	);
});

it('asks for the folder in parts when it is more than one import takes', () => {
	expect(refusalOfImport(new ApiError(413, 'too large'))).toBe(
		'That folder is more than Sift imports in one go. Import it in parts.'
	);
});

it("says try again for anything the server did not word, and a 400's detail as it came", () => {
	expect(refusalOfImport(new Error('network'))).toBe("Couldn't import that folder. Try again.");
	expect(refusalOfImport(new ApiError(400, 'bad', 'Not a folder.'))).toBe('Not a folder.');
	expect(refusalOfImport(new ApiError(400, 'bad'))).toBe("Couldn't import that folder. Try again.");
});

it("titles each fold in the report's own words, its count the files under it", () => {
	const left = (near: string[]) => ({
		left_out: [{ reason: 'no_face', words: 'with no face in it', files: ['A/1.jpg', 'B/2.jpg'] }],
		near_copies: near
	});
	expect(foldsOf(left(['A/3.jpg'])).map((one) => one.summary)).toEqual([
		'2 with no face in it',
		'1 photo kept though almost the same as another'
	]);
	expect(foldsOf(left(['A/3.jpg', 'A/4.jpg']))[1]?.summary).toBe(
		'2 photos kept though almost the same as another'
	);
	expect(foldsOf(left([]))).toHaveLength(1);
});
