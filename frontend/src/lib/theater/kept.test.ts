import { afterEach, describe, expect, it } from 'vitest';

import { storeWall, takeStoredWall, type KeptWall } from './kept';
import type { Playable, SavedCell } from './cell.svelte';

const CELL = { source: 'tag:cars', media_kind: 'video_gif' } as SavedCell;

function wall(): KeptWall {
	return {
		preset: { layout: 'single', shape: { rows: 1, cols: 1, slots: [] }, strip: 0, cells: [CELL] },
		playing: [
			{ id: 'k', at: 9, seed: 3, file: { id: 'k', original_filename: 'beach.mp4' } as Playable }
		],
		focused: 0,
		vaultOpen: true
	};
}

afterEach(() => sessionStorage.clear());

describe('the wall kept in the tab', () => {
	it('comes back with ids and positions, and no file record', () => {
		storeWall('a', wall());
		expect(sessionStorage.getItem('sift.theater.kept.a'), 'a file name was stored').not.toContain(
			'beach'
		);

		const back = takeStoredWall('a');

		expect(back?.playing).toEqual([{ id: 'k', at: 9, seed: 3 }]);
		expect(back?.preset.cells).toEqual([CELL]);
		expect(back?.vaultOpen, 'the server judges each file again').toBe(false);
		expect(takeStoredWall('a'), 'it was read twice').toBeNull();
	});

	it('is read only by the account that wrote it', () => {
		storeWall('a', wall());

		expect(takeStoredWall('b')).toBeNull();
	});

	it('is forgotten when told to', () => {
		storeWall('a', wall());
		storeWall('a', null);

		expect(takeStoredWall('a')).toBeNull();
	});

	it('refuses what it cannot read rather than handing a cell half a wall', () => {
		const key = 'sift.theater.kept.a';
		for (const raw of ['{', '[]', '{"preset":{"layout":"x","cells":[null]},"playing":[]}']) {
			sessionStorage.setItem(key, raw);
			expect(takeStoredWall('a'), raw).toBeNull();
		}
		sessionStorage.setItem(key, '{"preset":{"layout":"x","cells":[]},"playing":[{"id":""}, 4]}');
		expect(takeStoredWall('a')?.playing).toEqual([null, null]);
	});
});
