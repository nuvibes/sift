/* The workbench: what is waiting, and taking a decision back.
 *
 * **Nothing here decides who may see anything.** Every count arriving from the server has already
 * been resolved against whoever is asking. Recomputing any of it here would be a second opinion
 * about concealment living in the browser, which is the one place it cannot be read.
 *
 * **The board is one request and is asked for by the screens that draw it.** It surveys every queue
 * and reads a page of the record with it, so nothing asks for it in passing.
 */

import { api } from '$lib/api/client';
import { toasts } from '$lib/shell/toasts.svelte';
import { type ToastWords } from '$lib/components/common/toast-pieces';
import { recorded } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';

/* One thing to draw on a card.
 *
 * The KIND comes from the server rather than being worked out from the id, because the queues draw
 * different things (a still from a file, a crop of a face), and an id says nothing about which.
 * Guessing here would be a second place to edit every time a queue is added.
 */
export type Preview = components['schemas']['PreviewView'];

/** One pile on the board. */
export type Queue = components['schemas']['QueueView'];

export type Board = components['schemas']['BoardView'];

/*
 * The board, held between visits.
 *
 * Every wall keeps its rows in a store like this one, draws the last answer immediately and
 * replaces it when a fresh one lands. Organize and Recently viewed do the same through this store:
 * data declared inside a component dies when the screen is left, and arriving would start from
 * nothing plus a round trip: an empty flash on the way in.
 *
 * Still asked for on every visit. The counts are the point of the screen and a stale one is worth
 * less than no screen at all. What changes is only that something true is on screen while the new
 * answer is in flight.
 */
export class Held {
	found = $state<Board | null>(null);

	/* A request already in flight, so several screens asking at once ask once. Not state: nothing
	   draws it, and making it reactive would re-run every effect that reads this store twice per
	   load. */
	#asking: Promise<Board> | null = null;

	/**
	 * Ask the server, and PUBLISH the ask while it is in flight.
	 *
	 * Every Organize screen asks on a cold arrival twice over (the screen's own `refresh` and the
	 * header's `ensure`), and one survey of every queue is not a cheap request, so the two must be
	 * able to see each other.
	 *
	 * **It publishes its own and never JOINS anybody else's, and the asymmetry is the whole
	 * design.** A refresh is what a screen calls after a decision has been written, and it has to
	 * see that write; handed an answer that was already in flight when the decision was made it
	 * would redraw the board as it was before, which is worse than the second request it saved.
	 * `ensure` has no such need (it fills in a crumb), so it is the half that joins.
	 *
	 * The slot is cleared only by the refresh that owns it. A later refresh publishes over it, and
	 * the earlier one finishing must not then take the newer ask away from anybody waiting on it.
	 */
	async refresh(): Promise<Board> {
		const asking = board().then((answer) => {
			this.found = answer;
			return answer;
		});
		this.#asking = asking;
		try {
			return await asking;
		} finally {
			if (this.#asking === asking) this.#asking = null;
		}
	}

	/**
	 * Ask again for the queues one screen draws, and put their answers into the board as held.
	 *
	 * For a queue's page asking again because the library moved or something was decided: it
	 * draws its own queue and the tabs beside it, and a survey of every queue costs what the
	 * slowest of them costs. With nothing held yet there is nothing to put them into, so the whole
	 * board is asked for. A named queue missing from the answer has gone from the board; one new
	 * on it is asked for whole. Merged into what is held when the answer lands.
	 */
	async refreshOnly(names: readonly string[]): Promise<Board> {
		if (this.found === null || names.length === 0) return this.refresh();
		const answer = await board(names);
		const held = this.found ?? answer;
		const known = new Set(held.queues.map((one) => one.name));
		if (answer.queues.some((one) => !known.has(one.name))) return this.refresh();
		const fresh = new Map(answer.queues.map((one) => [one.name, one]));
		const merged: Board = {
			...held,
			queues: held.queues.flatMap((one) => {
				if (!names.includes(one.name)) return [one];
				const now = fresh.get(one.name);
				return now ? [now] : [];
			})
		};
		this.found = merged;
		return merged;
	}

	/**
	 * The board, if it is not already here. For a screen that NEEDS it but does not own it.
	 *
	 * Every Organize screen wears a header that names the queue it belongs to (the trail's middle
	 * step), and that name comes from the board. The queue screens fetch it themselves for their
	 * own counts; the DETAIL screens (one pile of faces, one person's) fetch their own thing and
	 * never the board, so on a direct hit or a refresh their trail would read *Organize > Organize*,
	 * the middle step falling back to the word it could not replace.
	 *
	 * Asked once and shared: the header on every one of these calls this, and without the guard a
	 * screen that draws two of them would ask twice for an answer that is the same both times.
	 * Silent on failure, deliberately: this fills in a crumb, and a screen that loaded perfectly
	 * well must not report an error because the word above it could not be improved.
	 */
	async ensure(): Promise<void> {
		if (this.found !== null) return;
		try {
			// Whatever is already in flight, from either half. `refresh` publishes its own, so a
			// screen that asks for itself and a header that asks for a crumb share one request.
			await (this.#asking ?? this.refresh());
		} catch {
			// See above: a crumb that stays generic is not a failure worth telling anybody about.
		}
	}
}

export const heldBoard = new Held();

/** The board, or only the queues `only` names. */
export async function board(only?: readonly string[]): Promise<Board> {
	return only ? api.get<Board>('/workbench', { query: { only } }) : api.get<Board>('/workbench');
}

/** What taking one decision back did: whether anything went back, and how much of it. */
export type Undone = components['schemas']['UndoneView'];

export async function undo(id: string): Promise<Undone> {
	return api.post<Undone>(`/workbench/decisions/${encodeURIComponent(id)}/undo`, {});
}

/**
 * The toast for an undo answer: `whole` when every act went back, `none` when nothing did.
 *
 * The one reader of that answer for every toast that says how an undo went. A decision of many
 * acts (a batch of renames) can go back in part, and the server writes the line for that, with
 * the counts and the reason; a yes alone would read as every one of them having gone back.
 */
export function undoneLine(answer: Undone, whole: string, none: string): string {
	if (answer.said) return answer.said;
	return answer.undone ? whole : none;
}

/**
 * That a decision was made, for anything drawing counts that a decision changes.
 *
 * The board is not asked for on every page:
 * it is loaded once when a screen opens, and every decision after that is made inside a panel the
 * board does not know about. Without this the counts on the chips would go stale the moment
 * somebody answered something, and only a page reload would put them right.
 *
 * A counter rather than an event, because the screens that care are already reading state: an
 * effect that reads `stamp` re-runs when it moves and nothing has to be subscribed to or torn down.
 * It reads nothing those effects write, so it cannot re-trigger them by way of their own work.
 */
class Answered {
	stamp = $state(0);

	/** Something was decided, or taken back. Anything showing a count should ask again.
	 *
	 * And any history on screen: a decision is a line in the thread of whatever it was about, so
	 * it rings `recorded` too. Rung from here rather than listened to by each history, so a thread
	 * has ONE bell for "this tab wrote something" and not one per kind of writer. */
	changed(): void {
		this.stamp += 1;
		recorded.changed();
	}
}

export const answered = new Answered();

/**
 * Say what a decision did, and offer to take it back.
 *
 * Every decision made in the workbench goes through here, so none of them can be written without a
 * way back. The toast is the immediate half; the permanent half is the record on the board, because
 * a wrong bulk apply is usually noticed a day later rather than in the four seconds a toast lasts.
 *
 * A decision with no record still says what it did. That happens where the server has nowhere to
 * write one, and a message with no Undo beats no message at all.
 *
 * Which is why `decisionId` is allowed to be NOTHING, spelled out as `null`. A plain string would
 * send the callers with no record to offer reaching for a field their answer does not carry, handed
 * `undefined`: the right toast for the wrong reason, with only the type checker knowing.
 */
export function decided(
	message: ToastWords,
	decisionId: string | null,
	options: { after?: () => void | Promise<void> } = {}
): void {
	answered.changed();
	toasts.show(message, {
		tone: 'success',
		action: decisionId
			? {
					label: 'Undo',
					run: () => {
						void (async () => {
							try {
								const put = await undo(decisionId);
								toasts.show(
									undoneLine(put, "That's put back", 'There was nothing left to put back')
								);
								answered.changed();
								await options.after?.();
							} catch {
								toasts.show("That couldn't be put back", { tone: 'error' });
							}
						})();
					}
				}
			: undefined
	});
}
