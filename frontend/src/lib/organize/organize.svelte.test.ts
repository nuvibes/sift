/* The board, held between visits, and the one request two callers share. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { Held, undoneLine, type Board } from '$lib/organize/organize.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

/** A board told apart by its one queue's count: a distinct number per answer. */
function aBoard(count: number): Board {
	return { queues: [{ name: 'folders', count } as Board['queues'][number]] };
}

/** An answer this test decides when to give, so two calls can be in flight at the same time. */
function held(value: Board) {
	let settle: (answer: Board) => void = () => {};
	const promise = new Promise<Board>((resolve) => (settle = resolve));
	return { promise, give: () => settle(value) };
}

beforeEach(() => {
	vi.clearAllMocks();
});

describe('the held board', () => {
	it('answers a screen and its header with ONE request', async () => {
		const first = held(aBoard(1));
		mocked.get.mockReturnValueOnce(first.promise);
		const store = new Held();

		const screen = store.refresh();
		const header = store.ensure();
		first.give();
		await Promise.all([screen, header]);

		expect(mocked.get).toHaveBeenCalledTimes(1);
		expect(mocked.get).toHaveBeenCalledWith('/workbench');
		expect(store.found?.queues[0]?.count).toBe(1);
	});

	it('asks again for a refresh made while another refresh is in flight', async () => {
		const first = held(aBoard(2));
		const second = held(aBoard(3));
		mocked.get.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
		const store = new Held();

		const one = store.refresh();
		const two = store.refresh();
		first.give();
		second.give();

		expect(await one).toEqual(aBoard(2));
		expect(await two).toEqual(aBoard(3));
		expect(mocked.get).toHaveBeenCalledTimes(2);
	});

	it('asks again for the queues a screen draws, and leaves the rest as held', async () => {
		const queue = (name: string, count: number) => ({ name, count }) as Board['queues'][number];
		mocked.get
			.mockResolvedValueOnce({
				queues: [queue('folders', 1), queue('duplicates', 2), queue('copies', 3)]
			})
			.mockResolvedValueOnce({ queues: [queue('duplicates', 5)] });
		const store = new Held();
		await store.refresh();

		const after = await store.refreshOnly(['duplicates', 'copies']);

		expect(mocked.get).toHaveBeenLastCalledWith('/workbench', {
			query: { only: ['duplicates', 'copies'] }
		});
		expect(after.queues.map((one) => [one.name, one.count])).toEqual([
			['folders', 1],
			['duplicates', 5]
		]);
		expect(store.found).toEqual(after);
	});

	it('asks for the whole board when a queue it was asked about is new on the board', async () => {
		/* A merge keeps only the queues already held, so a pile that has just become available
		   would never appear, and its own page would say no queue has that name. */
		const queue = (name: string, count: number) => ({ name, count }) as Board['queues'][number];
		const whole = { queues: [queue('folders', 1), queue('shoots', 2)] };
		mocked.get
			.mockResolvedValueOnce({ queues: [queue('folders', 1)] })
			.mockResolvedValueOnce({ queues: [queue('shoots', 2)] })
			.mockResolvedValueOnce(whole);
		const store = new Held();
		await store.refresh();

		const after = await store.refreshOnly(['shoots']);

		expect(mocked.get).toHaveBeenLastCalledWith('/workbench');
		expect(after).toEqual(whole);
		expect(store.found).toEqual(whole);
	});

	it('asks for the whole board when none is held yet', async () => {
		mocked.get.mockResolvedValueOnce(aBoard(4));
		const store = new Held();

		await store.refreshOnly(['duplicates']);

		expect(mocked.get).toHaveBeenCalledWith('/workbench');
		expect(store.found).toEqual(aBoard(4));
	});

	it('does not ask at all once the board is here', async () => {
		mocked.get.mockResolvedValueOnce(aBoard(1));
		const store = new Held();
		await store.refresh();

		await store.ensure();

		expect(mocked.get).toHaveBeenCalledTimes(1);
	});

	it('asks again on the next ensure when the shared request failed', async () => {
		mocked.get.mockRejectedValueOnce(new Error('away')).mockResolvedValueOnce(aBoard(1));
		const store = new Held();

		await store.ensure();
		expect(store.found).toBeNull();

		await store.ensure();
		expect(mocked.get).toHaveBeenCalledTimes(2);
		expect(store.found?.queues[0]?.count).toBe(1);
	});

	it('leaves a refusal to the screen that asked for a refresh', async () => {
		mocked.get.mockRejectedValueOnce(new Error('away'));
		const store = new Held();

		await expect(store.refresh()).rejects.toThrow('away');
	});
});

describe('what an undo toast says', () => {
	const whole = 'Undone';
	const none = 'There was nothing left to undo';

	it("says the caller's word when every act went back, and its other word when none did", () => {
		expect(undoneLine({ undone: true, put_back: 1, of: 1, said: null }, whole, none)).toBe(whole);
		expect(undoneLine({ undone: false, put_back: 0, of: 1, said: null }, whole, none)).toBe(none);
	});

	it("says the server's line, counts and reason, when a batch went back only in part", () => {
		const said = 'Put back 7 of 10 names. The others keep the names they have now.';
		expect(undoneLine({ undone: true, put_back: 7, of: 10, said }, whole, none)).toBe(said);
	});
});
