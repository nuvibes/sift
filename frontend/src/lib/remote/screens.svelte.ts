/*
 * The phone's side of the remote: the signed-in user's screens, and a command sent to one.
 *
 * The list is read when a page that shows it asks, and again whenever the server says one of the
 * screens moved (`screenChanges`), whenever the page comes back from the background, and at least
 * every half of `listed_for_seconds`, so a screen that stopped speaking leaves the list when the
 * server lets it go rather than when somebody reloads.
 *
 * **Every age here is carried on by this page's own clock** from the moment the list was read,
 * never by the wall clock: the server has already carried the position to the moment it answered,
 * and a wall clock can step backwards (a machine correcting its time does). So a position, and how long ago a screen last spoke, both move on by `performance.now()` alone.
 *
 * ## Whether a command landed
 *
 * The server accepting a command says only that it was handed on. The screen says it DID it by
 * reporting the command's id back as `acted_on`, which rings the bell, which re-reads this list.
 * A command whose id has not come back by `ANSWER_MS` is one the screen never heard (asleep, gone,
 * or a tab frozen in the background), and the page says so in one line rather than leaving a
 * press that did nothing looking like a press that is still on its way.
 *
 * ## Saying which screen this phone is driving
 *
 * While a page watches the list and this phone is in front of somebody, the screen on the card is
 * told it is being driven, and by what (`PUT .../controllers/{this phone}`), so the desk can say
 * so; picking another screen, leaving the page or putting the phone away lets go of it at once.
 * Said again every `REPORT_EVERY_MS` while it stays on the same screen, because the server lets a
 * phone go when it stops saying so, which is what happens to a phone that was simply switched off.
 */

import { ApiError, api } from '$lib/api/client';
import { screenChanges } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';
import type { RemoteAction } from '$lib/shell/shortcuts';

import { labelFor, REPORT_EVERY_MS } from './offer.svelte';
import type { CommandSent, RemoteScreens, ScreenOut } from './wire';

const SCREENS = '/remote/screens';

type AssetDetail = components['schemas']['AssetDetail'];

/**
 * How long a press waits for its screen to say it acted before the page says it did not.
 *
 * The server drops a command nobody took within four seconds (`COMMAND_SECONDS`), so by five the
 * answer is no: a screen that had heard it would have acted and said so well inside that.
 */
const ANSWER_MS = 5_000;

/** How often the page's own clock is read while the list is watched: a position moves in whole
 *  seconds on screen, so twice a second keeps it from ever looking stuck. */
const TICK_MS = 500;

/** What the page says when a screen took no notice of a press. */
const NO_ANSWER = "The screen didn't answer. It may be asleep or closed.";

/** This phone's name for the server while it drives a screen: random, and made on plain http. */
function mintController(): string {
	const bytes = new Uint8Array(16);
	crypto.getRandomValues(bytes);
	return 'phone-' + Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export class RemoteList {
	/** The user's screens as last read, the most recently heard first. */
	screens = $state<ScreenOut[]>([]);
	/** Whether the list has been read at least once, so a page can tell "none" from "not yet". */
	read = $state(false);
	/**
	 * How many of the user's browser tabs hold a player or a wall they do not offer, because that
	 * browser's own switch is off. The page says where the switch is while this is above nought.
	 */
	notOffering = $state(0);
	/**
	 * The screen the page is driving, by its id.
	 *
	 * The one heard from last when the list is first read, and then kept while it is listed: the
	 * server orders the list by who spoke last, and every screen speaks every few seconds, so a pick
	 * made by position would hop between two desks on its own. Picked again, the same way, only
	 * when the one picked has gone.
	 */
	picked = $state<string | null>(null);
	/** The files' names, by id, as the phone's own session reads them. See `learnNames`. */
	names = $state<Record<string, string>>({});
	/** One line saying what went wrong with the last press or read, or null. */
	problem = $state<string | null>(null);
	/** This page's clock, refreshed while the list is watched. What every age below is read from. */
	at = $state(0);
	/** How long the server keeps listing a screen that stopped speaking, in seconds. */
	#listedFor = $state(30);
	/** When the list was read, on this page's own clock. */
	#readAt = $state(0);
	/** The press still waiting for its screen to say it acted. */
	#waiting: { id: string; screen: string; sentAt: number } | null = null;
	/* The press that went unanswered: which screen, and when this page gave up on it. The line saying
	   so stands until that screen speaks again or another is picked; see `#heardSince`. */
	#unanswered: { screen: string; at: number } | null = null;
	#asked = new Set<string>();
	#watching = 0;
	#timer: ReturnType<typeof setInterval> | null = null;
	#tick: ReturnType<typeof setInterval> | null = null;
	/** This phone, to the server, for as long as the tab is open. */
	readonly controller = mintController();
	/** The screen this phone last said it is driving, and when, on this page's own clock. */
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
		// A store that lives as long as the tab, so the plain subscription rather than the rune.
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

	/**
	 * Tell the screens which one this phone is driving: the one on the card while a page watches
	 * and the phone is in front of somebody, and none otherwise. Let go of the one it was driving
	 * the moment that changes; said again only when due, so a list re-read on every bell does not
	 * turn into a request per bell.
	 */
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

	/** A request whose failure changes nothing: the server lets a phone go that stops saying so. */
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

	/** Re-read the page's clock, and give up on a press whose screen has had long enough. */
	tick(): void {
		this.at = this.#now();
		const waiting = this.#waiting;
		if (waiting !== null && this.at - waiting.sentAt >= ANSWER_MS) {
			this.#waiting = null;
			this.problem = NO_ANSWER;
			this.#unanswered = { screen: waiting.screen, at: this.at };
		}
	}

	/*
	 * The screen that did not answer has spoken since: it is not asleep or closed, so the line
	 * saying it may be is taken away: otherwise the line stands under a card that has
	 * just reported a new file and a new position, contradicting it, until the next press.
	 */
	#heardSince(): void {
		const unanswered = this.#unanswered;
		if (unanswered === null || this.problem !== NO_ANSWER) return;
		const screen = this.screens.find((one) => one.screen === unanswered.screen);
		if (screen === undefined) return;
		if (this.#readAt - screen.heard_seconds_ago * 1000 <= unanswered.at) return;
		this.#unanswered = null;
		this.problem = null;
	}

	/** A press answered: its id came back on the screen it was sent to. */
	#settleWaiting(): void {
		const waiting = this.#waiting;
		if (waiting === null) return;
		const screen = this.screens.find((one) => one.screen === waiting.screen);
		if (screen?.acted_on !== waiting.id) return;
		this.#waiting = null;
		if (this.problem === NO_ANSWER) this.problem = null;
	}

	/**
	 * A page showing the list starts watching it. Returns what stops.
	 *
	 * Watching reads the list now, on every bell, on every return from the background (a phone
	 * that was put in a pocket may have missed any number of bells, and its timers were paused),
	 * and at least every half of the time a quiet screen stays listed.
	 */
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

	/**
	 * Ask for the names of the files the screens are playing, once each.
	 *
	 * The list carries a file's id only, and only when this phone's own session may open it, so
	 * the name is asked for through the same door the file's own page uses: a file this session
	 * cannot open is never named here, whatever the desk has open. Remembered for as long as the
	 * tab, since a name the phone has read once does not need reading again for the next report.
	 */
	learnNames(): void {
		for (const screen of this.screens) {
			const id = screen.file;
			if (id === null || this.#asked.has(id)) continue;
			this.#asked.add(id);
			void api
				.get<AssetDetail>(`/assets/${id}`)
				.then((file) => {
					/* The filename, as the docked corner player names what it holds. */
					if (file.filename) this.names = { ...this.names, [id]: file.filename };
				})
				.catch(() => this.#asked.delete(id));
		}
	}

	/** Seconds since a screen last spoke, carried on from when the list was read. */
	heardAgo(screen: ScreenOut): number {
		return screen.heard_seconds_ago + Math.max(0, (this.at - this.#readAt) / 1000);
	}

	/**
	 * The screens still speaking, by this page's own clock.
	 *
	 * The server lets a quiet screen go when asked after `listed_for_seconds`; between two reads
	 * this page lets it go at the same moment, so a desktop put to sleep leaves the phone's list
	 * when it stops being controllable rather than up to one re-read later.
	 */
	get live(): ScreenOut[] {
		return this.screens.filter((screen) => this.heardAgo(screen) < this.#listedFor);
	}

	/** The screen being driven: the one picked while it is still speaking, else the first that is. */
	get current(): ScreenOut | null {
		const live = this.live;
		return live.find((one) => one.screen === this.picked) ?? live[0] ?? null;
	}

	/** Drive another of the screens. */
	pick(screen: string): void {
		this.picked = screen;
		this.#settleHold();
		/* The line is about the screen that did not answer, and it is no longer the one on the card. */
		if (this.problem === NO_ANSWER && this.#unanswered?.screen !== screen) {
			this.#unanswered = null;
			this.problem = null;
		}
	}

	/** Where a screen has got to by now, carried on from when the list was read. */
	positionOf(screen: ScreenOut): number {
		if (!screen.playing) return screen.position;
		const carried = screen.position + Math.max(0, (this.at - this.#readAt) / 1000);
		return screen.length === null ? carried : Math.min(carried, screen.length);
	}

	/**
	 * Send one command, and wait for the screen to say it acted (see the head of the file).
	 *
	 * A refusal says why in one line, in the server's own words where it wrote some for people
	 * ("That screen can't do that."). A screen the server no longer has re-reads the list, so the
	 * screen that went quiet leaves it at once. Resolves to the command's id, or null when refused.
	 */
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

/** One line for a failure: the server's own sentence where it wrote one, else the plain one. */
function wordsFor(error: unknown): string {
	if (error instanceof ApiError) return error.detail ?? error.message;
	return error instanceof Error ? error.message : String(error);
}

/** The one list, for every page of this tab that shows it. */
export const remoteList = new RemoteList();
