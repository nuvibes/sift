/* The one row no entity page declares for itself, and when it is left out. */

import { describe, expect, it, vi } from 'vitest';

import { withDelete } from './options';
import type { Verb } from '$lib/components/common/verbs';

const SHARE: Verb = { id: 'share', label: 'Sharing', icon: 'group', run: () => {} };

describe('withDelete', () => {
	it('puts Delete last, so what cannot be undone is not the first thing under the pointer', () => {
		const listed = withDelete([SHARE], true, () => {});

		expect(listed.map((one) => one.id)).toEqual(['share', 'delete']);
	});

	it('marks it destructive, so both surfaces colour it without being told twice', () => {
		expect(withDelete([], true, () => {})[0].destructive).toBe(true);
	});

	it('offers it on a page with nothing else to do, which is most of them', () => {
		expect(withDelete(undefined, true, () => {}).map((one) => one.id)).toEqual(['delete']);
	});

	it('leaves it out where the page offered no way to delete', () => {
		expect(withDelete([SHARE], false, () => {}).map((one) => one.id)).toEqual(['share']);
	});

	it('leaves the rest alone when it leaves Delete out', () => {
		// The others stay while the form is up: taking them away would change the row's width and
		// make the remaining control jump.
		expect(withDelete([SHARE], false, () => {})).toHaveLength(1);
	});

	it('asks the page to delete rather than doing anything itself', () => {
		const asked = vi.fn();

		withDelete([], true, asked)[0].run?.([]);

		expect(asked).toHaveBeenCalledOnce();
	});
});
