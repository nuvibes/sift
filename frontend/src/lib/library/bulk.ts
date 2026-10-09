// SPDX-License-Identifier: AGPL-3.0-or-later
/* What a write over a selection left out, said once for the whole application. */

import { ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { toasts } from '$lib/shell/toasts.svelte';
import { vaultPrompt } from '$lib/shell/vault.svelte';
import { counted } from '$lib/entity/entity-counts';

/** The server's own shape. Generated from the schema it publishes, never hand-copied. */
export type BulkWriteDone = components['schemas']['BulkWriteDone'];

/** Say what a bulk write could not do, if it could not do all of it. */
/** The server's reason, worded for the number this screen is about to say. */
export function reasonFor(
	// The two wordings and nothing else, so anything answering in this shape can use it.
	said: Pick<BulkWriteDone, 'reason' | 'reason_many'>,
	of: number
): string | null {
	if (of === 1) return said.reason ?? null;
	return said.reason_many ?? said.reason ?? null;
}

export function announceSkipped(done: BulkWriteDone, noun = 'file'): void {
	if (done.skipped <= 0) return;
	const many = done.skipped === 1 ? `One ${noun}` : `${counted(done.skipped)} ${noun}s`;
	const why = reasonFor(done, done.skipped);
	toasts.show(`${many} couldn't be included.${why ? ` ${why}` : ''}`, {
		// Red only where NOTHING landed. Where the rest of the work went through, painting the
		// whole thing as a failure says the write did not happen, and it did.
		tone: done.changed > 0 ? 'info' : 'error',
		// The one fact the tone cannot carry, which is what the icon override is for: this is a
		// lock, not a loss.
		icon: done.vault_locked ? 'lock' : undefined,
		// Only where unlocking would actually change the answer.
		action: done.vault_locked ? { label: 'Unlock', run: () => vaultPrompt.ask() } : undefined
	});
}

/** One answer out of several, for a screen that sends one request per destination. */
export function mergeBulk(results: BulkWriteDone[]): BulkWriteDone {
	const stopped = results.find((done) => done.skipped > 0);
	return {
		changed: results.reduce((total, done) => total + done.changed, 0),
		skipped: stopped?.skipped ?? 0,
		reason: stopped?.reason ?? null,
		// Taken from the SAME answer as the sentence beside it.
		reason_many: stopped?.reason_many ?? null,
		vault_locked: stopped?.vault_locked ?? false
	};
}

/* The status a write gets when the asker's OWN vault is concealing the one file it named. */
const VAULT_LOCKED = 423;

/** Say why ONE write was refused, and offer the way through when there is one. */
export function announceRefusal(caught: unknown, fallback = "That couldn't be saved"): void {
	const locked = caught instanceof ApiError && caught.status === VAULT_LOCKED;
	if (!locked) {
		toasts.show(fallback, { tone: 'error' });
		return;
	}
	toasts.show(caught.detail ?? "It's in your vault. Unlock the vault to include it.", {
		// Not an error. Nothing failed: the vault did exactly what it was asked to do, and the same
		// reasoning `announceSkipped` gives for its own tone applies with more force here.
		tone: 'info',
		icon: 'lock',
		action: { label: 'Unlock', run: () => vaultPrompt.ask() }
	});
}

/** The most ids one write may carry. The server's own cap, declared in five slices as 500 and
 * repeated here so a client that would be refused never sends the request. */
const MOST_PER_WRITE = 500;

/** Run one bulk write over a selection of any size, as however many requests it takes. */
export async function overChunks(
	ids: readonly string[],
	send: (chunk: string[]) => Promise<BulkWriteDone>
): Promise<BulkWriteDone> {
	const done: BulkWriteDone = {
		changed: 0,
		skipped: 0,
		reason: null,
		reason_many: null,
		vault_locked: false
	};
	for (let at = 0; at < ids.length; at += MOST_PER_WRITE) {
		const chunk = ids.slice(at, at + MOST_PER_WRITE);
		let answer: BulkWriteDone;
		try {
			answer = await send(chunk);
		} catch (failure) {
			// Everything from this chunk onwards, because none of it was attempted.
			done.skipped += ids.length - at;
			// One wording for both numbers: a request that failed outright names no count, so the
			// same sentence is true of the one file left and of the four thousand.
			const stopped = failure instanceof ApiError ? failure.message : "Sift couldn't finish.";
			done.reason ??= stopped;
			done.reason_many ??= stopped;
			return done;
		}
		done.changed += answer.changed;
		done.skipped += answer.skipped;
		// The FIRST reason and the first flag, kept rather than replaced.
		done.reason ??= answer.reason;
		done.reason_many ??= answer.reason_many;
		done.vault_locked ||= answer.vault_locked;
	}
	return done;
}
