// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * What a write over a selection left out, said once for the whole application.
 *
 * ## Why this is a module and not a line in each caller
 *
 * Seven routes now answer `BulkWriteDone`, and about a dozen screens call them: the grid's action
 * bar, the collection and photo-set walls, a person's page, the Loops wall, the detail pane, and
 * three of the faces surfaces. Every one of them has its own success sentence, which is right:
 * "went on 2 tags" and "joined the group" are different events. What is NOT different is the
 * exception: some of them could not be done, here is how many, here is why, and here is the button
 * that fixes it. Written out per caller that is a dozen chances to forget the button.
 *
 * ## Why the Unlock offer keys on a FLAG and not on the sentence
 *
 * `reason` is free text (bulk delete's comes from whatever refused the file, such as a folder
 * handed over read-only), so a screen deciding "is this the vault?" by comparing it against a copy
 * of one particular sentence is two lists that have to agree. Improve the wording on the server and
 * the Unlock button quietly stops appearing, with nothing failing. `vault_locked` is the fact.
 */

import { ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { toasts } from '$lib/shell/toasts.svelte';
import { vaultPrompt } from '$lib/shell/vault.svelte';
import { counted } from '$lib/entity/entity-counts';

/** The server's own shape. Generated from the schema it publishes, never hand-copied. */
export type BulkWriteDone = components['schemas']['BulkWriteDone'];

/**
 * Say what a bulk write could not do, if it could not do all of it.
 *
 * Silent when nothing was skipped, so every caller can call it unconditionally rather than each
 * writing the same `if`. Said as a SECOND toast after the caller's own success line, which is the
 * honest order: what happened is the answer to what was asked,
 * and what did not happen is the exception to it.
 *
 * `noun` names the things in the person's own words for this screen ("file", "face", "picture"),
 * because "3 files were left out" and "3 faces were left out" are read on different screens and
 * only one of them is ever right.
 */
/**
 * The server's reason, worded for the number this screen is about to say.
 *
 * ## Why the server sends two sentences and this picks one
 *
 * The count and the sentence are settled in two places that cannot see each other. Only this side
 * knows how many were skipped (it made the selection), and only the server knows the words,
 * which for bulk delete are not a constant at all but whatever refused the file. With one wording,
 * a screen would pair its own "3 files could not be included" with a sentence written about one,
 * and print *"3 files could not be included. It is in your vault."*
 *
 * Inflecting the sentence here was never available: it is free text, and a client that rewrote the
 * server's words would be a second author of them.
 *
 * `reason` is the fallback and not merely a default. An older server, or a producer that has not
 * been taught the plural, sends only the singular, and one sentence about the wrong number reads
 * better than no explanation at all.
 */
export function reasonFor(
	// The two wordings and nothing else, so anything answering in this shape can use it. The
	// stash-box scan does: it is not a `BulkWriteDone` (it has no `changed`), and it pairs the
	// same kind of sentence with the same kind of count, so it carries the same pair of fields.
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
		// whole thing as a failure says the write did not happen, and it did. There is no warning
		// tone and this is not the place to invent one. The padlock below carries the weight.
		tone: done.changed > 0 ? 'info' : 'error',
		// The one fact the tone cannot carry, which is what the icon override is for: this is a
		// lock, not a loss. It also pairs with the button beside it.
		icon: done.vault_locked ? 'lock' : undefined,
		// Only where unlocking would actually change the answer. See the note at the top for why
		// this reads a flag rather than the sentence beside it.
		action: done.vault_locked ? { label: 'Unlock', run: () => vaultPrompt.ask() } : undefined
	});
}

/**
 * One answer out of several, for a screen that sends one request per destination.
 *
 * Adding a selection to three collections is three calls, because that is the shape of the
 * endpoint, and all three ask about the SAME files, so all three skip the same ones for the same
 * reason. Summing `skipped` across them would report a selection of three with one file hidden as
 * three files left out.
 *
 * So the counts of what changed add up and the exception does not: it is one fact about one
 * selection, taken from the first call that hit it.
 */
export function mergeBulk(results: BulkWriteDone[]): BulkWriteDone {
	const stopped = results.find((done) => done.skipped > 0);
	return {
		changed: results.reduce((total, done) => total + done.changed, 0),
		skipped: stopped?.skipped ?? 0,
		reason: stopped?.reason ?? null,
		// Taken from the SAME answer as the sentence beside it. Reading one from the first call that
		// skipped and the other from the first that had a plural would be two halves of two
		// different refusals in one message.
		reason_many: stopped?.reason_many ?? null,
		vault_locked: stopped?.vault_locked ?? false
	};
}

/* The status a write gets when the asker's OWN vault is concealing the one file it named.
 *
 * `kernel.reach.vault_locked` is the only thing that answers it on a write to a file. The app lock
 * answers 423 too, and the client never reaches this for one of those: it carries `Sift-Locked` and
 * the request layer takes the whole page to the lock screen before any caller sees an error.
 */
const VAULT_LOCKED = 423;

/**
 * Say why ONE write was refused, and offer the way through when there is one.
 *
 * The single-file twin of `announceSkipped` above, and here beside it for the reason that one is
 * here: a dozen callers each writing their own sentence is a dozen chances to forget the button.
 *
 * A caller that says `That could not be saved.` and stops is, on a file in the vault, two
 * wrong things at once: it says something went wrong when nothing did, and it leaves somebody
 * with a heart that will not stick and no idea why. The server has said exactly what the matter is
 * and named the one thing that fixes it; this is what carries that to the screen.
 *
 * The server's own sentence rather than a copy of it, for the reason `ApiError.detail` exists: this
 * refusal is written for the person reading it, and the wording lives in one place. Anything that
 * is not the vault keeps the flat line, because most refusals explain nothing worth reading and a
 * 404 that explains itself has confirmed the thing exists.
 *
 * Returns nothing and swallows nothing: the caller has already put its optimistic write back.
 */
export function announceRefusal(caught: unknown, fallback = "That couldn't be saved"): void {
	const locked = caught instanceof ApiError && caught.status === VAULT_LOCKED;
	if (!locked) {
		toasts.show(fallback, { tone: 'error' });
		return;
	}
	toasts.show(caught.detail ?? "It's in your vault. Unlock the vault to include it.", {
		// Not an error. Nothing failed: the vault did exactly what it was asked to do, and the
		// same reasoning `announceSkipped` gives for its own tone applies with more force here.
		tone: 'info',
		icon: 'lock',
		action: { label: 'Unlock', run: () => vaultPrompt.ask() }
	});
}

/**
 * The most ids one write may carry.
 *
 * The server's own cap, declared in five slices as 500 and repeated here so a client that would be
 * refused never sends the request. Not a guess: a selection over this is split rather than trimmed,
 * so the number is a request size and never a limit on what somebody may act on.
 *
 * Not exported. Nothing outside decides how big a chunk is: `overChunks` below is the one thing
 * that splits, and a second reader of this number would be a second place that could disagree with
 * the server about what it accepts.
 */
const MOST_PER_WRITE = 500;

/**
 * Run one bulk write over a selection of any size, as however many requests it takes.
 *
 * ## Why this exists at all
 *
 * "Select all" means the whole query, and a library's whole query is thousands of files. Every
 * write endpoint takes at most five hundred ids, so without this the largest selection somebody can
 * act on is the largest selection they can make by hand, which is the fault, not a safeguard.
 *
 * ## Why it does not reuse `mergeBulk`
 *
 * `mergeBulk` deliberately does NOT add up `skipped`, and its reasoning is exactly right for what
 * it does: adding a selection to three collections is three calls about the SAME files, so all
 * three skip the same ones and summing would report one hidden file as three.
 *
 * Chunks are the opposite case. They are calls about DIFFERENT files, so chunk three's two skips
 * and chunk seven's five are seven distinct files and the sum is the true number. One reason is
 * still one reason: every chunk of one selection is refused for the same cause, and thirty copies
 * of one sentence is a screen nobody reads.
 *
 * ## What happens when a chunk fails outright
 *
 * The chunks before it have already landed and nothing can put them back. Reported as a partial
 * write rather than thrown, which is the shape the server itself answers with: what changed is
 * what changed, everything not attempted is counted as skipped, and the reason says why. Throwing
 * instead would tell somebody the write failed while several thousand files had already been
 * altered.
 */
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
			// Everything from this chunk onwards, because none of it was attempted. Counted rather
			// than raised: the writes before it are real and the person is owed the true number.
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
		// The FIRST reason and the first flag, kept rather than replaced. Later chunks are refused
		// for the same cause, and a selection spanning one locked vault produces one sentence.
		done.reason ??= answer.reason;
		done.reason_many ??= answer.reason_many;
		done.vault_locked ||= answer.vault_locked;
	}
	return done;
}
