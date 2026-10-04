import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
	announceSkipped,
	mergeBulk,
	reasonFor,
	type BulkWriteDone,
	overChunks
} from '$lib/library/bulk';

/* What a write over a selection left out, said once for the whole application.
 *
 * Seven routes answer this shape and about a dozen screens call them. The behaviour worth pinning
 * is not the wording: it is that the Unlock button appears for exactly one reason and no other,
 * because that is the half a screen written by hand would forget.
 */

const shown = vi.hoisted(() => vi.fn());
const ask = vi.hoisted(() => vi.fn());
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: shown } }));
vi.mock('$lib/shell/vault.svelte', () => ({ vaultPrompt: { ask } }));

function lastToast(): {
	message: string;
	tone?: string;
	icon?: string;
	action?: { label: string; run: () => void };
} {
	const call = shown.mock.calls.at(-1) ?? [];
	return { message: String(call[0] ?? ''), ...(call[1] ?? {}) };
}

const done = (over: Partial<BulkWriteDone> = {}): BulkWriteDone => ({
	changed: 0,
	skipped: 0,
	reason: null,
	reason_many: null,
	vault_locked: false,
	...over
});

beforeEach(() => {
	shown.mockReset();
	ask.mockReset();
});

describe('saying what a bulk write left out', () => {
	it('says nothing at all when nothing was left out', () => {
		announceSkipped(done({ changed: 3 }));

		expect(shown).not.toHaveBeenCalled();
	});

	it('offers to unlock, and a padlock, when the vault is what stopped it', () => {
		announceSkipped(
			done({ changed: 2, skipped: 1, reason: 'It is in your vault.', vault_locked: true })
		);

		const toast = lastToast();
		expect(toast.message).toBe("One file couldn't be included. It is in your vault.");
		expect(toast.icon).toBe('lock');
		expect(toast.action?.label).toBe('Unlock');
		toast.action?.run();
		expect(ask).toHaveBeenCalled();
	});

	it('offers NOTHING when unlocking would not change the answer', () => {
		/* The direction that matters. A file that is simply not there is not a lock, and a button
		   promising to fix it is a button that fixes nothing. */
		announceSkipped(done({ changed: 2, skipped: 1, reason: 'Sift could not find the file.' }));

		expect(lastToast().action).toBeUndefined();
		expect(lastToast().icon).toBeUndefined();
	});

	it('is red only when nothing landed at all', () => {
		announceSkipped(done({ changed: 2, skipped: 1, reason: 'why' }));
		expect(lastToast().tone).toBe('info');

		announceSkipped(done({ changed: 0, skipped: 1, reason: 'why' }));
		expect(lastToast().tone).toBe('error');
	});

	it('counts in the words of the screen it is on', () => {
		announceSkipped(done({ skipped: 2, reason: 'why' }), 'picture');

		expect(lastToast().message).toBe("2 pictures couldn't be included. why");
	});

	it('does not trail a space when there is no reason to give', () => {
		announceSkipped(done({ skipped: 1 }));

		expect(lastToast().message).toBe("One file couldn't be included.");
	});
});

describe('folding several answers about one selection', () => {
	it('adds up what changed and does NOT add up what was skipped', () => {
		/* Adding a selection to three collections is three calls about the SAME files, so all three
		   skip the same one. Summing that would report one hidden file as three left out. */
		const folded = mergeBulk([
			done({ changed: 2, skipped: 1, reason: 'in your vault', vault_locked: true }),
			done({ changed: 2, skipped: 1, reason: 'in your vault', vault_locked: true }),
			done({ changed: 2, skipped: 1, reason: 'in your vault', vault_locked: true })
		]);

		expect(folded).toEqual({
			changed: 6,
			skipped: 1,
			reason: 'in your vault',
			reason_many: null,
			vault_locked: true
		});
	});

	it('is a clean answer when every call was clean', () => {
		expect(mergeBulk([done({ changed: 1 }), done({ changed: 2 })])).toEqual(done({ changed: 3 }));
	});

	it('is a clean answer for no calls at all', () => {
		expect(mergeBulk([])).toEqual(done());
	});
});

describe('a write over more files than one request will take', () => {
	/* The whole reason this exists: "select all" means the whole query, and every write endpoint
	 * takes at most five hundred ids. Without splitting, the largest selection anybody can act on is
	 * the largest one they can make by hand, which is the fault, not a safeguard. */

	function ids(count: number): string[] {
		return Array.from({ length: count }, (_, at) => `a${at}`);
	}

	it('sends five hundred at a time and no more', async () => {
		const sent: number[] = [];
		const done = await overChunks(ids(1250), async (chunk) => {
			sent.push(chunk.length);
			return {
				changed: chunk.length,
				skipped: 0,
				reason: null,
				reason_many: null,
				vault_locked: false
			};
		});

		expect(sent).toEqual([500, 500, 250]);
		expect(done.changed).toBe(1250);
	});

	it('sends one request for a selection that fits, and none for an empty one', async () => {
		const sent: number[] = [];
		const send = async (chunk: string[]) => {
			sent.push(chunk.length);
			return {
				changed: chunk.length,
				skipped: 0,
				reason: null,
				reason_many: null,
				vault_locked: false
			};
		};

		await overChunks(ids(3), send);
		expect(sent).toEqual([3]);

		await overChunks([], send);
		expect(sent).toEqual([3]);
	});

	it('ADDS UP what was skipped, which merging answers deliberately does not', async () => {
		/* The distinction worth holding. `mergeBulk` covers several calls about the SAME files, so
		 * summing would report one hidden file as three. Chunks are calls about DIFFERENT files, so
		 * two skips here and five there are seven distinct files and the sum is the true number. */
		const done = await overChunks(ids(1500), async (chunk) => ({
			changed: chunk.length - 2,
			skipped: 2,
			reason: 'It is in your vault.',
			reason_many: 'They are in your vault.',
			vault_locked: true
		}));

		expect(done.skipped).toBe(6);
		expect(done.changed).toBe(1494);
		// One reason, not three copies of it: every chunk of one selection is refused for the same
		// cause, and a screen showing the sentence three times is a screen nobody reads.
		expect(done.reason).toBe('It is in your vault.');
		expect(done.vault_locked).toBe(true);
	});

	it('reports what LANDED when a chunk fails, rather than throwing it all away', async () => {
		/* The chunks before a failure have already been written and nothing can put them back.
		 * Throwing would tell somebody the write failed while a thousand files had been altered. */
		let calls = 0;
		const done = await overChunks(ids(1500), async (chunk) => {
			calls += 1;
			if (calls === 3) throw new Error('the server went away');
			return {
				changed: chunk.length,
				skipped: 0,
				reason: null,
				reason_many: null,
				vault_locked: false
			};
		});

		expect(done.changed).toBe(1000);
		// Everything from the failed chunk onwards, because none of it was attempted.
		expect(done.skipped).toBe(500);
		expect(done.reason).toBeTruthy();
		// And it stopped rather than carrying on into a server that is not answering.
		expect(calls).toBe(3);
	});

	it('keeps the FIRST reason when later chunks give their own', async () => {
		const done = await overChunks(ids(1000), async (chunk) => ({
			changed: 0,
			skipped: chunk.length,
			reason: chunk[0] === 'a0' ? 'the first reason' : 'a later one',
			reason_many: chunk[0] === 'a0' ? 'the first reasons' : 'later ones',
			vault_locked: false
		}));

		expect(done.reason).toBe('the first reason');
		// Both halves come from the same chunk, or the toast is two different refusals in one line.
		expect(done.reason_many).toBe('the first reasons');
		expect(done.skipped).toBe(1000);
	});
});

/*
 * THE COUNT AND THE SENTENCE ARE DECIDED IN TWO PLACES, and this is where they meet.
 *
 * Only this side knows how many were skipped; only the server knows the words, which for bulk
 * delete are whatever refused the file rather than a constant. So the server sends both wordings
 * and this picks. With one wording, a selection of three would print "3 files could not be
 * included. It is in your vault."
 */
describe('the reason agrees in number with the count beside it', () => {
	it('says it about ONE file when one was left out', () => {
		announceSkipped(
			done({
				changed: 2,
				skipped: 1,
				reason: 'It is in your vault. Unlock the vault to include it.',
				reason_many: 'They are in your vault. Unlock the vault to include them.',
				vault_locked: true
			})
		);

		expect(lastToast().message).toBe(
			"One file couldn't be included. It is in your vault. Unlock the vault to include it."
		);
	});

	it('says it about SEVERAL when several were', () => {
		announceSkipped(
			done({
				changed: 2,
				skipped: 3,
				reason: 'It is in your vault. Unlock the vault to include it.',
				reason_many: 'They are in your vault. Unlock the vault to include them.',
				vault_locked: true
			})
		);

		expect(lastToast().message).toBe(
			"3 files couldn't be included. They are in your vault. Unlock the vault to include them."
		);
	});

	it('falls back to the one wording it was given rather than explaining nothing', () => {
		// An older server, or a producer not yet taught the plural. One sentence about the wrong
		// number still says what happened; silence does not.
		announceSkipped(done({ changed: 0, skipped: 4, reason: 'That folder is read-only.' }));

		expect(lastToast().message).toBe("4 files couldn't be included. That folder is read-only.");
	});

	it('is picked by the number ASKED about, which is not always `skipped`', () => {
		// The stash-box scan pairs the same kind of sentence with a count of its own, and answers
		// in the same two fields without being a `BulkWriteDone` at all.
		const said = { reason: 'one', reason_many: 'many' };

		expect(reasonFor(said, 1)).toBe('one');
		expect(reasonFor(said, 2)).toBe('many');
		expect(reasonFor({ reason: null, reason_many: null }, 2)).toBeNull();
	});

	it('carries both wordings through a merge and through chunking', async () => {
		const merged = mergeBulk([
			done({ changed: 1 }),
			done({ changed: 1, skipped: 2, reason: 'one', reason_many: 'many' })
		]);
		expect([merged.reason, merged.reason_many]).toEqual(['one', 'many']);

		const chunked = await overChunks(['a'], async () =>
			done({ changed: 0, skipped: 1, reason: 'one', reason_many: 'many' })
		);
		expect([chunked.reason, chunked.reason_many]).toEqual(['one', 'many']);
	});
});
