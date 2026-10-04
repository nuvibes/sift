/*
 * One Theater session: a wall opened, and the wall closed.
 *
 * Every cell already writes a sitting for each file it shows. What those rows cannot say is the
 * WALL (nine cells running for an hour are nine hours of sittings and one hour of somebody's
 * evening), so the wall reports itself as well: once when it opens, with nothing on it but the
 * time, and once when it closes, with what it was. Both are the same report, and the server keys
 * them on the name minted here, so a lost opening costs nothing and a late one cannot reopen what
 * has closed. See the server's `theater/sessions.py`.
 *
 * Nothing polls. A session that never closes (a browser killed from outside) is left without an
 * end, and its cells' sittings, which carry its name, say when it was last in use.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { newSittingId } from '$lib/player/sitting.svelte';

/**
 * The server's ceilings on a session report, so a wall left open past them is recorded at the
 * ceiling rather than refused: a refusal on a keepalive nobody reads loses the whole session.
 * A week, and a saved cell's own longest source (`MAX_SESSION_MS` and `MAX_SOURCE` there).
 */
const LONGEST_MS = 7 * 24 * 60 * 60 * 1000;
const LONGEST_SOURCE = 1000;

/**
 * What a wall is, as the session's closing report records it: the layout, how many cells, the saved
 * wall it came from, and each drawn cell's source. The report's own fields, from generated types.
 */
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
	}

	/**
	 * Say the wall has closed, and what it was. Once: a second close is a no-op.
	 *
	 * `keepalive`, because the commonest way a wall ends is the window closing, and an ordinary
	 * request is cancelled when the page goes.
	 */
	close(facts: WallFacts): void {
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
