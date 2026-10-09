// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * What will not go in a swap (Kept local or "Don't swap", on the thing or above a file): the mark
 * it wears, and the sentence a press says with the way to change it. A file's inherited mark links
 * to the thing that carries it, whose page decides for every file.
 */

import { setKeptLocal } from '$lib/entity/enrichment.svelte';
import { toasts } from '$lib/shell/toasts.svelte';
import { thing, type ToastPiece, type ToastWords } from '$lib/components/common/toast-pieces';
import { keptFromSwaps, setKeptFromSwaps, type RefusalSubject } from '$lib/components/swap/swap';
import type { PickChoice } from '$lib/components/common/verbs';

/** Which mark keeps a thing back: Kept local, or "Don't swap". */
export type RefusedMark = 'local' | 'swap';

/** The mark a wall row wears, or null; Kept local first, as it keeps back more. */
export function refusedMark(row: {
	keep_local?: boolean | null;
	keep_from_swaps?: boolean | null;
}): RefusedMark | null {
	if (row.keep_local) return 'local';
	if (row.keep_from_swaps) return 'swap';
	return null;
}

const REFUSED_ICON = 'do_not_disturb_on';

/** Why a chooser's row cannot be chosen, in the walls' words, without the name it shows. */
function refusedReason(mark: RefusedMark): string {
	return mark === 'local'
		? "Kept local, so it isn't offered"
		: "Kept out of swaps, so it isn't offered";
}

/** A chooser's row refused as its card would be: one rule. */
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

/** Take the mark off through its own switch; the wall reads its rows again on the change. */
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

/** A press on a refused file: its own mark, or a link to what above it keeps it out. */
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
