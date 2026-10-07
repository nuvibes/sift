/* The one rule that makes an entity page stop looking like it reloaded itself.
 *
 * Every write on a person (a heart, a cover, a rename) announces that the library moved, so
 * the page re-reads. Putting the word "Loading..." over the whole screen while that happens is
 * indistinguishable from somebody pressing F5. Nothing about the row on screen has stopped being
 * true while a fresher copy is on its way.
 *
 * What must NOT follow from that is a stale row under a new address: stepping from one person to
 * another has to clear the screen, or the first one's name and picture sit under the second one's
 * heading and everything pressed acts on the wrong thing. The two cases are one line apart in the
 * code and opposite on the screen, which is why they are tested rather than reasoned about.
 */

import { flushSync } from 'svelte';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '$lib/api/client';
import { EntitySubject, prime, primedOr } from './subject.svelte';

interface Row {
	id: string;
	name: string;
}

/** A fetch whose answers are handed over by the test, one at a time. */
function pending() {
	const waiting: { id: string; settle: (row: Row) => void; fail: (error: unknown) => void }[] = [];
	const fetch = (id: string) =>
		new Promise<Row>((settle, fail) => {
			waiting.push({ id, settle: (row) => settle(row), fail });
		});
	return { fetch, waiting };
}

describe('the first answer for a subject', () => {
	it('settles while there is nothing on screen', async () => {
		const { fetch, waiting } = pending();
		const subject = new EntitySubject<Row>(fetch);

		void subject.load('one');
		expect(subject.settling).toBe(true);
		expect(subject.value).toBe(null);

		waiting[0].settle({ id: 'one', name: 'Ada' });
		await Promise.resolve();
		await Promise.resolve();

		expect(subject.settling).toBe(false);
		expect(subject.value).toEqual({ id: 'one', name: 'Ada' });
	});
});

describe('re-reading the SAME subject', () => {
	it('leaves what is on screen up, and never settles', async () => {
		const { fetch, waiting } = pending();
		const subject = new EntitySubject<Row>(fetch);

		void subject.load('one');
		waiting[0].settle({ id: 'one', name: 'Ada' });
		await Promise.resolve();
		await Promise.resolve();

		void subject.load('one');

		// The whole of the rule. A placeholder here would read as a page reload.
		expect(subject.settling).toBe(false);
		expect(subject.value).toEqual({ id: 'one', name: 'Ada' });

		waiting[1].settle({ id: 'one', name: 'Ada Lovelace' });
		await Promise.resolve();
		await Promise.resolve();

		expect(subject.value).toEqual({ id: 'one', name: 'Ada Lovelace' });
	});
});

describe('moving to a DIFFERENT subject', () => {
	it('clears the screen rather than showing the last one under the new address', async () => {
		const { fetch, waiting } = pending();
		const subject = new EntitySubject<Row>(fetch);

		void subject.load('one');
		waiting[0].settle({ id: 'one', name: 'Ada' });
		await Promise.resolve();
		await Promise.resolve();

		void subject.load('two');

		expect(subject.settling).toBe(true);
		expect(subject.value).toBe(null);
	});

	it('refuses an answer for the subject that was navigated away from', async () => {
		const { fetch, waiting } = pending();
		const subject = new EntitySubject<Row>(fetch);

		void subject.load('one');
		void subject.load('two');
		// The slow one lands second, and it is the one nobody is looking at any more.
		waiting[1].settle({ id: 'two', name: 'Grace' });
		await Promise.resolve();
		await Promise.resolve();
		waiting[0].settle({ id: 'one', name: 'Ada' });
		await Promise.resolve();
		await Promise.resolve();

		expect(subject.value).toEqual({ id: 'two', name: 'Grace' });
	});
});

describe('what a failure says', () => {
	it('tells "it is gone" from "it could not be read"', async () => {
		const { fetch, waiting } = pending();
		const subject = new EntitySubject<Row>(fetch);

		void subject.load('one');
		waiting[0].fail(new ApiError(404, 'no such thing'));
		await Promise.resolve();
		await Promise.resolve();

		expect(subject.value).toBe(null);
		expect(subject.unreadable).toBe(false);

		void subject.load('two');
		waiting[1].fail(new ApiError(500, 'the server fell over'));
		await Promise.resolve();
		await Promise.resolve();

		expect(subject.unreadable).toBe(true);
	});

	it('stops saying it could not be read once it can be', async () => {
		// Cleared on success: a page that failed once and then read perfectly well must not go on
		// saying so. And with a re-read arriving on every library change, that would be a
		// sentence somebody sat looking at.
		const { fetch, waiting } = pending();
		const subject = new EntitySubject<Row>(fetch);

		void subject.load('one');
		waiting[0].fail(new ApiError(500, 'the server fell over'));
		await Promise.resolve();
		await Promise.resolve();
		expect(subject.unreadable).toBe(true);

		void subject.load('one');
		waiting[1].settle({ id: 'one', name: 'Ada' });
		await Promise.resolve();
		await Promise.resolve();

		expect(subject.unreadable).toBe(false);
		expect(subject.value).toEqual({ id: 'one', name: 'Ada' });
	});
});

describe('no subject at all', () => {
	it('draws nothing and does not sit settling forever', async () => {
		const { fetch } = pending();
		const subject = new EntitySubject<Row>(fetch);

		await subject.load('');

		expect(subject.value).toBe(null);
		expect(subject.settling).toBe(false);
	});

	it('does not count an empty address as the subject already on screen', async () => {
		// `#shown` has to be cleared with the row, or coming back to the same person after a blank
		// address would keep the placeholder down and show nothing.
		const { fetch, waiting } = pending();
		const subject = new EntitySubject<Row>(fetch);

		void subject.load('one');
		waiting[0].settle({ id: 'one', name: 'Ada' });
		await Promise.resolve();
		await Promise.resolve();

		await subject.load('');
		void subject.load('one');

		expect(subject.settling).toBe(true);
		flushSync();
	});
});

it('hands a primed answer to the first read of its subject only, and asks afresh after it', async () => {
	const early = Promise.resolve('early');
	prime('kind-a', 'p1', early);
	const ask = vi.fn(() => Promise.resolve('fresh'));

	expect(primedOr('kind-a', 'p2', ask)).not.toBe(early);
	expect(primedOr('kind-a', 'p1', ask)).toBe(early);
	expect(await primedOr('kind-a', 'p1', ask)).toBe('fresh');
	expect(ask).toHaveBeenCalledTimes(2);
});
