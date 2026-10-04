/* The old address of a username's page: a redirect to the Files wall filtered to it. */
import { describe, expect, it } from 'vitest';

import { load } from './+page';

function whereTo(id: string): { status: number; location: string } {
	try {
		load({ params: { id } });
	} catch (thrown) {
		return thrown as { status: number; location: string };
	}
	throw new Error('the old address drew a page instead of moving on');
}

describe('the old address of a username', () => {
	it('lands on the Files wall narrowed to that username', () => {
		expect(whereTo('ac1')).toMatchObject({ status: 308, location: '/browse?username=ac1' });
	});

	it('carries an id that is not a plain word without breaking the address', () => {
		expect(whereTo('a c/1').location).toBe('/browse?username=a%20c%2F1');
	});
});
