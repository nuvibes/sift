// SPDX-License-Identifier: AGPL-3.0-or-later
/* Music lookup: naming a file's song by asking AcoustID, drawn on the Music section.
 *
 * It is the same kind of thing as a stash-box: a place Sift sends something about the library to
 * somebody else's service, with a key of yours, through a route you choose. So it is drawn the same
 * way (the switch with its disclosure, the key that is written and never read back, the route
 * picker a box has, and a test of the key), and nowhere else.
 *
 * ## Three kinds of thing, three ways in
 *
 * The SWITCH (`music.lookup`) and the ROUTE (`music.lookup_route`) are ordinary registered settings,
 * written through the one settings write like every other, so a change reaches every screen the way
 * any setting's does. Their words (the label, the help and the disclosure) are the server's
 * declaration, read here rather than written out again, so the sentence that says what leaves this
 * device is the one the server declared and cannot drift from it.
 *
 * The KEY is not a setting. It is sealed on the server and its own routes take it in and take it
 * away; nothing answers with it. So this module holds whether one is set and whether it can be
 * opened now, and never the key: the card types it into a box and sends it, and the box goes.
 *
 * The TEST sends AcoustID's own documented example with the key, never anything of the library,
 * and says what came back in the server's words.
 *
 * ASKING ABOUT THE LIBRARY is not here. It is a task of its own, Name songs with AcoustID, with its
 * own When and its presses on Settings > Tasks; the Music pane draws that task's row, so its When
 * is chosen on either screen and it is pressed from one place. Nothing on this card sends a file's
 * fingerprint anywhere.
 *
 * ## Why the traffic goes through a wire object
 *
 * So the design gallery can draw the real card without it writing a real setting when somebody
 * presses the switch there: the gallery hands in a wire that keeps its answers in memory. The
 * screen uses `LIVE`, the default.
 */

import { api, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import {
	fetchSettings,
	refusalOf,
	saveSettings,
	type SettingEntry
} from '$lib/settings-ui/settings';
import { UNREACHABLE } from '$lib/shell/unreachable';

type LookupState = components['schemas']['LookupState'];
type LookupChecked = components['schemas']['LookupChecked'];
type LookupPressed = components['schemas']['LookupPressed'];

/** The switch, by the key the server declares it under. Off unless somebody turns it on. */
export const LOOKUP_KEY = 'music.lookup';
/** Which way the lookup goes out: a tunnel's id, or nothing for this device's own connection. */
export const LOOKUP_ROUTE_KEY = 'music.lookup_route';

/**
 * The registered settings this card draws, so a pane that draws every setting it is sent can
 * leave them out. They are filed in the registry beside the stash-boxes' own, and the Stash-boxes
 * pane draws every setting of that section that is not a field rule, so without this they would
 * appear there too.
 */
const DRAWN_HERE: ReadonlySet<string> = new Set([LOOKUP_KEY, LOOKUP_ROUTE_KEY]);

/** The words the card writes itself. The switch and the route carry the server's own. */
export const COPY = {
	heading: 'Song names',
	/* Where the switch stands, in the Note under it. */
	status: {
		off: 'Off. Nothing is sent to AcoustID.',
		noKey: 'On, but nothing is sent until you add your AcoustID key below.',
		locked: 'On, but the key is locked until you unlock it at the top of this page.',
		on: 'On. Files are looked up on AcoustID when the task below runs, and the song on each is named.'
	},
	/* The page that holds what is set once, if ever: which way the lookup goes out. */
	more: {
		label: 'More settings',
		title: 'More settings',
		help: 'Which connection Sift uses to reach AcoustID.',
		open: 'Edit'
	},
	key: {
		label: 'AcoustID key',
		/* Said while there is no key: what to type, and what happens to it. */
		help: 'Your AcoustID application key. Sift encrypts it and never shows it again.',
		save: 'Save',
		/* Said once there is one. The key itself is never on this screen. */
		kept: 'Sift keeps it encrypted and never shows it.',
		saved: 'Key saved',
		locked: 'Key locked',
		/* The same sentence a stash-box's locked key says, because it is the same state for the
		   same reason: the key is sealed under a password the server forgets on restart. */
		lockedSays:
			'This key is locked because Sift restarted. Enter your password in Unlock at the top of this page to use it again. Nothing was lost.',
		/* The key stops existing on the server (its sealed copy is deleted), so Delete. */
		delete: 'Delete key'
	},
	test: {
		label: 'Test the key',
		help: "Looks up AcoustID's own example with your key. Nothing from your library is sent.",
		action: 'Test',
		/* The mark a stash-box's Test puts up when it answered, for the same answer. */
		worked: 'Connected'
	},
	loadFailed: "These settings couldn't be loaded. Reload the page to try again."
} as const;

/** Everything the card asks the server, and nothing else. See the header for why it is an object. */
export interface LookupWire {
	/** Whether it is on, whether a key is set and can be opened, and the route. */
	read(): Promise<LookupState>;
	/** The server's declarations of the switch and the route: their words, their defaults. */
	declared(): Promise<SettingEntry[]>;
	/** The ordinary settings write. */
	save(values: Record<string, unknown>): Promise<void>;
	setKey(key: string): Promise<LookupState>;
	deleteKey(): Promise<LookupState>;
	test(): Promise<LookupChecked>;
	/** Ask AcoustID again about the files it did not know, last asked long enough ago. */
	askAgain(): Promise<LookupPressed>;
}

/* WHY NOT FOLLOWED: a store built once per screen. The card that holds it re-reads it when a
   setting moves (`whenChanged(settingChanges, ...)` in `MusicLookup.svelte`).

   Exported for the gallery's wire, which answers from memory but reads the declarations from here,
   so the gallery draws the server's own sentence rather than a copy of it. */
export const LIVE: LookupWire = {
	read: () => api.get<LookupState>('/music/lookup'),
	async declared() {
		const sections = await fetchSettings();
		return sections
			.flatMap((section) => section.settings ?? [])
			.filter((entry) => DRAWN_HERE.has(entry.key));
	},
	save: (values) => saveSettings(values),
	setKey: (key) => api.put<LookupState>('/music/lookup/key', { body: { key } }),
	deleteKey: () => api.del<LookupState>('/music/lookup/key'),
	test: () => api.post<LookupChecked>('/music/lookup/check'),
	askAgain: () => api.post<LookupPressed>('/music/lookup/again')
};

function said(error: unknown): string {
	if (error instanceof ApiError) return error.detail ?? error.message;
	return UNREACHABLE;
}

export class MusicLookup {
	readonly #wire: LookupWire;
	/* Every read takes a number, and only the newest one's answer is kept. A write bumps it too, so
	   a read that set off before the write cannot land after it and put the old value back. */
	#reads = 0;

	/** What the server said. Null until it has answered: nothing is claimed before then. */
	state = $state<LookupState | null>(null);
	/** The switch's and the route's declarations, by key. */
	entries = $state<Record<string, SettingEntry>>({});
	failed = $state(false);
	/** The last write that was refused, in the server's words. */
	problem = $state<string | null>(null);
	/** What the last test of the key came back with. Cleared whenever the key changes. */
	tested = $state<LookupChecked | null>(null);
	testing = $state(false);
	busy = $state(false);
	/** Whether Ask again is on its way to the server. */
	asking = $state(false);

	constructor(wire: LookupWire = LIVE) {
		this.#wire = wire;
	}

	async load(): Promise<void> {
		const mine = ++this.#reads;
		try {
			const [state, declared] = await Promise.all([this.#wire.read(), this.#wire.declared()]);
			if (mine !== this.#reads) return;
			this.state = state;
			this.entries = Object.fromEntries(declared.map((entry) => [entry.key, entry]));
			this.failed = false;
		} catch {
			if (mine !== this.#reads) return;
			this.failed = true;
		}
	}

	/** Turn the lookup on or off. Shown immediately, and put back if the server refuses. */
	async turn(on: boolean): Promise<void> {
		await this.#setting('on', LOOKUP_KEY, on);
	}

	/** Send the lookup through a tunnel, or `null` for this device's own connection. */
	async route(route: string | null): Promise<void> {
		await this.#setting('route', LOOKUP_ROUTE_KEY, route);
	}

	/** Seal a key. True when it was taken; the refusal is in `problem` otherwise. */
	async setKey(key: string): Promise<boolean> {
		return this.#key(() => this.#wire.setKey(key));
	}

	async deleteKey(): Promise<boolean> {
		return this.#key(() => this.#wire.deleteKey());
	}

	/** Test the key against AcoustID's own example. What it said lands in `tested`. */
	async test(): Promise<void> {
		this.testing = true;
		try {
			this.tested = await this.#wire.test();
		} catch (error) {
			this.tested = { ok: false, said: said(error) };
		} finally {
			this.testing = false;
		}
	}

	/**
	 * Ask AcoustID again about the files it did not know. Answers the server's sentence (how many
	 * it is asking about, or that none is waiting), or the refusal in its words while the lookup is
	 * off or has no key. The count beside the press is read again after, from the same answer the
	 * pane draws, so it says what is left.
	 */
	async askAgain(): Promise<{ said: string; ok: boolean }> {
		this.asking = true;
		try {
			const pressed = await this.#wire.askAgain();
			return { said: pressed.said, ok: true };
		} catch (error) {
			return { said: said(error), ok: false };
		} finally {
			this.asking = false;
			void this.load();
		}
	}

	async #setting<K extends 'on' | 'route'>(
		field: K,
		key: string,
		value: LookupState[K]
	): Promise<void> {
		const state = this.state;
		if (!state) return;
		this.#reads += 1;
		const before = state[field];
		this.state = { ...state, [field]: value };
		this.problem = null;
		try {
			await this.#wire.save({ [key]: value });
		} catch (error) {
			// Put it back. A control left where it was pushed after the write failed is a screen
			// saying a thing is true when it is not.
			if (this.state) this.state = { ...this.state, [field]: before };
			this.problem = refusalOf(error);
		}
	}

	async #key(write: () => Promise<LookupState>): Promise<boolean> {
		this.busy = true;
		this.problem = null;
		this.#reads += 1;
		try {
			this.state = await write();
			this.tested = null;
			return true;
		} catch (error) {
			this.problem = said(error);
			return false;
		} finally {
			this.busy = false;
		}
	}
}
