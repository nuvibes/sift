/* Maintenance's figures, grouped the way every count on screen is: never "120000 files" beside a
 * wall saying "8,000 files". The words are `COPY`'s, and the number in each goes through the one
 * formatter (`$lib/entity/entity-counts`). */
import { expect, it } from 'vitest';

import { COPY } from './Maintenance.search';

it('groups every count it says', () => {
	expect(COPY.files(120000)).toBe('120,000 files');
	expect(COPY.files(1)).toBe('1 file');
	expect(COPY.howMany(2400, 'file', 'files')).toBe('2,400 files');
	expect(COPY.howMany(1, 'file', 'files')).toBe('1 file');
	expect(COPY.toFree(COPY.howMany(2400, 'file', 'files'), '300 MB')).toBe(
		'2,400 files \u2014 300 MB to free'
	);
	expect(COPY.rebuild.queued(120000)).toBe('120,000 queued');
	expect(COPY.rebuild.consequence(120000)).toContain('for 120,000 files again');
	expect(COPY.deleted(2400, 'file', 'files')).toBe('2,400 files were deleted.');
	expect(COPY.deleted(1, 'job', 'jobs')).toBe('1 job was deleted.');
	expect(COPY.deletedFrom(2, 'thumbnail', 'thumbnails')).toBe('thumbnails deleted from');
});
