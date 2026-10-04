import { api } from '$lib/api/client';
import { session } from '$lib/shell/session.svelte';

/* Giving the password back to a session Sift restarted under, and whether to ask for it.
 *
 * Saved stash-box keys, the AcoustID key, Site cookies and tunnels are sealed with a key that lives
 * only while Sift runs. A restart (an update, the computer started again) keeps the session signed in
 * and seals every one of them, so work that needs a key parks and waits, and has to be told why
 * and how to go on rather than only that it is blocked.
 *
 * ONE ACT, SEVERAL DOORS. The bar across the top of every screen, `Settings > Connections`, and a
 * parked row on Activity each offer the same field (`UnlockField`), and every one of them sends the
 * password through `unlock` below: one request, one re-read of the session, one answer to show.
 */

/** What the field says when the password is refused. */
export const REFUSED = "That password didn't work. Try again.";

/* Where a Not now is remembered in this browser, so a reload does not ask again. */
const KEPT = 'sift.unlock.not-now';

/* A Not now: who pressed it, under which run of the server (`/auth/me`'s `boot`), and how many
   tasks were parked for the password then. Held to the run because a restart seals the keys
   again, and that is a new question; held to the user because a browser can be signed in as
   another admin later. */
interface Aside {
	user: string;
	boot: string;
	at: number;
}

/* This browser's remembered Not now, or null. The page works without it: a private window, a
   blocked store or an unreadable value is simply nothing remembered, and the bar asks. */
function keptAside(): Aside | null {
	try {
		const raw = localStorage.getItem(KEPT);
		if (!raw) return null;
		const kept: unknown = JSON.parse(raw);
		if (
			typeof kept === 'object' &&
			kept !== null &&
			typeof (kept as Aside).user === 'string' &&
			typeof (kept as Aside).boot === 'string' &&
			typeof (kept as Aside).at === 'number'
		) {
			return kept as Aside;
		}
	} catch {
		// Nothing remembered.
	}
	return null;
}

function keepAside(aside: Aside | null): void {
	try {
		if (aside === null) localStorage.removeItem(KEPT);
		else localStorage.setItem(KEPT, JSON.stringify(aside));
	} catch {
		// Not remembered past this page; the bar asks again after a reload.
	}
}

class Unlock {
	/* The Not now pressed in this page, or null while the bar has not been put aside here. The bar
	   comes back when MORE tasks are parked than its `at`: work stopping for the key is the moment
	   the answer to "Not now" has changed. */
	#aside = $state<Aside | null>(null);

	/* How many tasks were parked when this session put the bar aside under this run of the server,
	   or null: the one pressed in this page, else the one this browser remembers. A Not now from
	   before a restart, or by somebody else, is not this one. */
	#mark(): number | null {
		const viewer = session.viewer;
		if (!viewer?.boot) return null;
		for (const aside of [this.#aside, keptAside()]) {
			if (aside && aside.user === viewer.id && aside.boot === viewer.boot) return aside.at;
		}
		return null;
	}

	#setMark(at: number | null): void {
		const viewer = session.viewer;
		const aside = at !== null && viewer?.boot ? { user: viewer.id, boot: viewer.boot, at } : null;
		this.#aside = aside;
		keepAside(aside);
	}

	/**
	 * Whether the bar is up: an admin whose saved keys are locked, who has not put it aside, or
	 * whose work has stopped for the key since they did. `waiting` is how many tasks are parked for
	 * the password now (the queue's `password_wanted`).
	 */
	shown(waiting: number): boolean {
		if (!session.isAdmin || !session.secretsLocked) return false;
		const mark = this.#mark();
		return mark === null || waiting > mark;
	}

	/** Put the bar aside until work stops for the key, across reloads, until the next restart. */
	notNow(waiting: number): void {
		this.#setMark(waiting);
	}

	/**
	 * Follow the queue while the bar is aside: work that was parked and has gone (cancelled, say)
	 * lowers the mark, so the next task parked for the key brings the bar back. And once the keys
	 * are unlocked the aside is forgotten, so the next restart asks again.
	 *
	 * `waiting` is null until the queue has been read: a page just loaded has counted nothing yet,
	 * and taking that for "the parked work has gone" would lower a remembered mark to nothing and
	 * bring the bar back the moment the count arrived. Nothing is forgotten before the session is
	 * known, for the same reason.
	 */
	follow(waiting: number | null): void {
		const viewer = session.viewer;
		if (viewer && !session.secretsLocked) {
			// Only this user's: another account signed in here later has no Not now to forget.
			if (this.#aside !== null || keptAside()?.user === viewer.id) this.#setMark(null);
			return;
		}
		const mark = this.#mark();
		if (waiting !== null && mark !== null && waiting < mark) this.#setMark(waiting);
	}

	/**
	 * Send the password. True when the keys are unlocked; false when the password was refused.
	 *
	 * The session is read again rather than assumed: whether the keys are locked is the server's
	 * fact, and it is the one every door reads to decide whether to ask.
	 */
	async unlock(password: string): Promise<boolean> {
		try {
			await api.post('/auth/unlock-secrets', { body: { password } });
		} catch {
			return false;
		}
		await session.recheck();
		return true;
	}
}

export const unlock = new Unlock();
