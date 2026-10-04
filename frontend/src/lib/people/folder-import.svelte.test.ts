/*
 * The import press's refusals, in the words Settings > Faces shows for each.
 */
import { expect, it } from 'vitest';

import { ApiError } from '$lib/api/client';
import { refusalOfImport } from './folder-import.svelte';

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
