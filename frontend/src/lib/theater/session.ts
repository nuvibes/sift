/*
 * One Theater session, a wall opened and closed: its cells' sittings cannot say how long the WALL
 * ran. Both reports are keyed on the name minted here, so a lost opening costs nothing; while open
 * and on screen it beats, keeping Sift in eco mode.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { newSittingId } from '$lib/player/sitting.svelte';

/** The server's ceilings, so a wall left open past them is recorded at the ceiling, not refused. */
const LONGEST_MS = 7 * 24 * 60 * 60 * 1000;
const LONGEST_SOURCE = 1000;

/** How often an open wall beats: a third of eco mode's minute, so one lost beat never ends it. */
export const BEAT_EVERY_MS = 20_000;

/** What a wall is, as the closing report records it, in the report's generated fields. */
export type WallFacts = Required<
	Pick<components['schemas']['SessionReport'], 'layout' | 'cells' | 'arrangement' | 'sources'>
>;

export class TheaterSession {
	/** The name every report of this session carries, and every cell's sitting names it by. */
	readonly id = newSittingId();
	#from = performance.now();
	/** Every different file the wall has shown, so the close can say how many. */
	#shown = new Set<string>();
	#ended = false;
	#beating: ReturnType<typeof setInterval> | undefined;

	/** A file has come on screen in one of the wall's cells. */
	shown(file: string): void {
		this.#shown.add(file);
	}

	/** How many different files the wall has shown so far. */
	get files(): number {
		return this.#shown.size;
	}

	/** Say the wall is open. Fire and forget: nothing waits on it, and a lost one costs nothing. */
	open(): void {
		void api.post(`/theater/sessions/${this.id}`, { body: {} }).catch(() => {});
		this.#beat();
		this.#beating = setInterval(() => this.#beat(), BEAT_EVERY_MS);
	}

	#beat(): void {
		if (document.visibilityState === 'visible') void api.post('/theater/watching').catch(() => {});
	}

	/** Say the wall has closed, once; `keepalive`, since a wall often ends with its window. */
	close(facts: WallFacts): void {
		clearInterval(this.#beating);
		if (this.#ended) return;
		this.#ended = true;
		void api
			.post(`/theater/sessions/${this.id}`, {
				body: {
					elapsed_ms: Math.min(LONGEST_MS, Math.max(0, Math.round(performance.now() - this.#from))),
					ended: true,
					layout: facts.layout,
					cells: facts.cells,
					arrangement: facts.arrangement,
					sources: facts.sources?.map((source) => source.slice(0, LONGEST_SOURCE)) ?? null,
					files: this.files
				},
				keepalive: true
			})
			.catch(() => {});
	}
}
