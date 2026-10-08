/* The period's answer, as Insights and its Stats view read it: asked for the place it is given, an
 * answer to a place already left dropped, a failure said, and a re-read that replaces only a
 * different answer, waits out a period still loading and runs once more for a bell that rang
 * meanwhile. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';

import type { InsightsPage, Place } from '$lib/components/insights/period';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get }
}));

import { PeriodAnswer } from './answer.svelte';

function page(from: string): InsightsPage {
	return {
		period: 'week',
		from,
		to: from,
		today_is_live: false,
		first_sentences: [],
		blocks: []
	};
}

/** A promise and the hands that settle it, so a test decides when an answer lands. */
function later<T>() {
	let settle!: (value: T) => void;
	let refuse!: (error: unknown) => void;
	const promise = new Promise<T>((yes, no) => {
		settle = yes;
		refuse = no;
	});
	return { promise, settle, refuse };
}

let place = $state<Place>({ period: 'week', at: '2026-10-05' });
let read: PeriodAnswer;
let stop: () => void;

async function settled() {
	for (let turn = 0; turn < 5; turn += 1) await Promise.resolve();
	flushSync();
}

beforeEach(() => {
	mocks.get.mockReset();
	place = { period: 'week', at: '2026-10-05' };
});

afterEach(() => stop?.());

function start() {
	stop = $effect.root(() => {
		read = new PeriodAnswer(() => place);
	});
	flushSync();
}

it('asks for the place it is given and holds the answer, counting the arrival', async () => {
	mocks.get.mockResolvedValue(page('2026-10-05'));
	start();
	expect(read.loading).toBe(true);
	await settled();
	expect(mocks.get).toHaveBeenCalledWith('/insights', {
		query: { period: 'week', at: '2026-10-05' }
	});
	expect([read.loading, read.failed, read.arrival, read.answer?.from]).toEqual([
		false,
		false,
		1,
		'2026-10-05'
	]);
});

it('says it failed when the answer could not be read', async () => {
	mocks.get.mockRejectedValue(new Error('down'));
	start();
	await settled();
	expect([read.failed, read.loading, read.answer]).toEqual([true, false, null]);
});

it('drops an answer to a place already left, and a failure from one', async () => {
	const first = later<InsightsPage>();
	const second = later<InsightsPage>();
	mocks.get.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
	start();
	place = { period: 'week', at: '2026-10-12' };
	flushSync();
	second.settle(page('2026-10-12'));
	await settled();
	first.settle(page('2026-10-05'));
	await settled();
	expect(read.answer?.from).toBe('2026-10-12');

	const third = later<InsightsPage>();
	mocks.get.mockReturnValueOnce(third.promise).mockResolvedValueOnce(page('2026-10-19'));
	place = { period: 'week', at: '2026-10-14' };
	flushSync();
	place = { period: 'week', at: '2026-10-19' };
	flushSync();
	third.refuse(new Error('late'));
	await settled();
	expect([read.failed, read.answer?.from]).toEqual([false, '2026-10-19']);
});

it('re-reads in place: a different answer replaces it, the same one stays, an arrival is not counted', async () => {
	mocks.get.mockResolvedValue(page('2026-10-05'));
	start();
	await settled();
	const drawn = read.answer;
	await read.reread();
	expect(read.answer, 'the same answer was replaced').toBe(drawn);
	mocks.get.mockResolvedValue({ ...page('2026-10-05'), today_is_live: true });
	await read.reread();
	expect(read.answer?.today_is_live).toBe(true);
	expect(read.arrival).toBe(1);
	/* A failed re-read leaves the page as drawn. */
	mocks.get.mockRejectedValue(new Error('down'));
	await read.reread();
	expect([read.failed, read.answer?.today_is_live]).toEqual([false, true]);
});

it('drops a re-read while a period is loading or once the place has moved', async () => {
	const pending = later<InsightsPage>();
	mocks.get.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(page('2026-09-01'));
	start();
	await read.reread();
	expect(read.answer, 'a re-read drawn over a period still loading').toBeNull();
	pending.settle(page('2026-10-05'));
	await settled();

	const slow = later<InsightsPage>();
	mocks.get.mockReturnValueOnce(slow.promise).mockResolvedValue(page('2026-10-12'));
	const rereading = read.reread();
	place = { period: 'week', at: '2026-10-12' };
	flushSync();
	await settled();
	slow.settle(page('2026-09-28'));
	await rereading;
	expect(read.answer?.from).toBe('2026-10-12');
});

it('reads once more after a re-read for a bell that rang while it was out', async () => {
	mocks.get.mockResolvedValue(page('2026-10-05'));
	start();
	await settled();
	const slow = later<InsightsPage>();
	mocks.get.mockReset();
	mocks.get.mockReturnValueOnce(slow.promise).mockResolvedValue(page('2026-10-06'));
	const first = read.reread();
	await read.reread();
	expect(mocks.get, 'a second read while one was out').toHaveBeenCalledTimes(1);
	slow.settle(page('2026-10-05'));
	await first;
	await settled();
	expect(mocks.get).toHaveBeenCalledTimes(2);
	expect(read.answer?.from).toBe('2026-10-06');
});
