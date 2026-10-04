// SPDX-License-Identifier: AGPL-3.0-or-later
import { describe, expect, it } from 'vitest';
import { markMissing, markOf } from './marks.svelte';

describe('markOf', () => {
	it('stops drawing an address once one picture of it has failed', () => {
		const unknown = '/api/sites/icons/for?host=unknown.example';
		const known = '/api/sites/icons/for?host=known.example';
		expect(markOf(unknown)).toBe(unknown);
		markMissing(unknown);
		expect(markOf(unknown)).toBeNull();
		expect(markOf(known)).toBe(known);
	});

	it('draws nothing for no address', () => {
		markMissing(undefined);
		expect(markOf(undefined)).toBeNull();
		expect(markOf('')).toBeNull();
	});
});
