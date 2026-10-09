/*
 * The phone's side of the remote: the user's screens, re-read on every bell, on return from the
 * background and every half of `listed_for_seconds`. Ages move by `performance.now()`, never the
 * wall clock. A command landed when its id comes back as `acted_on`; the screen on the card is
 * told this phone drives it, said again every `REPORT_EVERY_MS`.
 */

import { ApiError, api } from '$lib/api/client';
import { screenChanges } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';
import type { RemoteAction } from '$lib/shell/shortcuts';

import { labelFor, REPORT_EVERY_MS } from './offer.svelte';
import type { CommandSent, RemoteScreens, ScreenOut } from './wire';

const SCREENS = '/remote/screens';

type AssetDetail = components['schemas']['AssetDetail'];

/** The server drops an untaken command after four seconds (`COMMAND_SECONDS`). */
const ANSWER_MS = 5_000;

/** Twice a second, so a position shown in whole seconds never looks stuck. */
const TICK_MS = 500;

const NO_ANSWER = "The screen didn't answer. It may be asleep or closed.";

/** This phone's name for the server while it drives a screen, made on plain http too. */
function mintController(): string {
	const bytes = new Uint8Array(16);
	crypto.getRandomValues(bytes);
	return 'phone-' + Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export class RemoteList {
	screens = $state<ScreenOut[]>([]);
	/** Whether the list has been read, so a page can tell "none" from "not yet". */
	read = $state(false);
	/** Tabs holding a player or wall back with their switch off; the page says where it is. */
	notOffering = $state(0);
	/** The screen driven, kept while listed: the list reorders every few seconds. */
	picked = $state<string | null>(null);
	/** The files' names, by id, as the phone's own session reads them. See `learnNames`. */
	names = $state<Record<string, string>>({});
	problem = $state<string | null>(null);
	/** This page's clock, refreshed while the list is watched. */
	at = $state(0);
	#listedFor = $state(30);
	#readAt = $state(0);
	#waiting: { id: string; screen: string; sentAt: number } | null = null;
	/* The press that went unanswered; its line stands until that screen speaks (`#heardSince`). */
	#unanswered: { screen: string; at: number } | null = null;
	#asked = new Set<string>();
	#watching = 0;
	#timer: ReturnType<typeof setInterval> | null = null;
	#tick: ReturnType<typeof setInterval> | null = null;
	readonly controller = mintController();
	#held: { screen: string; at: number } | null = null;
	readonly #now: () => number;
	readonly #label: () => string;

	constructor(
		now: () => number = () => performance.now(),
		label: () => string = () => labelFor(false, navigator.userAgent)
	) {
		this.#now = now;
		this.#label = label;
		this.at = now();
		// Lives as long as the tab, so the plain subscription rather than the rune.
		screenChanges.subscribe(() => {
			if (this.#watching > 0) void this.load().catch(() => {});
		});
	}

	async load(): Promise<void> {
		let answer: RemoteScreens;
		try {
			answer = await api.get<RemoteScreens>(SCREENS);
		} catch (error) {
			this.problem = wordsFor(error);
			throw error;
		}
		this.screens = answer.screens;
		this.notOffering = answer.not_offering;
		this.#listedFor = answer.listed_for_seconds;
		this.#readAt = this.#now();
		this.at = this.#readAt;
		this.read = true;
		if (!answer.screens.some((one) => one.screen === this.picked)) {
			this.picked = answer.screens[0]?.screen ?? null;
		}
		this.#settleWaiting();
		this.#heardSince();
		this.learnNames();
		if (this.#watching > 0) this.#pace(answer.listed_for_seconds);
		this.#settleHold();
	}

	/** Tell the screens which one this phone drives, letting go now; said again when due. */
	#settleHold(): void {
		const inFront = typeof document === 'undefined' || document.visibilityState !== 'hidden';
		const target = this.#watching > 0 && inFront ? (this.current?.screen ?? null) : null;
		const held = this.#held;
		const at = this.#now();
		if (held !== null && held.screen !== target) {
			this.#held = null;
			void this.#quietly(() => api.del(`${SCREENS}/${held.screen}/controllers/${this.controller}`));
		}
		if (target === null) return;
		if (this.#held !== null && at - this.#held.at < REPORT_EVERY_MS) return;
		this.#held = { screen: target, at };
		void this.#quietly(() =>
			api.put(`${SCREENS}/${target}/controllers/${this.controller}`, {
				body: { label: this.#label() }
			})
		);
	}

	/** A request whose failure changes nothing: the server lets a quiet phone go. */
	async #quietly(request: () => Promise<unknown>): Promise<void> {
		try {
			await request();
		} catch {
			// Nothing to do; see above.
		}
	}

	#pace(listedFor: number): void {
		if (this.#timer !== null) clearInterval(this.#timer);
		this.#timer = setInterval(() => void this.load().catch(() => {}), (listedFor * 1000) / 2);
	}

	tick(): void {
		this.at = this.#now();
		const waiting = this.#waiting;
		if (waiting !== null && this.at - waiting.sentAt >= ANSWER_MS) {
			this.#waiting = null;
			this.problem = NO_ANSWER;
			this.#unanswered = { screen: waiting.screen, at: this.at };
		}
	}

	/* The screen that did not answer has spoken since, so the line saying it may be asleep goes. */
	#heardSince(): void {
		const unanswered = this.#unanswered;
		if (unanswered === null || this.problem !== NO_ANSWER) return;
		const screen = this.screens.find((one) => one.screen === unanswered.screen);
		if (screen === undefined) return;
		if (this.#readAt - screen.heard_seconds_ago * 1000 <= unanswered.at) return;
		this.#unanswered = null;
		this.problem = null;
	}

	#settleWaiting(): void {
		const waiting = this.#waiting;
		if (waiting === null) return;
		const screen = this.screens.find((one) => one.screen === waiting.screen);
		if (screen?.acted_on !== waiting.id) return;
		this.#waiting = null;
		if (this.problem === NO_ANSWER) this.problem = null;
	}

	/** Start watching the list; a phone back from a pocket may have missed any number of bells. */
	watch(): () => void {
		this.#watching += 1;
		void this.load().catch(() => {});
		this.#tick ??= setInterval(() => this.tick(), TICK_MS);
		const back = () => {
			if (document.visibilityState === 'visible') void this.load().catch(() => {});
			else this.#settleHold();
		};
		document.addEventListener('visibilitychange', back);
		return () => {
			document.removeEventListener('visibilitychange', back);
			this.#watching -= 1;
			this.#settleHold();
			if (this.#watching > 0) return;
			if (this.#timer !== null) clearInterval(this.#timer);
			if (this.#tick !== null) clearInterval(this.#tick);
			this.#timer = null;
			this.#tick = null;
		};
	}

	/** Ask once each for the playing files' names, through the file's own page door. */
	learnNames(): void {
		for (const screen of this.screens) {
			const id = screen.file;
			if (id === null || this.#asked.has(id)) continue;
			this.#asked.add(id);
			void api
				.get<AssetDetail>(`/assets/${id}`)
				.then((file) => {
					if (file.filename) this.names = { ...this.names, [id]: file.filename };
				})
				.catch(() => this.#asked.delete(id));
		}
	}

	heardAgo(screen: ScreenOut): number {
		return screen.heard_seconds_ago + Math.max(0, (this.at - this.#readAt) / 1000);
	}

	/** The screens still speaking by this page's clock, let go when the server would. */
	get live(): ScreenOut[] {
		return this.screens.filter((screen) => this.heardAgo(screen) < this.#listedFor);
	}

	get current(): ScreenOut | null {
		const live = this.live;
		return live.find((one) => one.screen === this.picked) ?? live[0] ?? null;
	}

	pick(screen: string): void {
		this.picked = screen;
		this.#settleHold();
		/* The line is about a screen that is no longer the one on the card. */
		if (this.problem === NO_ANSWER && this.#unanswered?.screen !== screen) {
			this.#unanswered = null;
			this.problem = null;
		}
	}

	positionOf(screen: ScreenOut): number {
		if (!screen.playing) return screen.position;
		const carried = screen.position + Math.max(0, (this.at - this.#readAt) / 1000);
		return screen.length === null ? carried : Math.min(carried, screen.length);
	}

	/** Send one command and wait for it to land; resolves to its id, or null when refused. */
	async send(
		screen: string,
		action: RemoteAction,
		value: number | null = null
	): Promise<string | null> {
		try {
			const sent = await api.post<CommandSent>(`${SCREENS}/${screen}/commands`, {
				body: { action, value }
			});
			this.problem = null;
			this.#waiting = { id: sent.id, screen, sentAt: this.#now() };
			return sent.id;
		} catch (error) {
			this.problem = wordsFor(error);
			if (error instanceof ApiError && error.status === 404) void this.load().catch(() => {});
			return null;
		}
	}
}

function wordsFor(error: unknown): string {
	if (error instanceof ApiError) return error.detail ?? error.message;
	return error instanceof Error ? error.message : String(error);
}

export const remoteList = new RemoteList();
