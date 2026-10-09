/* The workbench: what is waiting, and taking a decision back. */

import { api } from '$lib/api/client';
import { toasts } from '$lib/shell/toasts.svelte';
import { type ToastWords } from '$lib/components/common/toast-pieces';
import { recorded } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';

/* One thing to draw on a card. */
export type Preview = components['schemas']['PreviewView'];

/** One pile on the board. */
export type Queue = components['schemas']['QueueView'];

export type Board = components['schemas']['BoardView'];

/* The board, held between visits. Every wall keeps its rows in a store like this one, draws the
 * last answer immediately and replaces it when a fresh one lands. */
export class Held {
	found = $state<Board | null>(null);

	/* A request already in flight, so several screens asking together ask once. */
	#asking: Promise<Board> | null = null;

	/** Ask the server, and PUBLISH the ask while it is in flight. */
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

	/** Ask again for the queues one screen draws, and put their answers into the board as held. */
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

	/** The board, if it is not already here. For a screen that NEEDS it but does not own it. */
	async ensure(): Promise<void> {
		if (this.found !== null) return;
		try {
			// Whatever is already in flight, from either half.
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

/** The toast for an undo answer: `whole` when every act went back, `none` when nothing did. */
export function undoneLine(answer: Undone, whole: string, none: string): string {
	if (answer.said) return answer.said;
	return answer.undone ? whole : none;
}

/** That a decision was made, for anything drawing counts that a decision changes. */
class Answered {
	stamp = $state(0);

	/** Something was decided, or taken back. Anything showing a count should ask again. */
	changed(): void {
		this.stamp += 1;
		recorded.changed();
	}
}

export const answered = new Answered();

/** Say what a decision did, and offer to take it back. */
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
