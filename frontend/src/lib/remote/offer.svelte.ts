/*
 * This tab offering its open player or Theater wall to the signed-in user's phone: it reports what
 * it does when that changes (and every `REPORT_EVERY_MS`), and a command arrives down the live
 * connection to be answered from the same table a key would be. The desktop app offers by
 * default, a browser tab only when switched on; one holding back says so (`/remote/quiet`).
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

/** The server's `REPORT_EVERY_SECONDS`: it lets a screen go after three are missed. */
export const REPORT_EVERY_MS = 10_000;

const LOOK_EVERY_MS = 1_000;

/** How far a position may move past where playing took it before it is a seek. */
const DRIFT_SECONDS = 2;

/** Where this browser remembers its switch: a fact about the browser, not the user. */
const THIS_BROWSER_KEY = 'sift.remote.this_browser';

/** Where a player or wall stands: the server's shape, less what it adds on the way out. */
export type ScreenState = Pick<
	components['schemas']['ScreenOut'],
	'playing' | 'position' | 'length' | 'file' | 'volume' | 'muted'
> &
	Partial<Pick<components['schemas']['ScreenOut'], 'cells' | 'focused'>> &
	Partial<Extras>;

/** The drawer's state and lists in the report's fields; the phone dims what is not offered. */
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

/** The server's `MOST_CHOICES` and its `Choice`: a longer list is cut, not refused. */
const MOST_CHOICES = 32;
const LONGEST_CHOICE = 80;

/** A list cut to what the server takes; an empty name is dropped, as the server refuses it. */
function choices(list: readonly string[] | undefined): string[] {
	return (list ?? [])
		.slice(0, MOST_CHOICES)
		.map((one) => one.slice(0, LONGEST_CHOICE))
		.filter((one) => one.length > 0);
}

function place(at: number | null | undefined, among: readonly string[]): number | null {
	return at === null || at === undefined || at < 0 || at >= among.length ? null : at;
}

/** What an open player or wall hands over when it offers itself. */
export type Surface =
	| { kind: 'player'; actions: Actions<PlayerAction>; state: () => ScreenState }
	| { kind: 'theater'; actions: Actions<TheaterAction>; state: () => ScreenState };

/** What a viewer's content hands up so the viewer can offer it. */
export interface Offerable {
	actions: Actions<PlayerAction>;
	state: () => ScreenState;
}

/**
 * Offer a viewer (the popout, the mini player) to the phone: the viewer, not the video component,
 * so a photograph shown in it is offered too. Returns the withdrawal.
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

/** A screen's name, from `getRandomValues`: `randomUUID` needs a secure connection. */
function mintScreen(): string {
	const bytes = new Uint8Array(16);
	crypto.getRandomValues(bytes);
	return 'screen-' + Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/** Asks whether the bridge is there at all: "controllable by default" is about the window. */
function inTheApp(): boolean {
	return bridge.isDesktop();
}

/** What the phone calls this screen; the app names its computer when the shell can tell. */
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

/** The mark on a controlled screen: "Remote controlled", then the phones in their own names. */
export function controlledWords(by: readonly string[]): string {
	if (by.length === 0) return '';
	const names = by.length === 1 ? by[0] : `${by.slice(0, -1).join(', ')} and ${by.at(-1)}`;
	return `Remote controlled from ${names}`;
}

function seconds(value: number | null): number | null {
	return value === null || !Number.isFinite(value) || value < 0 ? null : value;
}

export class ScreenOffer {
	/** This tab's screen; a reload is a new screen. */
	readonly screen = mintScreen();

	thisBrowser = $state(readStored(THIS_BROWSER_KEY) === 'on');

	/**
	 * The phones driving this screen, most recent first, read back from the phone's own list: a
	 * command says nothing about which phone sent it.
	 */
	controlledBy = $state<string[]>([]);
	controlledSurface = $state<Surface['kind'] | null>(null);

	/** The surfaces open, the newest last; only the newest, on top, is offered. */
	#surfaces: Surface[] = [];
	#timer: ReturnType<typeof setInterval> | null = null;
	#quietTimer: ReturnType<typeof setInterval> | null = null;
	#saidQuiet = false;
	#said: { report: string; position: number; playing: boolean; at: number } | null = null;
	/** The last command acted on, said back so the phone knows it landed. */
	#actedOn: string | null = null;
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
		// Lives as long as the tab, so the plain subscription rather than the rune.
		screenChanges.subscribe(() => {
			if (this.#current() !== null) void this.#readControllers();
		});
	}

	async #readControllers(): Promise<void> {
		let answer: RemoteScreens | undefined;
		try {
			answer = await api.get<RemoteScreens>('/remote/screens');
		} catch {
			/* The mark keeps what it last knew until the next bell or report. */
			return;
		}
		const mine = answer?.screens.find((one) => one.screen === this.screen);
		const surface = this.#current();
		this.controlledBy = surface === null ? [] : (mine?.controlled_by ?? []);
		this.controlledSurface = surface?.kind ?? null;
	}

	get wallControlled(): boolean {
		return this.controlledSurface === 'theater' && this.controlledBy.length > 0;
	}

	get offered(): boolean {
		return this.#app() || this.thisBrowser;
	}

	get holdingBack(): boolean {
		return !this.offered && this.#surfaces.length > 0;
	}

	setThisBrowser(on: boolean): void {
		this.thisBrowser = on;
		writeStored(THIS_BROWSER_KEY, on ? 'on' : 'off');
		this.#settle();
	}

	offer(surface: Surface): () => void {
		this.#surfaces.push(surface);
		this.#settle();
		return () => {
			const at = this.#surfaces.lastIndexOf(surface);
			if (at >= 0) this.#surfaces.splice(at, 1);
			this.#settle();
		};
	}

	/** Commands from the live connection: only this screen's, and only by the surface on top. */
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
		// Said immediately: the phone is waiting for exactly this.
		if (acted) void this.look(true);
	}

	#current(): Surface | null {
		return this.offered ? (this.#surfaces.at(-1) ?? null) : null;
	}

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
			// A screen not withdrawn is let go of when it stops reporting.
		});
	}

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
			// The server stops counting a tab that stops saying so.
		});
	}

	async #sayQuiet(): Promise<void> {
		this.#saidQuiet = true;
		try {
			await api.put(`/remote/quiet/${this.screen}`);
		} catch {
			/* Said again on the next beat. */
		}
	}

	/** Ask the shell once what this computer is called; an older shell answers null. */
	#askMachine(): void {
		if (this.#askedMachine || !this.#app()) return;
		this.#askedMachine = true;
		void this.#machineName()
			.then((name) => {
				this.#machine = name;
				if (name !== null) void this.look(false);
			})
			.catch(() => {
				// The label names the system, as it does in a browser.
			});
	}

	#report(surface: Surface, state: ScreenState): ScreenReport {
		const app = this.#app();
		const supports: RemoteAction[] = offerable(surface.actions)
			/* Full screen needs a press made at the desk, so the phone is never offered it. */
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

	/** Read the surface and report when anything but the clock moved it, or a report is due. */
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
			/* Kept as said, so a locked or signed-out session is not asked every second. */
		}
	}
}

export const screenOffer = new ScreenOffer();
