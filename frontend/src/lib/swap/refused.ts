// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * What will not go in a swap, as swap mode says it: the mark a card or a tile wears, and the one
 * sentence a press on it says, with the way to change it.
 *
 * Two marks keep a thing out of every swap: Kept local (the "Don't enrich" switch, which keeps
 * everything about a thing on this device) and "Don't swap". A person, a Site or a tag answers for
 * its own row, which its wall row carries (`keep_local`, `keep_from_swaps`); a file answers for
 * itself and for anything it is filed under, which its tile carries as one fact (`swap_refused`)
 * and which a press asks about in full (`keptFromSwaps`, whose answer names what above it keeps it
 * out, as the reader may see it).
 *
 * A press on a refused card or tile never picks it. It says why in one sentence, and offers the way
 * to change it: the switch itself where the mark is on the thing pressed ("Allow swapping", "Allow
 * enrichment", the words the menus use), or a link to the thing above a file that carries it,
 * whose own menu holds the switch. Changing a person's mark from a press on one of their files
 * would change it for every file of theirs, which is a decision to make on their page.
 */

import { setKeptLocal } from '$lib/entity/enrichment.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { thing, type ToastPiece, type ToastWords } from '$lib/components/common/toast-pieces';
import { keptFromSwaps, setKeptFromSwaps, type RefusalSubject } from '$lib/components/swap/swap';
import type { PickChoice } from '$lib/components/common/verbs';

/** Which mark keeps a thing back: Kept local, or "Don't swap". */
export type RefusedMark = 'local' | 'swap';

/** The mark a wall row says it wears, or null for one that goes. Kept local first: it keeps back
 *  more than a swap. The fields are read loosely, since a row from before the field reads none. */
export function refusedMark(row: {
	keep_local?: boolean | null;
	keep_from_swaps?: boolean | null;
}): RefusedMark | null {
	if (row.keep_local) return 'local';
	if (row.keep_from_swaps) return 'swap';
	return null;
}

/** The glyph a refused card or tile wears where a picked one wears the swap's: the menus' own
 *  "Don't swap". */
const REFUSED_ICON = 'do_not_disturb_on';

/** Why a row in a swap's chooser cannot be chosen, said on the row itself: the same two marks
 *  the walls say, in the same words, without the name the row already shows. */
function refusedReason(mark: RefusedMark): string {
	return mark === 'local'
		? "Kept local, so it isn't offered"
		: "Kept out of swaps, so it isn't offered";
}

/** A chooser's row for a wall row, refused where the wall row wears a mark: ONE rule with the
 *  cards, which a press never picks either. */
export function refusedChoice<T extends PickChoice>(
	choice: T,
	row: { keep_local?: boolean | null; keep_from_swaps?: boolean | null }
): T {
	const mark = refusedMark(row);
	return mark ? { ...choice, refused: refusedReason(mark) } : choice;
}

/** The sentence a press on a refused person, Site or tag says, and the switch that changes it. */
export function refusedWords(
	name: string | ToastPiece,
	mark: RefusedMark
): { words: ToastWords; change: string } {
	const local = mark === 'local';
	const tail = local
		? " is kept local, so it isn't offered"
		: " is kept out of swaps, so it isn't offered";
	return {
		words: typeof name === 'string' ? name + tail : [name, tail],
		change: local ? 'Allow enrichment' : 'Allow swapping'
	};
}

/** Take the mark off: Kept local through its own switch, which says what it did; "Don't swap"
 *  through the swap's, said here the same way. The wall reads its rows again on the change. */
async function letGo(kind: RefusalSubject, id: string, mark: RefusedMark): Promise<void> {
	if (mark === 'local') {
		await setKeptLocal(kind, id, false);
		return;
	}
	try {
		await setKeptFromSwaps(kind, id, false);
		toasts.show('It can be offered in a swap again', { tone: 'success' });
	} catch {
		toasts.show("That couldn't be changed", { tone: 'error' });
	}
}

/** A press on a refused person, Site or tag: why, and the switch that lets it go. */
export function sayRefused(
	kind: RefusalSubject,
	id: string,
	name: string,
	mark: RefusedMark
): void {
	const { words, change } = refusedWords(thing(kind, id, name), mark);
	toasts.show(words, {
		icon: REFUSED_ICON,
		action: { label: change, run: () => void letGo(kind, id, mark) }
	});
}

/** A press on a refused file: its own mark with its switch, or what above it keeps it out, as a
 *  link to that thing's page. Asked as it is pressed: one question, for the one file. */
export async function sayFileRefused(id: string): Promise<void> {
	let state: Awaited<ReturnType<typeof keptFromSwaps>>;
	try {
		state = await keptFromSwaps('asset', id);
	} catch {
		toasts.show("This file isn't offered in a swap", { icon: REFUSED_ICON });
		return;
	}
	const local = state.kept_local_here;
	if (local || state.kept_out_here) {
		const mark: RefusedMark = local ? 'local' : 'swap';
		const { words, change } = refusedWords('This file', mark);
		toasts.show(words, {
			icon: REFUSED_ICON,
			action: { label: change, run: () => void letGo('asset', id, mark) }
		});
		return;
	}
	const first = state.by[0];
	if (!first) {
		toasts.show("This file isn't offered because something it's filed under keeps it out", {
			icon: REFUSED_ICON
		});
		return;
	}
	// The name ends the sentence as a link to its page, where its own menu holds the switch.
	const lead =
		first.mark === 'local'
			? "This file is kept local because it's filed under "
			: "This file is kept out of swaps because it's filed under ";
	toasts.show([lead, thing(first.kind, first.id, first.name)], { icon: REFUSED_ICON });
}
