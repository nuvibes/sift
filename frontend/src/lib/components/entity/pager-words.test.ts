/* A pager names what its wall holds: a Loops tab counts Loops, not files. */
import { describe, expect, it } from 'vitest';

import { ASSET_SOURCE, LOOP_SOURCE } from '$lib/grid/grid.svelte';
import assetGrid from '../AssetGrid.svelte?raw';

describe('the pager under a wall of files or Loops', () => {
	it('is told what a row is by where the rows come from', () => {
		expect(LOOP_SOURCE.noun).toBe('loops');
		expect(ASSET_SOURCE.noun, 'the pager says files by itself').toBeUndefined();
		expect(assetGrid).toMatch(/<Pager\b(?:(?!\/>)[\s\S])*noun=\{source\.noun\}/);
	});
});
