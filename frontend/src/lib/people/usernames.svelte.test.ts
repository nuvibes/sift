/* Usernames, read whole and filed by the card they are drawn under.
 *
 * A username has no page; it is drawn under its Site on a person's Sites tab and under its person
 * on a Site's People tab. Two things decide what those lines say, and both are here: WHICH
 * usernames are lines at all (the ones holding files or a number: the thousands of stash-box
 * profile links are the record's Links, not usernames), and that the read is the WHOLE set rather
 * than the first page of it.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn(), del: vi.fn() }));

vi.mock('$lib/api/client', () => ({ api: { get: mocks.get, put: mocks.put, del: mocks.del } }));

import {
	UsernamesByCard,
	usernames,
	usernamesBy,
	holdsSomething,
	type Username
} from './usernames.svelte';

function username(over: Partial<Username> = {}): Username {
	return {
		size_bytes: null,
		id: 'a-1',
		username: 'esmewrenfield',
		display_name: null,
		asset_count: 3,
		site_id: 's-1',
		site_name: 'SomeSite',
		person_id: 'p-1',
		person_name: 'Neve',
		number: null,
		number_said: null,
		number_via: null,
		site_icon: null,
		url: null,
		name_candidates: 0,
		...over
	};
}

beforeEach(() => {
	vi.clearAllMocks();
});

describe('which usernames are lines', () => {
	it('keeps a username with files, and one with a number, and drops a bare profile link', () => {
		expect(holdsSomething(username({ asset_count: 2 }))).toBe(true);
		expect(holdsSomething(username({ asset_count: 0, number: '51234567' }))).toBe(true);
		expect(holdsSomething(username({ asset_count: 0, number: null }))).toBe(false);
	});

	it('files them by the column asked for, leaving out file-less links and rows with no key', () => {
		const filed = usernamesBy(
			[
				username({ id: 'a-1', site_id: 's-1' }),
				username({ id: 'a-2', site_id: 's-1', asset_count: 0 }),
				username({ id: 'a-3', site_id: 's-2', asset_count: 0, number: '7' }),
				username({ id: 'a-4', site_id: null })
			],
			'site_id'
		);

		expect([...filed.keys()]).toEqual(['s-1', 's-2']);
		expect(filed.get('s-1')?.map((one) => one.id)).toEqual(['a-1']);
		expect(filed.get('s-2')?.map((one) => one.id)).toEqual(['a-3']);
	});

	/* The Site's "poster unknown" row holds files and has no name: its files stay filed, and it is
	   never listed under a card on a Site's People tab or a person's Sites tab. */
	it('never lists a username with no name, even one holding files', () => {
		const rows = [
			username({ id: 'a-1', person_id: 'p-1' }),
			username({ id: 'blank', username: '', person_id: 'p-1', asset_count: 687 }),
			username({ id: 'spaces', username: '  ', site_id: 's-1', asset_count: 4 })
		];

		expect(
			usernamesBy(rows, 'person_id')
				.get('p-1')
				?.map((one) => one.id)
		).toEqual(['a-1']);
		expect(
			usernamesBy(rows, 'site_id')
				.get('s-1')
				?.map((one) => one.id)
		).toEqual(['a-1']);
	});
});

describe('reading every username', () => {
	it('pages until the total is reached, at the route ceiling', async () => {
		mocks.get
			.mockResolvedValueOnce({
				items: Array.from({ length: 200 }, (_, at) => username({ id: `a${at}` })),
				total: 201
			})
			.mockResolvedValueOnce({ items: [username({ id: 'last' })], total: 201 });

		const all = await usernames.allOf({ siteId: 's-1' });

		expect(all).toHaveLength(201);
		expect(mocks.get).toHaveBeenNthCalledWith(1, '/usernames', {
			query: { site_id: 's-1', limit: '200', offset: '0' }
		});
		expect(mocks.get).toHaveBeenNthCalledWith(2, '/usernames', {
			query: { site_id: 's-1', limit: '200', offset: '200' }
		});
	});

	it('stops on an empty page whatever the total says', async () => {
		// A total that moved while the read was going must not become a loop that never ends.
		mocks.get.mockResolvedValue({ items: [], total: 50 });

		expect(await usernames.allOf({ personId: 'p-1' })).toEqual([]);
		expect(mocks.get).toHaveBeenCalledTimes(1);
	});
});

describe('the usernames under a wall of cards', () => {
	it("answers each card's usernames, and nothing for a card it has none for", async () => {
		mocks.get.mockResolvedValue({
			items: [
				username({ id: 'a-1', person_id: 'p-1' }),
				username({ id: 'a-2', person_id: 'p-2', asset_count: 0 })
			],
			total: 2
		});
		const lines = new UsernamesByCard('person_id');

		await lines.read({ siteId: 's-1' });

		expect(lines.of('p-1').map((one) => one.id)).toEqual(['a-1']);
		expect(lines.of('p-2')).toEqual([]);
	});

	it('draws nothing rather than failing when the read fails', async () => {
		mocks.get.mockResolvedValueOnce({ items: [username()], total: 1 });
		const lines = new UsernamesByCard('site_id');
		await lines.read({ personId: 'p-1' });
		mocks.get.mockRejectedValueOnce(new Error('offline'));

		await lines.read({ personId: 'p-1' });

		expect(lines.of('s-1')).toEqual([]);
	});
});

describe("the two writes a username line's sheet makes", () => {
	/* The sheet under a person's Sites tab is where these are made. Pinned to the ADDRESS, because
	   "Take off" must be the route that records the unlinking in History (`detach_account` writes
	   an `unlinked` event), and not a second way of clearing the join that leaves no trace. */
	it("writes a typed number through the username's own write", async () => {
		mocks.put.mockResolvedValueOnce(username({ number: '4242' }));

		await usernames.save('a-1', { number: '4242' });

		expect(mocks.put).toHaveBeenCalledWith('/usernames/a-1', { body: { number: '4242' } });
	});

	it('takes a username off its person through DELETE /usernames/{id}/person', async () => {
		mocks.del.mockResolvedValueOnce(undefined);

		await usernames.detach('a-1');

		expect(mocks.del).toHaveBeenCalledWith('/usernames/a-1/person');
	});
});
