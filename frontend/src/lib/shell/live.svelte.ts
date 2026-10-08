/* One connection for the whole session, telling every screen when what it may see has changed.
 *
 * Everything in Sift is scoped, and most of it can change underneath somebody. A share taken
 * back, a folder removed, a file put into a collection or taken out of one: each changes what
 * several screens should be showing, and none of them produces anything a screen would notice on
 * its own.
 *
 * **What arrives is a word, never a row.** The server says "the library moved for you" and this
 * rings the bell the application already has; the screens then re-ask the ordinary endpoints,
 * which go through the permission layer like every other read. So the connection is not a second
 * way to read anything, and there is nothing on it the vault has no say over.
 *
 * Here rather than in any one screen, because it is not any one screen's: it outlives navigation,
 * and a store opened per screen would open and close a connection on every one.
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

/** After a connection drops for a reason that is not "you may not do this". */
const RECONNECT_MS = 2000;

/**
 * After the server says this account already has as many connections as it may have.
 *
 * A refused handshake and a dropped one arrive identically (see `nextStep`), so the plain read
 * is what tells the two apart: at the cap it refuses, and a browser over the cap waits here rather
 * than coming back on `RECONNECT_MS` for as long as the tab stays open.
 *
 * Thirty seconds because of what clears the cap: a tab somewhere else being closed, which is a
 * human timescale. Coming back sooner is a poll on a question whose answer cannot have changed.
 *
 * Its own number rather than the `Retry-After` the server sends, deliberately: the header is a
 * request and this is a decision, and reading it would mean the wait a person waits is set by
 * whichever server version they happen to be talking to.
 */
const FULL_AGAIN_MS = 30_000;

/** The status the plain read answers with when this account is at the connection cap. */
const TOO_MANY = 429;

/** The WebSocket close code for "you may not do this": a handshake has no 403 to carry. */
const POLICY_VIOLATION = 1008;

/** And the one for "not now": what a connection that could not keep up is closed with. */
const TRY_AGAIN_LATER = 1013;

/**
 * How often the screens are asked to re-read while there is no connection at all.
 *
 * **The fallback that stops a wall freezing.** Everything in Sift that draws a list reads it once
 * and is then TOLD when it moved, so with no connection nothing moves: tiles would sit on
 * "Importing..." while the files behind them finished, with nothing on screen saying anything was
 * wrong. Every one of those screens is already listening to the bells below, so the honest answer
 * to "we cannot be told" is to do what a message we could not fit already does: ask them to
 * re-read. The file's own `more_opinions` handling is the same decision.
 *
 * Thirty seconds, which is the wait the connection cap already uses for the same reason: what
 * clears it is a person closing a window somewhere. It is deliberately far slower than the two
 * seconds an ordinary reconnect takes. This is the safety net under a connection that is coming
 * back, not a poll standing in for one, and the whole reason the connection exists is that it spares a
 * page read per screen per second.
 */
const BLIND_POLL_MS = 30_000;

/** What to do about a connection that has gone away. */
type NextStep = 'give-up' | 'come-back' | 'ask-whether';

/**
 * Where this browser stands with the server, in the words a screen needs to say it.
 *
 * `opening` is the first connection being made and says nothing: a line on screen during the second
 * a page takes to connect would be noise on every load. The other three are each a different real
 * situation and only one of them clears by itself.
 */
export type Standing = 'opening' | 'live' | 'reconnecting' | 'full' | 'stopped';

/**
 * One quiet line saying why the screen is not keeping up, or nothing when it is.
 *
 * Here rather than in the markup so the words are written once and can be asserted on. What it must
 * never do is stay silent about a screen that has stopped following: the one thing worse than a
 * stale wall is a stale wall that looks current.
 */
export function liveWords(standing: Standing): string {
	if (standing === 'full') return 'Live updates are paused: too many windows are open';
	if (standing === 'reconnecting') return 'Reconnecting\u2026';
	if (standing === 'stopped') return 'Live updates are off';
	return '';
}

/**
 * A connection has gone away. Decide what to do about it.
 *
 * More careful than it looks, because **a refused connection and a dropped one do not arrive as
 * different close codes.** A handshake refused before it completes (not signed in, an origin that
 * is not allowed, too many connections already open for this account) has no socket to carry a
 * close frame, so the browser reports 1006 and never sees the server's reason. 1006 is also exactly
 * what a real network failure looks like.
 *
 * So 1006 alone cannot be trusted either way: coming back from every one of them hammers the server
 * forever on behalf of somebody who is never going to be let in, and coming back from none of them
 * means a screen that gives up for good the first time a laptop lid closes. What settles it is
 * asking the plain read, which sits behind exactly the same session check the connection does.
 *
 * A function of its own so the decision can be checked without a socket. The socket around it is
 * the same shape as the job feed's, and is where a fault would be obvious; this is where one would
 * not be.
 */
export function nextStep(code: number, opened: boolean): NextStep {
	// "You may not do this", carried intact because by then there was a connection to carry it.
	if (code === POLICY_VIOLATION) return 'give-up';
	// Too slow to keep up. Coming back is cheap: the mark says whether anything needs re-reading.
	if (code === TRY_AGAIN_LATER) return 'come-back';
	// It worked, then stopped. A reload, a proxy timeout, a lid. Nothing to ask anybody.
	if (opened) return 'come-back';
	return 'ask-whether';
}

/**
 * Whether the plain read refused because this account already holds every connection it may.
 *
 * Its own function for the reason `nextStep` is one: this is the second half of telling a refusal
 * from a dropped connection, and it is read in two places: before the first socket is opened, and
 * after one has been refused.
 */
function isFull(error: unknown): boolean {
	return error instanceof ApiError && error.status === TOO_MANY;
}

function streamUrl(since: string | null): string {
	const url = new URL(API_PREFIX + '/live/stream', window.location.href);
	url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
	if (since !== null) url.searchParams.set('since', since);
	return url.toString();
}

/* What each subject does when it arrives, and the only place that decides.
 *
 * One entry per subject the server can send, so that adding a name on the server and forgetting it
 * here is a build failure rather than a message that lands and does nothing. A check holds the two
 * lists to each other, and holds every bell below to having somebody listening for it.
 *
 * Nothing here knows what any screen draws. Almost all of it is ringing a bell the screens that
 * care are already listening to, which is what keeps a scan of fifty thousand files from making a
 * wall of people re-read itself once a second for an hour.
 */
const HANDLED: Record<LiveState['about'][number], (state: LiveState) => void> = {
	library: () => libraryChanges.changed(),
	arrivals: () => arrivals.changed(),
	jobs: () => jobChanges.changed(),
	downloads: () => downloadChanges.changed(),
	settings: () => settingChanges.changed(),
	mine: () => mine.changed(),
	screens: () => screenChanges.changed(),
	same_music: () => sameMusicChanges.changed(),

	/* The one subject that arrives as an answer rather than as a question.
	 *
	 * What this account thinks of particular files, applied by the screens already drawing those
	 * rows without asking the server for anything. Past what a message will carry the server says so
	 * instead of sending some of them, and the honest response is to re-read: a page is one read
	 * whatever it holds, and applying half of a bulk rating would leave the rest silently wrong. */
	opinions: (state) => {
		for (const opinion of state.opinions ?? []) assetState.changed(opinion);
		if (state.more_opinions) arrivals.changed();
	},

	/* The second subject that arrives as an answer: a command from this user's phone for one of
	 * their screens. Handed to this tab's offer, which acts only on a command naming its own
	 * screen. Never asked for again and never repaired: a command missed while this tab was away
	 * is a command that should not happen late. */
	remote: (state) => screenOffer.receive(state.commands ?? [])
};

export class LiveFeed {
	/** True while a connection is open. Nothing draws it today; it is what a test asserts on. */
	live = $state(false);

	/** Where this browser stands, for the one line the header draws. See `Standing`. */
	standing = $state<Standing>('opening');

	#socket: WebSocket | null = null;
	#reconnect: ReturnType<typeof setTimeout> | null = null;
	#blind: ReturnType<typeof setInterval> | null = null;
	#running = false;

	/*
	 * Where this account's view stood when it was last spoken to, or null for a browser that has
	 * not been told yet.
	 *
	 * Compared for equality and never read. It is what closes the gap between a page loading and
	 * this connection opening, and the gap left by any absence after that. Without it every
	 * reconnect would either re-read the whole screen or miss whatever happened while it was away,
	 * and a connection that flaps on bad wifi would do the first of those every few seconds.
	 */
	#marker: string | null = null;

	start(): void {
		if (this.#running) return;
		this.#running = true;
		/* One timer for the whole session rather than one started and stopped around each gap, and
		   it does nothing at all while a connection is open. Started and stopped per gap it would be
		   cleared and remade by `#teardown`, which every reconnect calls, so a connection flapping
		   every two seconds would reset the thirty-second wait on each attempt and the fallback
		   would never once fire, which is exactly the case it exists for. */
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

	/**
	 * Ask every screen to re-read, because nothing is going to tell it.
	 *
	 * Every bell, not a chosen few: what is missed while there is no connection is unknown by
	 * definition, so picking which bells to ring would be guessing what the server would have said.
	 * `opinions` arrives as rows rather than as a question, and re-reading is what this file already
	 * does when more were written than a message carries. `remote` has no re-read at all: a command
	 * missed is one that must not happen late.
	 */
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
		// Asked before connecting, so that a change landing between the page loading and the
		// connection opening is one this can still find out about.
		try {
			this.#marker = (await api.get<LiveState>('/live')).marker;
		} catch (error) {
			/* ALREADY AT THE CAP, so do not open a socket at all.
			   Every handshake this makes would be refused before it became one, and the refusal
			   cannot say why, so opening one and asking afterwards is a round trip to learn what
			   has just been said here. Wait for a tab somewhere else to close instead. */
			if (isFull(error)) {
				this.standing = 'full';
				this.#later(FULL_AGAIN_MS);
				return;
			}
			// Unreachable, or not allowed. Either way the connection below settles it, and with no
			// mark to compare against, a first message says to re-read, which is the safe direction.
			this.#marker = null;
		}
		this.#connect();
	}

	#connect(): void {
		this.#teardown();
		if (!this.#running) return;

		const socket = new WebSocket(streamUrl(this.#marker));
		this.#socket = socket;

		// Whether this ever became a connection. It is the whole basis of telling a refusal from a
		// dropped connection below. See `#closed`.
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

	/**
	 * What the server just said, applied.
	 *
	 * The whole of what arrives: where this account now stands, and whether to re-ask. Public so it
	 * can be checked without a socket: what is worth pinning down is this decision, not the
	 * transport, which is the same shape as the job feed's and is proved there.
	 */
	apply(state: LiveState): void {
		this.#marker = state.marker;

		/* Read as "if it is there", and that is not defensiveness for its own sake: this application
		 * runs as a shell on one computer against a server on another, and the two are allowed to be
		 * different versions. A subject this version does not know about would otherwise be looked
		 * up and called, here, inside the handler, taking the whole live connection down with it,
		 * silently, because there is nothing on screen to say so and the reconnect would do it
		 * again. An inherited name (`__proto__`) is no subject either. */
		for (const about of state.about ?? []) {
			if (Object.hasOwn(HANDLED, about)) HANDLED[about](state);
		}
	}

	/** Where this account stood when it was last spoken to. For a test, and for nothing else. */
	get marker(): string | null {
		return this.#marker;
	}

	#closed(code: number, opened: boolean): void {
		const step = nextStep(code, opened);
		if (step === 'give-up') {
			/* Told plainly that this account may not. Nothing is coming back, so the screen says so
			   and the slow re-read above is the only thing keeping it current from here. Silence
			   would be a wall that never moves again and never says why. */
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
	 * Am I allowed at all, or was that a dropped connection?
	 *
	 * **It asks one question and keeps no part of the answer**, and the second half is the load
	 * bearing one. This read comes back with where the account stands now, and taking that as the
	 * new mark would be adopting a position this screen has not been brought up to. The next
	 * handshake would then say "nothing has moved since", perfectly truthfully, about a mark that
	 * was never acted on, and the screen would stay as it was for good.
	 *
	 * So the mark is left exactly where the last message put it. Coming back with a stale one is
	 * the point: the server compares, sees it has moved, and says so.
	 */
	async #askWhether(): Promise<void> {
		try {
			await api.get<LiveState>('/live');
		} catch (error) {
			if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
				this.standing = 'stopped';
				return;
			}
			/* Allowed, but this account is holding as many connections as it may. It is the one
			   refusal that is worth coming back from and is not worth coming back from SOON: it
			   clears when a tab somewhere else is closed. */
			if (isFull(error)) {
				this.standing = 'full';
				this.#later(FULL_AGAIN_MS);
				return;
			}
			// Unreachable rather than unwilling. Coming back is the point.
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
			// Unhooked before closing: `close()` fires `onclose`, and that handler's job is to decide
			// whether to reconnect. Left attached it would dial back in as this is being taken down.
			this.#socket.onclose = null;
			this.#socket.onmessage = null;
			this.#socket.close();
			this.#socket = null;
		}
	}
}

export const live = new LiveFeed();
