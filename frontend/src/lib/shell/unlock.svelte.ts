import { api } from '$lib/api/client';
import { session } from '$lib/shell/session.svelte';

/*
 * Giving the password back after a restart sealed the saved keys and cookies. ONE ACT, SEVERAL
 * DOORS: the bar, Settings > Connections and a parked row all send it through `unlock`.
 */

export const REFUSED = "That password didn't work. Try again.";

const KEPT = 'sift.unlock.not-now';

/* A Not now: who, under which run of the server (`boot`), and how many tasks were parked then. */
interface Aside {
	user: string;
	boot: string;
	at: number;
}

/* Or null: a private window or a blocked store remembers nothing. */
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
		// Not remembered past this page.
	}
}

class Unlock {
	/* The bar comes back when MORE tasks are parked than its `at`. */
	#aside = $state<Aside | null>(null);

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

	/** `waiting`: tasks parked for the password now (`password_wanted`). */
	shown(waiting: number): boolean {
		if (!session.isAdmin || !session.secretsLocked) return false;
		const mark = this.#mark();
		return mark === null || waiting > mark;
	}

	notNow(waiting: number): void {
		this.#setMark(waiting);
	}

	/**
	 * Lowers the mark as parked work goes; forgets the aside once unlocked. Null waits for the
	 * count.
	 */
	follow(waiting: number | null): void {
		const viewer = session.viewer;
		if (viewer && !session.secretsLocked) {
			if (this.#aside !== null || keptAside()?.user === viewer.id) this.#setMark(null);
			return;
		}
		const mark = this.#mark();
		if (waiting !== null && mark !== null && waiting < mark) this.#setMark(waiting);
	}

	/** True when unlocked; the session is read again, since locked is the server's fact. */
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
