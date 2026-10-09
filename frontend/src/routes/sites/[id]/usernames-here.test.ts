/* The usernames on a Site's People tab, filed two ways. */
import { describe, expect, it } from 'vitest';

import type { Username } from '$lib/people/usernames.svelte';

import { bareLine, usernamesHere } from './usernames-here';

function username(over: Partial<Username> = {}): Username {
	return {
		id: 'u1',
		username: 'harlowquin',
		asset_count: 0,
		name_candidates: 0,
		display_name: null,
		url: null,
		site_id: 's1',
		site_name: 'Sunsetter',
		person_id: 'p1',
		person_name: 'Neve Alder',
		number: null,
		number_said: null,
		number_via: null,
		site_icon: null,
		...over
	} as Username;
}

describe("a Site's usernames, filed by person", () => {
	it('keeps a username with nothing under it', () => {
		const filed = usernamesHere([username()]);

		expect(filed.held.has('p1')).toBe(false);
		expect(filed.bare.get('p1')?.map((one) => one.id)).toEqual(['u1']);
	});

	it('files a username that holds files or a number with the lines, and not as bare', () => {
		const filed = usernamesHere([
			username({ id: 'u2', asset_count: 3 }),
			username({ id: 'u3', person_id: 'p2', number: '4411' })
		]);

		expect(filed.held.get('p1')?.map((one) => one.id)).toEqual(['u2']);
		expect(filed.held.get('p2')?.map((one) => one.id)).toEqual(['u3']);
		expect(filed.bare.size).toBe(0);
	});

	it('leaves out a username with no person and the nameless "poster unknown" row', () => {
		const filed = usernamesHere([
			username({ id: 'u4', person_id: null }),
			username({ id: 'u5', username: '  ' })
		]);

		expect(filed.bare.size).toBe(0);
	});

	it('gives a username with no person and something under it a card of its own', () => {
		/* A username nobody has claimed has no person's card to stand under, so without its own
		   filing its files would be on the Files tab and the username on no list at all. */
		const filed = usernamesHere([
			username({ id: 'u6', person_id: null, person_name: null, asset_count: 1 }),
			username({ id: 'u7', person_id: null, person_name: null }),
			username({ id: 'u8', person_id: null, username: ' ', asset_count: 4 })
		]);

		expect(filed.loose.map((one) => one.id)).toEqual(['u6']);
		expect(filed.bare.size).toBe(0);
	});

	it('says the username and that nothing is here', () => {
		expect(bareLine(username({ username: ' harlowquin ' }))).toBe('@harlowquin, no files here');
	});
});
