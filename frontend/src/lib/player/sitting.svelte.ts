/*
 * How long somebody sat with a file that has no end of its own, reported in two pieces.
 *
 * A video ends, and the player measures it against a timeline it already has. A PHOTOGRAPH has no
 * player, no timeline and no end, so nothing else in this application is measuring it. Without this
 * a picture opened a hundred times would be opened zero times as far as the library was concerned:
 * never in Recently viewed, and every still unwatched to the Unwatched filter.
 *
 * Two numbers and a clock is the whole mechanism. A still does not move, so the only two moments
 * that matter are when it arrived and when it was left.
 *
 * ## Why the report goes in TWO pieces
 *
 * A picture is viewed by BEING OPENED: that is the rule the server keeps, and it does not need to
 * wait for anything to find out. So an empty piece goes the moment it arrives, which is what earns
 * the view, and the time goes when it is left.
 *
 * Reporting it all at the end cannot work: the server asks how long a sitting lasted, and a report
 * of zero qualifies as nothing, so every photograph would be unviewable. Reporting it all at the
 * START, with no time on it, would count every picture in a run arrow-keyed through at the speed
 * the key repeats as seen.
 *
 * ## Why it is here rather than in the screen that uses it
 *
 * `AssetView` is over a thousand lines with thirty-five imports, and this is thirty of them. A test
 * that had to mount that component to reach this would be a mock surface larger than the thing
 * under test: a second thing to maintain and a second thing to get wrong. The screen calls it and
 * owns nothing about it.
 *
 * !! NOT `dwell.svelte.ts` beside it, which answers a DIFFERENT question: how long a RUN rests on
 * a still before moving on. Two meanings under one name is how the next reader gets the wrong one.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { FilledClock } from '$lib/player/inside';

type ViewReport = components['schemas']['ViewReport'];

/** Which screen a sitting happened on. The server's list, read off the generated types. */
type SittingScreen = NonNullable<ViewReport['screen']>;
/** The screen a panel was opened over. The server's list, read off the generated types. */
export type OpenedFrom = NonNullable<ViewReport['opened_from']>;

/**
 * WHERE a sitting happened, carried on every piece of its report.
 *
 * Gathered because none of it can be worked out afterwards: which screen a file was on, what it
 * was opened from, the saved Loop or kept filter behind it, and the Theater session it was part
 * of. Every piece repeats it and the server keeps the first, the way it keeps the first piece's
 * start. The file's KIND is not here on purpose: the server reads that off the file itself.
 *
 * The report's own fields, taken from the generated type rather than written again, so it is
 * spread into a body rather than translated, and the screen is REQUIRED here where the wire lets
 * an old client omit it: every frame in this client knows which screen it is.
 */
export type SittingPlace = Pick<
	ViewReport,
	'opened_from' | 'opened_from_id' | 'searched' | 'loop' | 'kept_filter' | 'theater_session'
> & { screen: SittingScreen };

/**
 * What a sitting carried on in the corner says it was opened from: what the panel's sitting it
 * came out of was opened from. The same file, carried on, was opened from the same thing; only the
 * screen is the corner's. Nothing but the screen where there was no panel behind it.
 */
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
 * A name for one sitting, minted where the sitting begins.
 *
 * The server cannot work this out for itself. A sitting arrives in pieces (a picture reports an
 * empty one when it is opened and the time when it is left, a video reports once when it has earned
 * its view and again on the way out), and with nothing in a piece saying which sitting it belonged
 * to, every one of them would become a row of its own. Repeated on every piece, this is what makes them one.
 *
 * `crypto.getRandomValues` and NOT `crypto.randomUUID`, which would be the obvious call and is the
 * wrong one here: Sift is served over plain http on a home network by design, which is not a secure
 * context, and `randomUUID` does not merely fail there: it is not defined at all. `getRandomValues` has no such restriction.
 *
 * Sixteen bytes of randomness, written as hex. It is compared with other ids from the same account
 * and never shown, so the only property it needs is not colliding with the one before it.
 */
export function newSittingId(): string {
	const bytes = new Uint8Array(16);
	crypto.getRandomValues(bytes);
	return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/**
 * A sitting on its way from one screen to another, carrying what its closing report needs.
 *
 * A picture handed from the panel to the corner is the same sitting: its view was earned when it
 * was opened, so the corner carries the clock and the name on rather than starting a second
 * sitting, which would count a second view on every hand-over.
 */
export interface SittingBaton {
	asset: string;
	id: string;
	from: number;
	sent: number | null;
	place: SittingPlace;
	/** Whether the picture was magnified before it was handed over. */
	magnified: boolean;
	/** How long it had filled the screen before it was handed over, not yet reported. */
	filled: number;
}

/* The sittings in progress, so a hand-over can find the one on the file being handed. */
const live = new Set<Sitting>();

/**
 * Say that a picture is being shown magnified, for every sitting in progress on it.
 *
 * Called by the screen that does the magnifying (`StillView`), which does not own the sitting: the
 * frame around it does. A fact about the sitting, sent on its closing piece.
 */
export function noteMagnified(assetId: string): void {
	for (const one of live) if (one.on === assetId) one.magnify();
}

/**
 * Take the sitting a screen has open on this file, so another screen can carry it on.
 *
 * The screen it is taken from reports nothing for it afterwards; whoever resumes it sends the
 * closing piece. Null when that screen has no sitting on the file.
 */
export function handOffSitting(assetId: string, screen: SittingScreen): SittingBaton | null {
	for (const one of live) {
		if (one.on === assetId && one.screen === screen) return one.handOff();
	}
	return null;
}

/** One screen's record of the file it is sitting with. */
export class Sitting {
	#on: string | null = null;
	#from = 0;
	/** What both pieces of this sitting carry, so the two become one row. See `newSittingId`. */
	#id = '';
	/**
	 * How much of this sitting has already been reported, or null where none of it has.
	 *
	 * What the closing report carries as `already_reported_ms`, and the reason a picture's tally
	 * moves the moment it is opened rather than when it is left.
	 *
	 * **Zero here is not nothing.** It says a piece of this sitting has already gone, which is what
	 * stops the second one being counted as a second view. See `ViewReport.already_reported_ms`.
	 */
	#sent: number | null = null;
	/** Where this sitting is happening, fixed when it begins. See `SittingPlace`. */
	#place: SittingPlace | null = null;
	/** Whether the picture was magnified during this sitting. See `noteMagnified`. */
	#magnified = false;
	/** How long this sitting has filled the screen. */
	readonly #filled = new FilledClock();

	/** Which file this is sitting with, or null between two. Read by tests and by nothing else. */
	get on(): string | null {
		return this.#on;
	}

	/** Which screen this sitting is happening on, or null between two. */
	get screen(): SittingScreen | null {
		return this.#place?.screen ?? null;
	}

	/**
	 * Begin a sitting, and earn the view.
	 *
	 * Whatever was on screen before this is finished off first. Stepping from one picture to the
	 * next fires no unmount (the frame lives across the change), so without this the previous
	 * file's time would simply be dropped.
	 */
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
			/* Put back if it did not land, so the report on the way out carries the whole sitting and
			   the view is counted then instead. Only while this is still the file on screen: once it
			   has been left there is nothing to correct, and one view lost to a request that failed
			   is the right side to be wrong on: the other side counts it twice. */
			.catch(() => {
				if (this.#on === assetId) this.#sent = null;
			});
	}

	/**
	 * Report the sitting that just ended, if there was one.
	 *
	 * `keepalive`, because the commonest way to leave a picture is to close the tab or navigate
	 * away, and an ordinary request is cancelled when the page goes. Fire and forget past that: a
	 * view that was not counted is not worth a message, and nothing the person did failed.
	 */
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
				// No position and no end: a still is looked at rather than played, so the only honest
				// facts about the sitting are which file it was and how long it lasted. Zero rather
				// than null for the position, which is what clears a resume point left by something
				// else. See `record_view`. The same sitting id as the piece sent on the way in, which
				// is what makes a picture ONE row instead of two, one of them always zero-length.
				body: {
					...place,
					watch_ms: spent,
					already_reported_ms: before,
					position_ms: 0,
					ended: false,
					sitting,
					// What happened inside it: how long it filled the screen, and whether it was
					// magnified. Both facts, measured here, and false is a fact too.
					fullscreen_ms: filled,
					magnified
				},
				keepalive: true
			})
			.catch(() => {});
	}

	/**
	 * Let this sitting go WITHOUT reporting it, for another screen to carry on. See `SittingBaton`.
	 */
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

	/**
	 * Carry on a sitting another screen handed over. Nothing is sent now: the view was earned when
	 * the sitting began, and the closing piece goes when this screen leaves the file.
	 */
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

	/** Mark this sitting's picture as magnified. See `noteMagnified`. */
	magnify(): void {
		if (this.#on !== null) this.#magnified = true;
	}
}

/**
 * One record per screen, never a shared one.
 *
 * Two of these views can be mounted at the same time (the panel in the corner and a full-size view
 * behind it), and a single shared record would have the second one end the first one's sitting on
 * the way in.
 */
export function newSitting(): Sitting {
	return new Sitting();
}
