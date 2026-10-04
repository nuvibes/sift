/* The words the Stash summary is read in: every count a person reads is grouped the same way. */

import { expect, it } from 'vitest';
import { COPY } from './Backup.search';

it('groups the pictures inside zip files like every other count', () => {
	expect(COPY.switcher.stash.images(200000, 150000)).toBe(
		'200,000 pictures, 150,000 of them inside zip files, which are found where they are'
	);
});

it('names only the kinds that have favorites, and says so when none do', () => {
	expect(COPY.switcher.stash.favorites(120, 0, 0)).toBe(
		'Favorites on 120 People, which stay favorites'
	);
	expect(COPY.switcher.stash.favorites(120, 30, 2)).toBe(
		'Favorites on 120 People, 30 Sites and 2 Tags, which stay favorites'
	);
	expect(COPY.switcher.stash.favorites(0, 0, 0)).toBe('No favorites to bring across');
});

it('says one gallery, one marker and one matching folder in the singular', () => {
	const stash = COPY.switcher.stash;
	expect(stash.galleries(1)).toBe('1 gallery, which becomes a Photo Set');
	expect(stash.galleries(2)).toBe('2 galleries, which become Photo Sets');
	expect(stash.markers(1)).toBe('1 marker, which becomes a Loop on its video');
	expect(stash.markers(1200)).toBe('1,200 markers, which become Loops on their videos');
	expect(stash.matched(1)).toMatch(/^1 of its folders matches a folder in this library by name\./);
	expect(stash.matched(3)).toMatch(/^3 of its folders match folders in this library by name\./);
});

/* The paragraph over the pane says what a person needs before pressing, and leaves to each row what
   that row says beside its switch. */
it('keeps the backup paragraph to what is in it, what is not, how it comes back and the password', () => {
	const said = [COPY.holds, COPY.notMedia, COPY.mediaYours, COPY.comesBack].join(' ');
	for (const fact of [
		'People',
		'faces you confirmed',
		"doesn't copy your media files",
		'backing them up is up to you',
		'restore the file and scan your folders again',
		'admin password'
	])
		expect(said).toContain(fact);
	expect(said).not.toContain('Include detected face pictures');
	expect(said.split(/\s+/).length).toBeLessThan(90);
});
