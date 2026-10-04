/* The decision record's old address: a redirect to History, showing the decisions alone. */
import { describe, expect, it } from 'vitest';

import { landingFor } from '$lib/jobs/tabs';
import { load } from './+page';

function whereTo(): { status: number; location: string } {
	try {
		load();
	} catch (thrown) {
		return thrown as { status: number; location: string };
	}
	throw new Error('the old address drew a page instead of moving on');
}

describe('the old address of the decision record', () => {
	it('moves for good to History, landing on its Decisions', () => {
		const went = whereTo();
		expect(went.status).toBe(308);
		expect(went.location).toBe('/settings/tasks#activity.decisions');
		const url = new URL(went.location, 'http://sift.invalid');
		expect(landingFor(url.search, url.hash)).toEqual({ tab: 'history', decisions: true });
	});
});
