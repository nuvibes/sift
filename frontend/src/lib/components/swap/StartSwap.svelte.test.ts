/*
 * Start a swap's facial fingerprints sheet: somebody whose fingerprints will not go is in the
 * sheet, wearing the refused state with its reason, as on the people sheet beside it.
 */

import { describe, expect, it } from 'vitest';
import type { KnownPerson } from '$lib/people/faces.svelte';
import { noServerAt } from '../../../test-setup';
import { fingerprintChoices } from './StartSwap.svelte';

/* A person's row asks once for the shipped creator pictures, which this file is not about. */
noServerAt('/api/creator-art');

function known(
	overrides: Partial<KnownPerson> & { keep_local?: boolean; keep_from_swaps?: boolean }
) {
	return {
		id: '01HX0000000000000000000841',
		name: 'Wren Hale',
		faces: 3,
		starters: 0,
		art: null,
		cover_asset_id: null,
		cover_upload_id: null,
		cover_at_ms: null,
		cover_frame: null,
		cover_track_id: null,
		...overrides
	} as KnownPerson;
}

describe('the facial fingerprints sheet', () => {
	it('draws somebody kept out of swaps refused, with the reason, and the rest as they are', () => {
		const rows = fingerprintChoices([
			known({ id: 'a', name: 'Wren Hale' }),
			known({ id: 'b', name: 'Odo Venne', keep_from_swaps: true }),
			known({ id: 'c', name: 'Ilsa Moor', keep_local: true })
		]);
		expect(rows.map((row) => [row.name, row.refused ?? null])).toEqual([
			['Wren Hale', null],
			['Odo Venne', "Kept out of swaps, so it isn't offered"],
			['Ilsa Moor', "Kept local, so it isn't offered"]
		]);
	});

	it("marks somebody under twenty confirmed faces with their page's band, in words", () => {
		const rows = fingerprintChoices([
			known({ id: 'a', name: 'Wren Hale', faces: 3, verdict: 'weak' }),
			known({ id: 'b', name: 'Odo Venne', faces: 12, verdict: 'good' }),
			known({ id: 'c', name: 'Ilsa Moor', faces: 40, verdict: 'strong' })
		]);
		expect(rows.map((row) => [row.name, row.strength ?? null])).toEqual([
			[
				'Wren Hale',
				{ band: 'weak', said: 'Only 3 confirmed faces: Sift recognizes them less surely' }
			],
			[
				'Odo Venne',
				{ band: 'good', said: 'Only 12 confirmed faces: Sift recognizes them less surely' }
			],
			['Ilsa Moor', null]
		]);
	});

	it('leaves out somebody with no faces of their own', () => {
		expect(fingerprintChoices([known({ faces: 0 })])).toEqual([]);
	});
});
