/*
 * How long somebody sat with a file that has no end of its own, reported in two pieces: an empty
 * one on arrival earns the view, the time goes when it is left. Not `dwell.svelte.ts`, which is how
 * long a run rests on a still.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { FilledClock } from '$lib/player/inside';

type ViewReport = components['schemas']['ViewReport'];

type SittingScreen = NonNullable<ViewReport['screen']>;
export type OpenedFrom = NonNullable<ViewReport['opened_from']>;

/**
 * WHERE a sitting happened, on every piece; the server keeps the first. The generated type's
 * fields.
 */
export type SittingPlace = Pick<
	ViewReport,
	'opened_from' | 'opened_from_id' | 'searched' | 'loop' | 'kept_filter' | 'theater_session'
> & { screen: SittingScreen };

/** A sitting carried on in the corner was opened from what the panel's was. */
export function inTheCorner(from: SittingPlace | null | undefined): SittingPlace {
	if (!from) return { screen: 'corner' };
	return {
		screen: 'corner',
		opened_from: from.opened_from ?? null,
		opened_from_id: from.opened_from_id ?? null,
		searched: from.searched ?? null,
		loop: from.loop ?? null,
		kept_filter: from.kept_filter ?? null
	};
}

/**
 * Names one sitting across its pieces. `getRandomValues`, since `randomUUID` is undefined over the
 * plain http Sift is served on.
 */
export function newSittingId(): string {
	const bytes = new Uint8Array(16);
	crypto.getRandomValues(bytes);
	return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** A picture handed to the corner is the same sitting, or each hand-over counts a view. */
export interface SittingBaton {
	asset: string;
	id: string;
	from: number;
	sent: number | null;
	place: SittingPlace;
	magnified: boolean;
	filled: number;
}

const live = new Set<Sitting>();

export function noteMagnified(assetId: string): void {
	for (const one of live) if (one.on === assetId) one.magnify();
}

/** The screen it is taken from reports nothing more; whoever resumes sends the closing piece. */
export function handOffSitting(assetId: string, screen: SittingScreen): SittingBaton | null {
	for (const one of live) {
		if (one.on === assetId && one.screen === screen) return one.handOff();
	}
	return null;
}

export class Sitting {
	#on: string | null = null;
	#from = 0;
	#id = '';
	/** Zero is not nothing: a piece has gone, so the next is not a second view. */
	#sent: number | null = null;
	#place: SittingPlace | null = null;
	#magnified = false;
	readonly #filled = new FilledClock();

	get on(): string | null {
		return this.#on;
	}

	get screen(): SittingScreen | null {
		return this.#place?.screen ?? null;
	}

	/** Whatever was on screen is finished first: stepping to the next picture fires no unmount. */
	start(assetId: string, place: SittingPlace): void {
		this.end();
		this.#on = assetId;
		this.#from = performance.now();
		this.#sent = 0;
		this.#id = newSittingId();
		this.#place = place;
		this.#magnified = false;
		this.#filled.start();
		live.add(this);
		const sitting = this.#id;
		void api
			.post(`/assets/${assetId}/view`, {
				body: {
					...place,
					watch_ms: 0,
					already_reported_ms: null,
					position_ms: 0,
					ended: false,
					sitting
				}
			})
			/* Put back if it did not land, while this is still the file on screen. */
			.catch(() => {
				if (this.#on === assetId) this.#sent = null;
			});
	}

	/** `keepalive`: the commonest exit is closing the tab. */
	end(): void {
		if (this.#on === null) return;
		const was = this.#on;
		const spent = Math.max(0, Math.round(performance.now() - this.#from));
		const before = this.#sent;
		const sitting = this.#id;
		const place = this.#place;
		const magnified = this.#magnified;
		this.#filled.stop();
		const filled = this.#filled.read();
		this.#on = null;
		this.#sent = null;
		this.#id = '';
		this.#place = null;
		live.delete(this);
		void api
			.post(`/assets/${was}/view`, {
				// A still has no position; zero clears a resume point. The same sitting id as the
				// first piece.
				body: {
					...place,
					watch_ms: spent,
					already_reported_ms: before,
					position_ms: 0,
					ended: false,
					sitting,
					fullscreen_ms: filled,
					magnified
				},
				keepalive: true
			})
			.catch(() => {});
	}

	handOff(): SittingBaton | null {
		if (this.#on === null || this.#place === null) return null;
		this.#filled.stop();
		const baton: SittingBaton = {
			asset: this.#on,
			id: this.#id,
			from: this.#from,
			sent: this.#sent,
			place: this.#place,
			magnified: this.#magnified,
			filled: this.#filled.read()
		};
		this.#on = null;
		this.#sent = null;
		this.#id = '';
		this.#place = null;
		live.delete(this);
		return baton;
	}

	/** Nothing is sent now: the view was earned when the sitting began. */
	resume(baton: SittingBaton): void {
		this.end();
		this.#on = baton.asset;
		this.#from = baton.from;
		this.#sent = baton.sent;
		this.#id = baton.id;
		this.#place = baton.place;
		this.#magnified = baton.magnified;
		this.#filled.start();
		this.#filled.carry(baton.filled);
		live.add(this);
	}

	magnify(): void {
		if (this.#on !== null) this.#magnified = true;
	}
}

/** One per screen: the corner and a full-size view can be mounted together. */
export function newSitting(): Sitting {
	return new Sitting();
}
