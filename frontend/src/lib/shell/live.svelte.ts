/*
 * One connection for the session, saying when what it may see has changed. What arrives is a word,
 * never a row: screens re-ask the ordinary, permission-checked endpoints.
 */

import { api, API_PREFIX, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import {
	arrivals,
	assetState,
	downloadChanges,
	jobChanges,
	libraryChanges,
	mine,
	sameMusicChanges,
	screenChanges,
	settingChanges
} from '$lib/library/changes.svelte';
import { screenOffer } from '$lib/remote/offer.svelte';

type LiveState = components['schemas']['LiveState'];

const RECONNECT_MS = 2000;

/**
 * After the account is at its connection cap: what clears it is a tab closing elsewhere, a human
 * timescale. Its own number, not the server's `Retry-After`.
 */
const FULL_AGAIN_MS = 30_000;

const TOO_MANY = 429;

/** A handshake has no 403 to carry. */
const POLICY_VIOLATION = 1008;

const TRY_AGAIN_LATER = 1013;

/**
 * How often screens re-read while there is no connection, so a wall does not freeze silently. A
 * safety net under a reconnect, not a poll.
 */
const BLIND_POLL_MS = 30_000;

type NextStep = 'give-up' | 'come-back' | 'ask-whether';

/** `opening` says nothing: a line during the second a page connects would be noise. */
export type Standing = 'opening' | 'live' | 'reconnecting' | 'full' | 'stopped';

/** Never silent about a screen that has stopped following. */
export function liveWords(standing: Standing): string {
	if (standing === 'full') return 'Live updates are paused: too many windows are open';
	if (standing === 'reconnecting') return 'Reconnecting\u2026';
	if (standing === 'stopped') return 'Live updates are off';
	return '';
}

/**
 * A refused handshake and a dropped connection both arrive as 1006, so a refusal is told apart by
 * asking the plain read. Its own function, so it can be checked without a socket.
 */
export function nextStep(code: number, opened: boolean): NextStep {
	if (code === POLICY_VIOLATION) return 'give-up';
	// Too slow to keep up; the mark says whether anything needs re-reading.
	if (code === TRY_AGAIN_LATER) return 'come-back';
	// It worked, then stopped.
	if (opened) return 'come-back';
	return 'ask-whether';
}

/** The second half of telling a refusal from a drop, read before connecting and after a refusal. */
function isFull(error: unknown): boolean {
	return error instanceof ApiError && error.status === TOO_MANY;
}

function streamUrl(since: string | null): string {
	const url = new URL(API_PREFIX + '/live/stream', window.location.href);
	url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
	if (since !== null) url.searchParams.set('since', since);
	return url.toString();
}

/* What each subject does when it arrives; a missing name is a build failure. Mostly bells. */
const HANDLED: Record<LiveState['about'][number], (state: LiveState) => void> = {
	library: () => libraryChanges.changed(),
	arrivals: () => arrivals.changed(),
	jobs: () => jobChanges.changed(),
	downloads: () => downloadChanges.changed(),
	settings: () => settingChanges.changed(),
	mine: () => mine.changed(),
	screens: () => screenChanges.changed(),
	same_music: () => sameMusicChanges.changed(),

	/* Rows, not a question; past what a message carries, re-read rather than apply half. */
	opinions: (state) => {
		for (const opinion of state.opinions ?? []) assetState.changed(opinion);
		if (state.more_opinions) arrivals.changed();
	},

	/* A command from this user's phone; one missed is one that must not happen late. */
	remote: (state) => screenOffer.receive(state.commands ?? [])
};

export class LiveFeed {
	live = $state(false);

	standing = $state<Standing>('opening');

	#socket: WebSocket | null = null;
	#reconnect: ReturnType<typeof setTimeout> | null = null;
	#blind: ReturnType<typeof setInterval> | null = null;
	#running = false;

	/* Compared, never read: it closes the gap between a page loading and the connection opening. */
	#marker: string | null = null;

	start(): void {
		if (this.#running) return;
		this.#running = true;
		/*
		 * One timer for the session, idle while connected, or a flapping connection would reset it
		 * forever.
		 */
		this.#blind = setInterval(() => {
			if (!this.live) this.#readAgain();
		}, BLIND_POLL_MS);
		void this.#begin();
	}

	stop(): void {
		this.#running = false;
		this.#marker = null;
		this.standing = 'opening';
		if (this.#blind !== null) {
			clearInterval(this.#blind);
			this.#blind = null;
		}
		this.#teardown();
	}

	/** Every bell: what was missed is unknown. `remote` has no re-read. */
	#readAgain(): void {
		libraryChanges.changed();
		arrivals.changed();
		jobChanges.changed();
		downloadChanges.changed();
		settingChanges.changed();
		mine.changed();
		screenChanges.changed();
	}

	async #begin(): Promise<void> {
		// Asked before connecting, so a change in between is still found.
		try {
			this.#marker = (await api.get<LiveState>('/live')).marker;
		} catch (error) {
			/* ALREADY AT THE CAP: a socket would be refused without saying why. */
			if (isFull(error)) {
				this.standing = 'full';
				this.#later(FULL_AGAIN_MS);
				return;
			}
			// With no mark, a first message says to re-read: the safe direction.
			this.#marker = null;
		}
		this.#connect();
	}

	#connect(): void {
		this.#teardown();
		if (!this.#running) return;

		const socket = new WebSocket(streamUrl(this.#marker));
		this.#socket = socket;

		let opened = false;

		socket.onopen = () => {
			opened = true;
			this.live = true;
			this.standing = 'live';
		};

		socket.onmessage = (event: MessageEvent) => {
			this.apply(JSON.parse(String(event.data)) as LiveState);
		};

		socket.onclose = (event: CloseEvent) => {
			this.live = false;
			this.#socket = null;
			if (this.#running) this.#closed(event.code, opened);
		};
	}

	/** Public so the decision can be checked without a socket. */
	apply(state: LiveState): void {
		this.#marker = state.marker;

		/* Read as "if it is there": the shell and the server may be different versions. */
		for (const about of state.about ?? []) {
			if (Object.hasOwn(HANDLED, about)) HANDLED[about](state);
		}
	}

	get marker(): string | null {
		return this.#marker;
	}

	#closed(code: number, opened: boolean): void {
		const step = nextStep(code, opened);
		if (step === 'give-up') {
			/* Told plainly that this account may not; the screen says so. */
			this.standing = 'stopped';
			return;
		}
		if (step === 'come-back') {
			this.standing = 'reconnecting';
			this.#later();
			return;
		}
		this.standing = 'reconnecting';
		void this.#askWhether();
	}

	/**
	 * Asks and keeps no part of the answer: adopting the new mark would hide every change the
	 * screen has not been brought up to.
	 */
	async #askWhether(): Promise<void> {
		try {
			await api.get<LiveState>('/live');
		} catch (error) {
			if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
				this.standing = 'stopped';
				return;
			}
			/* At the cap: worth coming back from, not soon. */
			if (isFull(error)) {
				this.standing = 'full';
				this.#later(FULL_AGAIN_MS);
				return;
			}
		}
		this.#later();
	}

	#later(wait = RECONNECT_MS): void {
		if (!this.#running) return;
		this.#reconnect = setTimeout(() => this.#connect(), wait);
	}

	#teardown(): void {
		this.live = false;
		if (this.#reconnect !== null) {
			clearTimeout(this.#reconnect);
			this.#reconnect = null;
		}
		if (this.#socket !== null) {
			// Unhooked first: `close()` fires `onclose`, which would dial back in.
			this.#socket.onclose = null;
			this.#socket.onmessage = null;
			this.#socket.close();
			this.#socket = null;
		}
	}
}

export const live = new LiveFeed();
