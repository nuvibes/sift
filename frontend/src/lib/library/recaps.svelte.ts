/* Recaps, as the screens see them: the list, the one being announced, and one recap opened.
 *
 * ## What the server decides, and what is left here
 *
 * Everything a reader may be shown is decided on the server when it is asked: which recaps a locked
 * vault leaves out, which cards are absent or drawn as a locked tile, the words on every card, the
 * heading and the one hidden line. Nothing here knows what is hidden, and nothing here says a
 * sentence: a card's words arrive as pieces and `HistorySentence` draws them. What is left is
 * holding the answer, asking again when what this account may see has moved, and the cross.
 *
 * ## One list, read by three places
 *
 * The card at the top of Insights, the Recaps block on the same page, and the quiet line on
 * Browse's header all read `GET /api/insights/recaps`, whose `announced` is the one recap being
 * announced. Three copies of the list would disagree the moment the cross was pressed in one of
 * them, so they share this one, and a read already in flight is shared rather than asked again.
 *
 * ## Whose list it is
 *
 * Held against the account it was read for and drawn only while that account is the one signed in.
 * A second person signing in on the same window must never see the first one's "Your September",
 * not even for the moment it takes the new list to arrive.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { libraryChanges, mine } from '$lib/library/changes.svelte';
import { session } from '$lib/shell/session.svelte';

/** A recap as a list names it: its title, the days it covers, and how many cards it has now. */
export type RecapHead = components['schemas']['RecapHead'];

/** One recap opened: its heading and its cards, drawn for the reader's vault state now. */
export type Recap = components['schemas']['Recap'];

/** One card of a recap. A card whose `hidden` is true is a locked tile and says nothing. */
export type RecapCard = components['schemas']['RecapCard'];

type RecapList = components['schemas']['RecapList'];

class RecapShelf {
	/* The answer and the account it was read for. */
	#held = $state<{ who: string; list: RecapList } | null>(null);
	/* The read in flight, shared by every place that asks while it is out. */
	#asking: Promise<void> | null = null;

	readonly #mine = $derived(
		this.#held !== null && this.#held.who === (session.viewer?.id ?? '') ? this.#held.list : null
	);

	/** Whether the list has been read for the account signed in now. */
	readonly loaded = $derived(this.#mine !== null);

	/** Every recap of a week, a month or a year, newest first, as the server drew them now. */
	readonly recaps = $derived<readonly RecapHead[]>(this.#mine?.recaps ?? []);

	/** The recap being announced: made in the last week, neither opened nor closed. */
	readonly announced = $derived<RecapHead | null>(this.#mine?.announced ?? null);

	/**
	 * Read the list. A read already out is joined rather than repeated.
	 *
	 * Never throws: every place that draws this list draws nothing while it has none, so a refusal
	 * is the same drawing as an empty answer, and a failed read of a quiet line must not become an
	 * error on Browse.
	 */
	load(): Promise<void> {
		this.#asking ??= this.#read().finally(() => (this.#asking = null));
		return this.#asking;
	}

	async #read(): Promise<void> {
		const who = session.viewer?.id ?? '';
		try {
			const list = await api.get<RecapList>('/insights/recaps');
			this.#held = { who, list };
		} catch {
			/* Nothing to draw; what was held stays until a read succeeds. */
		}
	}

	/**
	 * The cross: this recap stops being announced, everywhere at once. It stays in the list, to be
	 * opened whenever.
	 *
	 * Taken off the screen before the server answers, because a line that stays after its cross is
	 * pressed reads as a cross that does not work. If the server refuses, the list is read again and
	 * says what is true.
	 */
	async dismiss(id: string): Promise<void> {
		this.#forget(id);
		try {
			await api.post<void>(`/insights/recaps/${encodeURIComponent(id)}/dismiss`);
		} catch {
			await this.load();
		}
	}

	/** Opening a recap ends its announcement: the server wrote that when it drew the recap. */
	opened(id: string): void {
		this.#forget(id);
	}

	#forget(id: string): void {
		const held = this.#held;
		if (held === null || held.list.announced?.id !== id) return;
		this.#held = { who: held.who, list: { ...held.list, announced: null } };
	}
}

export const recapShelf = new RecapShelf();

/* What this account may see moved (the vault opened or shut, something hidden or shared), and
   with it which recaps a reader is shown and how many cards each has. Only a list somebody has
   already asked for is asked again. */
libraryChanges.subscribe(() => {
	if (recapShelf.loaded) void recapShelf.load();
});

/* A recap opened or its announcement dismissed on another of this account's tabs: the shelf is
   this account's own list, so it is asked again and the announcement goes here too. */
mine.subscribe(() => {
	if (recapShelf.loaded) void recapShelf.load();
});

/** One recap, drawn for the reader's vault state now. Opening it ends its announcement. */
export async function readRecap(id: string): Promise<Recap> {
	const recap = await api.get<Recap>(`/insights/recaps/${encodeURIComponent(id)}`);
	recapShelf.opened(recap.id);
	return recap;
}
