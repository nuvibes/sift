/** What the live-matrix gate refuses.
 *
 * `scripts/check_live_matrix.js` holds every screen's row in `lib/library/live-matrix.ts` to hearing
 * the bells for what it draws. Each case is a row and a screen written for the purpose, run through
 * exactly the judgement the build runs.
 */

import { describe, expect, it } from 'vitest';

import { bellsOf, judgeRow, rereadsOnOpinion } from '../../../scripts/check_live_matrix.js';

const THINGS = {
	files: ['arrivals', 'libraryChanges'],
	opinions: ['assetState'],
	tags: ['libraryChanges'],
	counts: ['libraryChanges', 'settled']
};

/** A screen made of the given files, read as `bellsOf` reads them. */
function screen(files: Record<string, string>) {
	const read = (path: string) => files[path] ?? '';
	return {
		bells: (file: string, via: string[]) => bellsOf(file, via, read),
		reach: () => new Set(Object.keys(files))
	};
}

const LIST = 'routes/tags/+page.svelte';
const row = { screen: LIST, file: LIST, shows: ['tags', 'counts'] };

describe('the live-matrix gate', () => {
	it('refuses a list that never hears the library', () => {
		const { bells, reach } = screen({ [LIST]: 'let items = $state([]);' });
		expect(judgeRow(row, THINGS, bells, reach)).toMatch(/tags needs libraryChanges/);
	});

	it('passes the list once it re-reads on the library and when files settle', () => {
		const { bells, reach } = screen({ [LIST]: 'reloadOnLibraryChange(reread);' });
		expect(judgeRow(row, THINGS, bells, reach)).toBeNull();
	});

	it('does not count ringing a bell, or naming one in a comment, as hearing it', () => {
		const { bells, reach } = screen({
			[LIST]: '/* whenChanged(libraryChanges, reread) */\nlibraryChanges.changed();'
		});
		expect(judgeRow(row, THINGS, bells, reach)).toMatch(/never hears/);
	});

	it('hears through a file the screen names as keeping it, and only one it imports', () => {
		const wall = { ...row, shows: ['files'], via: ['lib/components/wall-catch-up.svelte.ts'] };
		const kept = screen({
			[LIST]: 'whenChanged(libraryChanges, () => void current.catchUp());',
			'lib/components/wall-catch-up.svelte.ts': 'whenChanged(arrivals, () => void this.catchUp());'
		});
		expect(judgeRow(wall, THINGS, kept.bells, kept.reach)).toBeNull();
		expect(judgeRow(wall, THINGS, kept.bells, () => new Set([LIST]))).toMatch(/does not import/);
	});

	it('holds a wall whose members hang on opinions to re-reading on one', () => {
		const favorites = { ...row, shows: ['opinions'], membership: 'opinions' as const };
		const restyles = screen({
			[LIST]: 'onAssetStateChange((state) => {\n\t\tgrid.setState(state);\n\t});'
		});
		expect(judgeRow(favorites, THINGS, restyles.bells, restyles.reach)).toMatch(/re-reads nothing/);
		expect(
			rereadsOnOpinion(
				'onAssetStateChange((state) => {\n\t\tgrid.setState(state);\n\t\tvoid current.catchUp();\n\t});'
			)
		).toBe(true);
	});

	it('lets a row say what it still owes, and names a thing nobody declared', () => {
		const { bells, reach } = screen({ [LIST]: '' });
		expect(judgeRow({ ...row, owes: ['tags', 'counts'] }, THINGS, bells, reach)).toBeNull();
		expect(judgeRow({ ...row, shows: ['weather'] }, THINGS, bells, reach)).toMatch(/THING_BELLS/);
	});
});
