/*
 * This tab offering its open player or Theater wall to the signed-in user's phone.
 *
 * ## What an offer is
 *
 * A player or a wall that is open says so: it hands this module its table of actions (the same
 * table its keys are answered from, in `$lib/shell/shortcuts`) and a way to read where it stands. From
 * then until it closes, this tab tells the server what it is doing whenever that changes, and at
 * least every `REPORT_EVERY_MS` while nothing does, and a command from the phone arrives down the
 * one live connection the tab already holds and is answered from the same table a key would be.
 * There is no pairing: the phone is signed in as the same user, and that is the whole of it.
 *
 * ## Who offers
 *
 * **Sift's own desktop application offers by default; a browser tab only when it is switched on.**
 * A window of the app is the screen at the desk, which is what a remote is for. A browser tab
 * might be anybody's laptop on the sofa, or the phone itself, and a phone listing itself as a
 * screen to control is noise, so a tab waits to be asked.
 *
 * ## What it costs
 *
 * One local read of the player a second, and a request only when something changed (playing,
 * the file, the volume, a seek) or the report is due. A tab offering nothing does nothing.
 *
 * ## A browser that holds something back
 *
 * A browser tab with its switch off and a player or a wall open says ONE thing to the server, at
 * the report's pace: that it exists (`/remote/quiet`), and nothing about what it plays. The phone
 * reads the count and says where the switch is, so a laptop's tab missing from the Remote's list
 * explains itself rather than reading as the Remote being broken. A tab with nothing open says
 * nothing: it has nothing to offer either way.
 */

import type { components } from '$lib/api/schema';
import { api } from '$lib/api/client';
import { bridge } from '$lib/bridge';
import { screenChanges } from '$lib/library/changes.svelte';
import { readStored, writeStored } from '$lib/shell/remembered.svelte';
import {
	commanded,
	offerable,
	type Actions,
	type PlayerAction,
	type RemoteAction,
	type TheaterAction
} from '$lib/shell/shortcuts';

import type { RemoteCommand, RemoteScreens, ScreenReport } from './wire';

/**
 * How often an offered screen says where it stands when nothing has changed.
 *
 * The server's `REPORT_EVERY_SECONDS`, which a server test holds this to: the server lets a screen
 * go after three of these are missed, so a copy that drifted longer would have screens flicker in
 * and out of the phone's list.
 */
export const REPORT_EVERY_MS = 10_000;

/** How often this tab reads its own player. A read of local state, not a request. */
const LOOK_EVERY_MS = 1_000;

/** How far a position may move beyond where playing carried it before it counts as a seek. The
 *  server's `DRIFT_SECONDS`. */
const DRIFT_SECONDS = 2;

/** Where this browser remembers that it was switched on. A fact about this browser, not the user. */
const THIS_BROWSER_KEY = 'sift.remote.this_browser';

/** Where a player or wall stands, as its surface reads it: the server's own shape, less what the
 *  server adds on the way out (the screen's id, whether it is hidden, how long ago it was heard). */
export type ScreenState = Pick<
	components['schemas']['ScreenOut'],
	'playing' | 'position' | 'length' | 'file' | 'volume' | 'muted'
> &
	Partial<Pick<components['schemas']['ScreenOut'], 'cells' | 'focused'>> &
	Partial<Extras>;

/**
 * What a screen's drawer stands at and the lists it chooses from, in the report's own fields: the
 * repeat, shuffle and A-B loop the desk lights, the sizes, the favourite and O counter, a wall's
 * timer, layouts, presets and what each cell shows. A surface says what it has and leaves out
 * what it does not; the phone dims a control the screen did not offer.
 */
type Extras = Pick<
	ScreenReport,
	| 'repeat'
	| 'shuffle'
	| 'loop_marks'
	| 'qualities'
	| 'quality'
	| 'favorite'
	| 'count'
	| 'timer'
	| 'every_cell'
	| 'cell_held'
	| 'cell_muted'
	| 'layouts'
	| 'layout'
	| 'presets'
	| 'cell_files'
>;

/** The longest list a report may carry, and the longest name in it: the server's `MOST_CHOICES`
 *  and its `Choice`. A preset list longer than this is cut rather than refused whole. */
const MOST_CHOICES = 32;
const LONGEST_CHOICE = 80;

/** A list a screen offers, cut to what the server takes. An empty name is dropped: the server
 *  refuses one, and it could be nobody's press. */
function choices(list: readonly string[] | undefined): string[] {
	return (list ?? [])
		.slice(0, MOST_CHOICES)
		.map((one) => one.slice(0, LONGEST_CHOICE))
		.filter((one) => one.length > 0);
}

/** A place in a list, kept only where it names something in the list as it will be sent. */
function place(at: number | null | undefined, among: readonly string[]): number | null {
	return at === null || at === undefined || at < 0 || at >= among.length ? null : at;
}

/** What an open player or wall hands over when it offers itself. */
export type Surface =
	| { kind: 'player'; actions: Actions<PlayerAction>; state: () => ScreenState }
	| { kind: 'theater'; actions: Actions<TheaterAction>; state: () => ScreenState };

/**
 * What a viewer's content hands up so the VIEWER can offer it: a video's player, a picture's still
 * view. Its table of actions and where it stands, and nothing about who offers.
 */
export interface Offerable {
	actions: Actions<PlayerAction>;
	state: () => ScreenState;
}

/**
 * Offer a viewer (the popout, the mini player) to the phone, whatever it is showing.
 *
 * THE OFFER IS THE VIEWER'S, not the video component's. Made from the video player's own mount, a
 * popout showing a photograph or a GIF, drawn by a different component, would offer nothing and
 * the phone would see no screen at all. The viewer is what is open; what it shows changes
 * under it. So the viewer offers once, for as long as it is open, and every read and every press
 * goes to whatever it is showing at that moment (`showing`), with the viewer's own presses
 * (`own`: a favourite, the O counter) laid over it.
 *
 * Returns the withdrawal, for the viewer's teardown.
 */
export function offerViewer(
	showing: () => Offerable | null,
	own: () => Actions<PlayerAction> = () => ({}),
	ownState: () => Partial<ScreenState> = () => ({})
): () => void {
	return screenOffer.offer({
		kind: 'player',
		get actions(): Actions<PlayerAction> {
			return { ...(showing()?.actions ?? {}), ...own() };
		},
		state: () => ({
			...(showing()?.state() ?? {
				playing: false,
				position: 0,
				length: null,
				file: null,
				volume: 100,
				muted: false
			}),
			...ownState()
		})
	});
}

/**
 * A screen's name: random, and made in a way a plain-http page can make it.
 *
 * `crypto.randomUUID` exists only on a secure connection, which most installs on a home network
 * are not, so the bytes come from `getRandomValues`, which is there on both.
 */
function mintScreen(): string {
	const bytes = new Uint8Array(16);
	crypto.getRandomValues(bytes);
	return 'screen-' + Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/**
 * Whether this page is inside Sift's own desktop application.
 *
 * The one question here that is about the window rather than about something it can do, which is
 * why it asks whether the application's bridge is there at all rather than asking for one of its
 * verbs: every verb it has is a capability, and "the app is controllable by default" is a rule
 * about the window.
 */
/** The bridge is the one place that knows whether the desktop shell is here. */
function inTheApp(): boolean {
	return bridge.isDesktop();
}

/**
 * What the phone calls this screen: the application or the browser, and where it is.
 *
 * The application says which COMPUTER when the shell can tell it (`machine`): two desks running
 * the app both read "The Sift app on Windows" otherwise, told apart only by what each is playing.
 * A browser has no shell to ask, so a tab names the system under it.
 */
export function labelFor(app: boolean, agent: string, machine: string | null = null): string {
	/* An iPhone's own words say it is "like Mac OS X", so it is asked about before a Mac is. */
	const system = /Windows/.test(agent)
		? 'Windows'
		: /iPhone|iPad/.test(agent)
			? 'an iPhone or iPad'
			: /Macintosh|Mac OS X/.test(agent)
				? 'a Mac'
				: /Android/.test(agent)
					? 'Android'
					: /Linux/.test(agent)
						? 'Linux'
						: null;
	const what = app
		? 'The Sift app'
		: /Edg\//.test(agent)
			? 'Edge'
			: /Firefox\//.test(agent)
				? 'Firefox'
				: /Chrome\//.test(agent)
					? 'Chrome'
					: /Safari\//.test(agent)
						? 'Safari'
						: 'A browser';
	const where = app && machine ? machine : system;
	return where === null ? what : `${what} on ${where}`;
}

/**
 * What the mark on a controlled screen says: which phones are driving it, in their own names.
 *
 * "Remote controlled", the words the Remote's own glyph stands for, then the phones as the list
 * names them ("Safari on an iPhone or iPad"), joined as a sentence joins them.
 */
export function controlledWords(by: readonly string[]): string {
	if (by.length === 0) return '';
	const names = by.length === 1 ? by[0] : `${by.slice(0, -1).join(', ')} and ${by.at(-1)}`;
	return `Remote controlled from ${names}`;
}

/** Keeps a number the server will take: finite, never below zero. */
function seconds(value: number | null): number | null {
	return value === null || !Number.isFinite(value) || value < 0 ? null : value;
}

export class ScreenOffer {
	/** This tab's screen, for as long as the tab is open. A reload is a new screen. */
	readonly screen = mintScreen();

	/** Whether this browser tab offers itself. The desktop application does regardless. */
	thisBrowser = $state(readStored(THIS_BROWSER_KEY) === 'on');

	/**
	 * What the phones driving this screen call themselves, the most recently heard first; empty
	 * while none is, and always empty while nothing is offered.
	 *
	 * Read back from the phone's own list (`controlled_by` on this screen's entry) rather than
	 * worked out from the commands arriving: a phone sitting on this screen's card is driving it
	 * whether or not a thumb is on a button, and a command says nothing about which phone sent it.
	 * Re-read on every bell about the screens (a phone picking this screen or letting go rings
	 * one) and with every due report while a phone is named, so one that went quiet without
	 * letting go drops off when the server lets it go.
	 */
	controlledBy = $state<string[]>([]);
	/** Which kind of surface `controlledBy` is about: the one offered when it was read. */
	controlledSurface = $state<Surface['kind'] | null>(null);

	/** The surfaces open, the newest last. Only the newest is offered: it is the one on top. */
	#surfaces: Surface[] = [];
	#timer: ReturnType<typeof setInterval> | null = null;
	/** While this tab holds a surface back, the clock that says so again. See the file's head. */
	#quietTimer: ReturnType<typeof setInterval> | null = null;
	/** Whether the server was told this tab holds something back, so it is told when it stops. */
	#saidQuiet = false;
	/** What was last said, with the moment on this page's own clock. Null when nothing is offered. */
	#said: { report: string; position: number; playing: boolean; at: number } | null = null;
	/** The last command this tab acted on, said back so the phone knows it landed. */
	#actedOn: string | null = null;
	/** What the computer is called, once the shell has said; null until then and in a browser. */
	#machine: string | null = null;
	#askedMachine = false;
	readonly #app: () => boolean;
	readonly #now: () => number;
	readonly #agent: () => string;
	readonly #machineName: () => Promise<string | null>;

	constructor(
		app: () => boolean = inTheApp,
		now: () => number = () => performance.now(),
		agent: () => string = () => navigator.userAgent,
		machineName: () => Promise<string | null> = () => bridge.machineName()
	) {
		this.#app = app;
		this.#now = now;
		this.#agent = agent;
		this.#machineName = machineName;
		// A store that lives as long as the tab, so the plain subscription rather than the rune.
		screenChanges.subscribe(() => {
			if (this.#current() !== null) void this.#readControllers();
		});
	}

	/** Read which phones are driving this screen, from the list the phone itself reads. */
	async #readControllers(): Promise<void> {
		let answer: RemoteScreens | undefined;
		try {
			answer = await api.get<RemoteScreens>('/remote/screens');
		} catch {
			/* Nothing to do: the mark keeps what it last knew until the next bell or report. */
			return;
		}
		const mine = answer?.screens.find((one) => one.screen === this.screen);
		const surface = this.#current();
		this.controlledBy = surface === null ? [] : (mine?.controlled_by ?? []);
		this.controlledSurface = surface?.kind ?? null;
	}

	/** Whether a phone is driving this tab's Theater wall right now: what the wall's bar marks. */
	get wallControlled(): boolean {
		return this.controlledSurface === 'theater' && this.controlledBy.length > 0;
	}

	/** Whether this tab offers what it has open. */
	get offered(): boolean {
		return this.#app() || this.thisBrowser;
	}

	/** Whether this tab has a player or a wall open that it does not offer: its switch is off. */
	get holdingBack(): boolean {
		return !this.offered && this.#surfaces.length > 0;
	}

	/** Switch this browser tab's offer on or off. Remembered in this browser. */
	setThisBrowser(on: boolean): void {
		this.thisBrowser = on;
		writeStored(THIS_BROWSER_KEY, on ? 'on' : 'off');
		this.#settle();
	}

	/** Offer an open player or wall. Returns what takes the offer back, for when it closes. */
	offer(surface: Surface): () => void {
		this.#surfaces.push(surface);
		this.#settle();
		return () => {
			const at = this.#surfaces.lastIndexOf(surface);
			if (at >= 0) this.#surfaces.splice(at, 1);
			this.#settle();
		};
	}

	/**
	 * Commands that arrived down the live connection. Every tab of this user receives every one;
	 * only this tab's screen's are acted on, and only by the surface on top.
	 */
	receive(commands: readonly RemoteCommand[]): void {
		const surface = this.#current();
		if (surface === null) return;
		let acted = false;
		for (const command of commands) {
			if (command.screen !== this.screen) continue;
			if (!commanded(surface.actions, command.action, command.value)) continue;
			this.#actedOn = command.id;
			acted = true;
		}
		// Said immediately rather than on the next look: the phone is waiting for exactly this.
		if (acted) void this.look(true);
	}

	#current(): Surface | null {
		return this.offered ? (this.#surfaces.at(-1) ?? null) : null;
	}

	/** Start looking, or stop and take the screen away, to match what is open and switched on. */
	#settle(): void {
		this.#settleQuiet();
		if (this.#current() !== null) {
			this.#askMachine();
			this.#timer ??= setInterval(() => void this.look(false), LOOK_EVERY_MS);
			void this.look(true);
			return;
		}
		if (this.#timer !== null) {
			clearInterval(this.#timer);
			this.#timer = null;
		}
		this.controlledBy = [];
		this.controlledSurface = null;
		if (this.#said === null) return;
		this.#said = null;
		void api.del(`/remote/screens/${this.screen}`).catch(() => {
			// Nothing to do: a screen that is not withdrawn is let go of when it stops reporting.
		});
	}

	/** Say this tab holds something back while it does, and that it stopped once it does not. */
	#settleQuiet(): void {
		if (this.holdingBack) {
			if (this.#quietTimer !== null) return;
			this.#quietTimer = setInterval(() => void this.#sayQuiet(), REPORT_EVERY_MS);
			void this.#sayQuiet();
			return;
		}
		if (this.#quietTimer !== null) {
			clearInterval(this.#quietTimer);
			this.#quietTimer = null;
		}
		if (!this.#saidQuiet) return;
		this.#saidQuiet = false;
		void api.del(`/remote/quiet/${this.screen}`).catch(() => {
			// Nothing to do: the server stops counting a tab that stops saying so.
		});
	}

	async #sayQuiet(): Promise<void> {
		this.#saidQuiet = true;
		try {
			await api.put(`/remote/quiet/${this.screen}`);
		} catch {
			/* Nothing to do: said again on the next beat, and a tab that is never heard is simply
			   not counted. */
		}
	}

	/**
	 * Ask the shell once what this computer is called, the first time anything is offered, and say
	 * the new label as soon as it is known. An older shell, or none, answers null and the label
	 * keeps naming the system.
	 */
	#askMachine(): void {
		if (this.#askedMachine || !this.#app()) return;
		this.#askedMachine = true;
		void this.#machineName()
			.then((name) => {
				this.#machine = name;
				if (name !== null) void this.look(false);
			})
			.catch(() => {
				// Nothing to do: the label names the system, as it does in a browser.
			});
	}

	#report(surface: Surface, state: ScreenState): ScreenReport {
		const app = this.#app();
		const supports: RemoteAction[] = offerable(surface.actions)
			/* Filling the screen needs a press made AT the desk: a browser refuses to go full screen
			   for anything else, and a command from the phone is not a press, so the phone is never
			   offered it: the person at the desk presses Full screen. */
			.filter((action) => action !== 'player.fill');
		return {
			label: labelFor(app, this.#agent(), this.#machine),
			surface: surface.kind,
			app,
			playing: state.playing,
			position: seconds(state.position) ?? 0,
			length: seconds(state.length),
			file: state.file,
			volume: Math.min(100, Math.max(0, Math.round(state.volume))),
			muted: state.muted,
			supports,
			acted_on: this.#actedOn,
			cells: state.cells ?? 0,
			focused: state.focused ?? null,
			...this.#extras(state)
		};
	}

	/** The drawer and the lists, bounded to what the server takes. See `Extras`. */
	#extras(state: ScreenState): Extras {
		const qualities = choices(state.qualities);
		const layouts = choices(state.layouts);
		return {
			repeat: state.repeat ?? null,
			shuffle: state.shuffle ?? null,
			loop_marks: Math.min(2, Math.max(0, state.loop_marks ?? 0)),
			qualities,
			quality: place(state.quality, qualities),
			favorite: state.favorite ?? null,
			count: state.count ?? null,
			timer: state.timer ?? null,
			every_cell: state.every_cell ?? false,
			cell_held: state.cell_held ?? null,
			cell_muted: state.cell_muted ?? null,
			layouts,
			layout: place(state.layout, layouts),
			presets: choices(state.presets),
			cell_files: (state.cell_files ?? []).slice(0, state.cells ?? 0)
		};
	}

	/**
	 * Read the surface, and say where it stands when anything but the clock moved it or the report
	 * is due. Public for a test, which drives it rather than waiting on the timer.
	 */
	async look(now: boolean): Promise<void> {
		const surface = this.#current();
		if (surface === null) return;
		const state = surface.state();
		const report = this.#report(surface, state);
		const key = JSON.stringify({ ...report, position: 0 });
		const at = this.#now();
		const said = this.#said;
		const carried =
			said === null ? 0 : said.position + (said.playing ? Math.max(0, (at - said.at) / 1000) : 0);
		const due =
			now ||
			said === null ||
			key !== said.report ||
			at - said.at >= REPORT_EVERY_MS ||
			Math.abs(report.position - carried) > DRIFT_SECONDS;
		if (!due) return;
		this.#said = { report: key, position: report.position, playing: report.playing, at };
		try {
			await api.post(`/remote/screens/${this.screen}`, { body: report });
			if (this.controlledBy.length > 0) void this.#readControllers();
		} catch {
			/* Kept as said, so the next attempt is when the next report is due rather than on the
			   next look: a session that is locked or signed out would otherwise be asked every
			   second. The server lets the screen go when it stops hearing from it. */
		}
	}
}

/** This tab's offer. One per tab, because a tab is one screen. */
export const screenOffer = new ScreenOffer();
