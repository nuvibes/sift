/*
 * The answer to one period (`GET /api/insights`), as Insights and its Stats view both read it.
 *
 * Asked again when the place changes, and when the reader's clock does: the server says a time of
 * day on that clock. An answer to a period somebody has already left is dropped, not drawn over the
 * newer one. A screen hands its bells to `reread`, which reads again in place, so the page as
 * drawn stays until a different answer lands; one read at a time, and one more after it if a bell
 * rang meanwhile.
 */
import { api } from '$lib/api/client';
import type { InsightsPage, Place } from '$lib/components/insights/period';
import { clock } from '$lib/shell/clock.svelte';

/* LIVE: followed by routes/insights/+page.svelte (the period's answer, on libraryChanges and assetState, through reread) */
export class PeriodAnswer {
	answer = $state<InsightsPage | null>(null);
	/** Bumped when a period's answer lands, never by a re-read: a screen's arrival keys on it. */
	arrival = $state(0);
	failed = $state(false);
	loading = $state(true);

	#place: () => Place;
	#rereading = false;
	#owed = false;

	/** Built while a screen sets itself up: it reads the place it is given until the screen goes. */
	constructor(place: () => Place) {
		this.#place = place;
		$effect(() => {
			const { period, at } = this.#place();
			void clock.hours;
			let current = true;
			this.loading = true;
			this.failed = false;
			api
				.get<InsightsPage>('/insights', { query: { period, at: at ?? undefined } })
				.then((found) => {
					if (!current) return;
					this.answer = found;
					this.arrival += 1;
					this.loading = false;
				})
				.catch(() => {
					if (!current) return;
					this.failed = true;
					this.loading = false;
				});
			return () => {
				current = false;
			};
		});
	}

	reread = async (): Promise<void> => {
		if (this.#rereading) {
			this.#owed = true;
			return;
		}
		this.#rereading = true;
		const { period, at } = this.#place();
		try {
			const found = await api.get<InsightsPage>('/insights', {
				query: { period, at: at ?? undefined }
			});
			const now = this.#place();
			if (this.loading || period !== now.period || at !== now.at) return;
			if (JSON.stringify(found) !== JSON.stringify(this.answer)) this.answer = found;
		} catch {
			// The page as drawn stays.
		} finally {
			this.#rereading = false;
			if (this.#owed) {
				this.#owed = false;
				void this.reread();
			}
		}
	};
}
