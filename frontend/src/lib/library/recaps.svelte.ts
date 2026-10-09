/* Recaps, as the screens see them: the list, the one being announced, and one recap opened. */

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

	/** Every recap of a day, a week, a month or a year, newest first, as the server drew them now. */
	readonly recaps = $derived<readonly RecapHead[]>(this.#mine?.recaps ?? []);

	/** The recap being announced, in the server's order: the longest period's first. */
	readonly announced = $derived<RecapHead | null>(this.#mine?.announced ?? null);

	/** Read the list. A read already out is joined rather than repeated. */
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

	/** The cross: this recap stops being announced, everywhere immediately. */
	/** The recap of one period, by its key (`periodKey`), or null where none was created. */
	of(period: string): RecapHead | null {
		return this.recaps.find((head) => head.period === period) ?? null;
	}

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
   with it which recaps a reader is shown and how many cards each has. */
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

/** The key the server files a period's recap under (`recaps_periods.Period.key`), from its span
 * and its first day: a week by its ISO year and week. */
export function periodKey(span: string, first: string): string | null {
	if (span === 'day') return `day:${first}`;
	if (span === 'month') return `month:${first.slice(0, 7)}`;
	if (span === 'year') return `year:${first.slice(0, 4)}`;
	if (span !== 'week') return null;
	const [year, month, day] = first.split('-').map(Number);
	const at = new Date(Date.UTC(year, month - 1, day));
	/* The ISO week is the one its Thursday falls in, counted from that year's first Thursday. */
	at.setUTCDate(at.getUTCDate() + 3 - ((at.getUTCDay() + 6) % 7));
	const isoYear = at.getUTCFullYear();
	const week = 1 + Math.floor((at.getTime() - Date.UTC(isoYear, 0, 1)) / 604_800_000);
	return `week:${isoYear}-W${String(week).padStart(2, '0')}`;
}
