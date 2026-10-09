/*
 * What the top bar holds for the screen under it; only the screen knows what it can do. A
 * publisher holds a token, since the next screen mounts before the last one unmounts.
 */

import type { Attachment } from 'svelte/attachments';
import type { Component, Snippet } from 'svelte';
import { untrack } from 'svelte';
import { page } from '$app/state';
import type { IconName } from '$lib/design/icons';
import type { Subject } from './facet-labels';
import type { Verb } from '$lib/components/common/verbs';
import type { NarrowedToUsername } from '$lib/grid/grid.svelte';
import { PHONE_WIDTH } from '$lib/components/common/phone-width.svelte';
import { stage } from './stage.svelte';

export interface SortChoice {
	value: string;
	label: string;
	/** A row that is a press rather than an order (`action` in `common/Select.svelte`). */
	action?: boolean;
	/** Dimmed and never chosen: an order this wall cannot be put in now. */
	disabled?: boolean;
	/** A second line under the name: what the order is measured against, or why it is dimmed. */
	note?: string;
	/** Words shown when the row is pointed at (`tooltip` in `common/Select.svelte`). */
	tooltip?: string;
}

/** What the Filter control filters when not the address: a saved filter's draft, or a cell. */
export interface Narrowing {
	read: () => URLSearchParams;
	write: (next: URLSearchParams) => void;
	choose?: (next: URLSearchParams) => void;
	/** Spread by a control that will filter the target, to say which one. */
	pointing?: Pointing;
	/** What the target filters by on its own control, which the panel never writes. */
	within?: () => URLSearchParams;
}

/** The handlers and the let-go attachment a control spreads. See `$lib/theater/aim`. */
export type Pointing = {
	onmouseenter: (event?: Event) => void;
	onmouseleave: () => void;
	onfocus: (event?: Event) => void;
	onblur: () => void;
	[key: symbol]: Attachment;
};

// `true` acts; a string is why not, shown on hover: the bar keeps its shape, controls dim.
type Capability = true | string;

/** The orders a screen offers, out of what it published. */
export function ordersOffered(sorts: readonly SortChoice[] | string | undefined): SortChoice[] {
	// Copied, because the chooser takes a mutable list and a screen's table is `as const`.
	return Array.isArray(sorts) ? [...sorts] : [];
}

/** Whether a control may act, given what its screen said. Anything but `true` is a no. */
export function ableTo(capability: Capability | undefined): boolean {
	return capability === true;
}

// For a screen that said nothing; the bar is still above it, and its controls still answer.
export const NOT_HERE = {
	filter: 'Nothing to filter on this screen',
	sort: 'Nothing to order on this screen',
	resize: 'Nothing to resize on this screen',
	play: 'Nothing to preview on this screen'
} as const;

/** What a control says when it cannot act: the screen's own reason, or the general one. */
export function whyNot(capability: Capability | undefined, fallback: string): string {
	return typeof capability === 'string' ? capability : fallback;
}

// The one way a panel opens from the bar; it reads its screen's stores, so takes no props.
interface BarPanel {
	/** Stable, and unique on this screen. It is what the bar remembers as open. */
	id: string;
	icon: IconName;
	/** The tooltip, and what a screen reader is told the button opens. */
	label: string;
	content: Component<Record<string, never>>;
	/** At the head of the row, where Filter and Order decide what the screen shows. */
	lead?: boolean;
	/** Draw the trigger on the top bar instead, for a panel about the whole app (the key sheet). */
	atTheTop?: boolean;
}

/** A menu of this screen's own on the bar's row: a list, one of which is current. See `menus`. */
interface BarMenu {
	/** Stable, and unique on this screen. It is what the bar remembers as open. */
	id: string;
	icon: IconName;
	label: string;
	options: SortChoice[];
	value: string;
	onChoose: (next: string) => void;
	/** A picture per option, where words are not the answer (`Select.preview`). */
	preview?: Snippet<[SortChoice]>;
}

/** The built-in panel: the facets, which belong to the query language rather than to any screen. */
export const FILTERS_PANEL = 'filters';

// A menu, but the row holds one open thing at a time, so its id lives here.
export const SORT_MENU = 'sort';

// One phone sheet for Filter and Sort; its own id, as a screen may order without filtering.
export const PHONE_SHEET = 'filter-and-sort';

// A menu reports its own pointer; a panel is reported by the drawer.
function isAMenu(id: string | null, tools: ScreenTools): boolean {
	if (id === null) return false;
	if (id === SORT_MENU) return true;
	// A screen's own menu is the same control as the order, read from the published list.
	return (tools.menus ?? []).some((one) => one.id === id);
}

/** Everything the bar can draw for a screen. Every part is optional; a screen fills what it has. */
interface ScreenTools {
	/** What this screen is, as filters (a person's page is `people:<them>`): a fixed chip. */
	query?: Record<string, string>;

	/** How many things the screen shows, as its header counts; absent while counting. */
	count?: number;

	/** The username `?username=` filters to, as the server named it, or null. */
	username?: NarrowedToUsername | null;

	/** Whether the query language applies: true on a wall, else the reason. */
	filterable?: Capability;

	/** Which noun this wall shows, deciding the facet panel's columns. Absent means files. */
	subject?: Subject;

	/** A chip's own verbs (its right-click menu), for a chip naming a thing with verbs. */
	chipVerbs?: (field: string, value: string, label: string) => readonly Verb[];

	/** Facets every row here has one value of (the Loops wall's Loop), left out of the panel. */
	fixed?: readonly string[];

	/** The address's name for this wall's own box words, where not `q` (see `wall-words`). */
	words?: string;

	/** What the Filter control filters here, when the address is the wrong answer. */
	narrowing?: Narrowing;

	/** A control at the head of the filter panel, drawn by the screen (which Theater cell). */
	narrowingLead?: Snippet;

	/** What the filtered thing is called, at the head of the chips row; grey, not the accent. */
	narrowingName?: string;

	/** Menus of the screen's own beside Filter and Order, such as the Theater layout. */
	menus?: BarMenu[];

	/** The orders offered and the one in force; a string says why there are none. */
	sorts?: readonly SortChoice[] | string;
	sort?: string;
	onSort?: (next: string) => void;

	/* Whether the tile-size slider has anything to resize: drawn everywhere, dimmed where not. */
	resizable?: Capability;

	/** Whether the tiles can play, apart from resizing: a wall of cards resizes, plays nothing. */
	playable?: Capability;

	/** Whether the screen is moving now (hover previews, Theater's feeds); the screen answers. */
	playing?: boolean;
	onPlay?: () => void;
	playLabel?: string;
	/** The key this screen answers the control with, read from the act table (`keyOf`), if any. */
	playShortcut?: string;

	/** Anything this screen has that nothing else does, drawn at the end of the bar. */
	extra?: Snippet;

	/** The same on the top bar, for a control about the whole window (the wall's silence). */
	topExtra?: Snippet;

	/** After the chip naming what the row filters: Theater's play and silence for everything. */
	besideTheName?: Snippet;

	/** Fade this row with the screen's own bar (the Theater wall), not over the pictures. */
	quiet?: boolean;

	/** Panels this screen can drop open, each a button on the bar. */
	panels?: BarPanel[];
}

const NOTHING: ScreenTools = {};

/* Filter and Sort with their two gaps, read off the bar rather than the window. */
const MENUS_ROOM = 88;

/** The search field's floor: at 228px it holds one row, at 226 it wraps. */
export const FIELD_FLOOR = 227;

const SEARCH_FIELD = 'form.search';

/** The end group's parts, whose widths are what each side of the centre group keeps. */
const BAR_END = '.actions > *';

const TILE_SIZE = '.size';

const PASTE_HALF = '.add .half.trail';

/* Under the phone's width (`PHONE_WIDTH`) the bar is a row of squares and none of this applies. */

interface Need {
	room: number;
	end: number;
}

// By route, kept in this browser: the bar lays out for the widest screen met, so nothing moves.
const NEEDS_KEY = 'sift.screen-bar.needs.2';
const needs = new Map<string, Need>(readNeeds());

function readNeeds(): [string, Need][] {
	try {
		const kept: unknown = JSON.parse(localStorage.getItem(NEEDS_KEY) ?? '{}');
		if (kept === null || typeof kept !== 'object') return [];
		return Object.entries(kept as Record<string, Need>).filter(([, one]) =>
			[one?.room, one?.end].every(Number.isFinite)
		);
	} catch {
		return [];
	}
}

/** The most any screen met has needed: what every screen's bar is laid out for. */
function widest(): Need {
	let most: Need = { room: 0, end: 0 };
	for (const one of needs.values())
		most = { room: Math.max(most.room, one.room), end: Math.max(most.end, one.end) };
	return most;
}

function keepNeed(screen: string, need: Need): void {
	const was = needs.get(screen);
	if (was && was.room === need.room && was.end === need.end) return;
	needs.set(screen, need);
	try {
		localStorage.setItem(NEEDS_KEY, JSON.stringify(Object.fromEntries(needs)));
	} catch {
		// Kept for this sitting only.
	}
}

class ScreenBar {
	#tools = $state<ScreenTools>(NOTHING);
	/** Who filled it. Only they may empty it. See the ordering note above. */
	#owner: symbol | null = null;

	/** The open panel's id; here so a screen can open its own. Cleared on release, not publish. */
	open = $state<string | null>(null);

	/** The kept filter this screen is, by id, worked out once by the bar (`appliedKept`). */
	keptInForce = $state<string | null>(null);

	/** Whether the named menus fit on the top bar; one home draws them, never a hidden copy. */
	roomOnTopBar = $state(true);

	/** The end group's width (`--bar-end`), the least each side of the centre group keeps. */
	barEnd = $state(0);

	/** Whether the tile size is on the bar: it leaves before the centre group would slide. */
	sizeOnBar = $state(true);

	/** Whether Add's paste half is on the bar: it folds into Add after the tile size has gone. */
	pasteOnBar = $state(true);

	/** The tile size's panel on the screen's own row while it is off the top bar. */
	sizeHome = $state.raw<BarPanel | null>(null);

	/** Watch the top bar's content box, its end group and its field. Hands back the teardown. */
	watchRoom(bar: HTMLElement): () => void {
		// jsdom has no `ResizeObserver`.
		if (typeof ResizeObserver === 'undefined') return () => {};
		const phone = typeof matchMedia === 'function' ? matchMedia(PHONE_WIDTH) : null;
		const field = bar.querySelector<HTMLElement>(SEARCH_FIELD);
		const ends = [...bar.querySelectorAll<HTMLElement>(BAR_END)];
		const gapOf = (box: Element | null) =>
			box === null ? 0 : Number.parseFloat(getComputedStyle(box).columnGap) || 0;
		const endWidth = () =>
			Math.ceil(
				ends.reduce((total, one) => total + one.getBoundingClientRect().width, 0) +
					gapOf(ends[0]?.parentElement ?? null) * Math.max(0, ends.length - 1)
			);
		const size = bar.querySelector<HTMLElement>(TILE_SIZE);
		const pasteWidth = () => bar.querySelector(PASTE_HALF)?.getBoundingClientRect().width ?? 0;
		let sizeRoom = 0;
		let pasteRoom = 0;
		/* The end group with all that may leave counted in, so a leaving moves no threshold. */
		let end = 0;
		let drawn = 0;
		const menusBeside = (group: number) =>
			2 * (group + gapOf(bar)) + MENUS_ROOM + FIELD_FLOOR + raised;
		let width = Number.POSITIVE_INFINITY;
		/* What a screen's menus need beyond the sum; never lowered in a visit, or they bounce. */
		let raised = 0;
		/* The same need as a whole width, kept for the screens met after this one. */
		let room = 0;
		const decide = () => {
			const onPhone = phone?.matches ?? false;
			const most = widest();
			const full = Math.max(end, most.end);
			// The menus leave last, after the tile size and the paste half.
			const need = Math.max(menusBeside(full - sizeRoom - pasteRoom), room, most.room);
			this.roomOnTopBar = onPhone || width >= need;
			this.sizeOnBar = onPhone || width >= need + 2 * (sizeRoom + pasteRoom);
			this.pasteOnBar = onPhone || width >= need + 2 * pasteRoom;
			const gone = (this.sizeOnBar ? 0 : sizeRoom) + (this.pasteOnBar ? 0 : pasteRoom);
			this.barEnd = Math.max(drawn, full - gone);
		};
		const observer = new ResizeObserver((entries) => {
			for (const entry of entries) {
				if (entry.target === field || ends.includes(entry.target as HTMLElement)) continue;
				width = entry.contentBoxSize?.[0]?.inlineSize ?? entry.contentRect.width;
			}
			drawn = endWidth();
			const standing = size?.getBoundingClientRect().width ?? 0;
			const holder = ends.find((one) => size !== null && one.contains(size));
			if (standing > 0 && holder !== undefined)
				sizeRoom = standing + (holder.childElementCount > 1 ? gapOf(holder) : 0);
			const pasting = pasteWidth();
			if (pasting > 0) pasteRoom = pasting;
			end = drawn + (standing > 0 ? 0 : sizeRoom) + (pasting > 0 ? 0 : pasteRoom);
			if (field !== null && this.roomOnTopBar && !(phone?.matches ?? false)) {
				const has = field.getBoundingClientRect().width;
				if (has > 0 && has < FIELD_FLOOR && Number.isFinite(width)) {
					const asDrawn = menusBeside(this.barEnd) - raised;
					raised = Math.max(raised, Math.ceil(width + FIELD_FLOOR - has) - asDrawn);
				}
			}
			room = Math.max(room, menusBeside(end - sizeRoom - pasteRoom));
			if (this.#screen !== null) keepNeed(this.#screen, { room, end });
			decide();
		});
		/* A new screen starts from its own last need, or from the sum: never from another's. */
		this.#screenChanged = () => {
			const known = this.#screen === null ? undefined : needs.get(this.#screen);
			({ room, end } = known ?? { room: 0, end });
			raised = 0;
			decide();
		};
		observer.observe(bar);
		// A screen's controls change the field and the ends without the bar changing size.
		if (field !== null) observer.observe(field);
		for (const one of ends) observer.observe(one);
		phone?.addEventListener('change', decide);
		return () => {
			observer.disconnect();
			this.#screenChanged = null;
			phone?.removeEventListener('change', decide);
		};
	}

	/** Told when a different screen takes the bar. Set by `watchRoom`, which owns the reading. */
	#screenChanged: (() => void) | null = null;

	#screen: string | null = null;

	get tools(): ScreenTools {
		return this.#offered;
	}

	#offered = $derived(
		this.sizeOnBar || this.sizeHome === null || stage.filling
			? this.#tools
			: { ...this.#tools, panels: [...(this.#tools.panels ?? []), this.sizeHome] }
	);

	/** Say what this screen can do. Called by the screen, with a token of its own. */
	publish(owner: symbol, tools: ScreenTools): void {
		const another = this.#owner !== owner;
		this.#owner = owner;
		this.#tools = tools;
		if (!another) return;
		this.#screen = untrack(() => page.route?.id ?? null);
		this.#screenChanged?.();
	}

	// Opened by pointing: it shuts when the pointer wanders off; a pressed one stays.
	#byHover = false;

	// Grace for the pointer to cross from the trigger to the panel.
	#leaving: ReturnType<typeof setTimeout> | null = null;

	/** Open or shut a panel by press; a press on a hover-opened one pins it. */
	toggle(id: string): void {
		this.#cancelLeaving();
		const pinning = this.open === id && this.#byHover;
		this.#byHover = false;
		if (pinning) return;
		this.open = this.open === id ? null : id;
	}

	/** Open this one, whatever was open before. What a control outside the bar calls. */
	show(id: string): void {
		this.#cancelLeaving();
		this.#byHover = false;
		this.open = id;
	}

	/** Open it because somebody is pointing at it. Shuts again when they point somewhere else. */
	showByHover(id: string): void {
		this.#cancelLeaving();
		this.#byHover = true;
		this.open = id;
	}

	/** The pointer is inside the trigger row or inside the panel. Either counts as still here. */
	stillHere(): void {
		this.#cancelLeaving();
		this.#inside = true;
	}

	// Held height: a reflow while editing must not leave a still pointer outside the panel.
	shapeHeld = $state(false);
	#drafting = false;
	#inside = false;

	/** Whether a kept filter's draft is open; called by the bar as the draft opens and ends. */
	drafting(open: boolean): void {
		this.#drafting = open;
		if (open) this.shapeHeld = true;
		// Ended under the pointer (Cancel, Save): held until it leaves; otherwise let go now.
		else if (!this.#inside) this.shapeHeld = false;
	}

	// A caret in the panel, not focus, since a ticked checkbox keeps focus.
	#someoneIsTyping(): boolean {
		if (typeof document === 'undefined') return false;
		const active = document.activeElement;
		if (!(active instanceof HTMLElement)) return false;
		if (!active.matches('input, textarea, [contenteditable="true"]')) return false;
		return active.closest('.bar-panel') !== null;
	}

	// A portalled dropdown is outside the panel in the DOM; nothing to subscribe to, so asked.
	#aLayerIsOpen(): boolean {
		if (typeof document === 'undefined') return false;
		return document.querySelector('[role="listbox"], [role="menu"], [role="dialog"]') !== null;
	}

	// Work with Save and Cancel (`data-unfinished`) vetoes only the hover close.
	#somethingIsUnfinished(): boolean {
		if (typeof document === 'undefined') return false;
		return document.querySelector('.bar-panel [data-unfinished]') !== null;
	}

	/** The pointer left. Shut, after the grace, and only if pointing is what opened it. */
	leaving(): void {
		/* A real leave lets a held shape go when no draft is open: nothing is under the pointer. */
		this.#inside = false;
		if (!this.#drafting) this.shapeHeld = false;
		if (!this.#byHover || this.open === null) return;
		this.#cancelLeaving();
		this.#leaving = setTimeout(() => {
			this.#leaving = null;
			if (!this.#byHover) return;
			// Still in a layer the panel opened; a menu is a layer itself and reports its pointer.
			if (!isAMenu(this.open, this.#tools) && this.#aLayerIsOpen()) return;
			if (this.#someoneIsTyping()) return;
			// Something in it is half done and has a Save waiting. Nor is that.
			if (this.#somethingIsUnfinished()) return;
			this.close();
		}, 220);
	}

	#cancelLeaving(): void {
		if (this.#leaving === null) return;
		clearTimeout(this.#leaving);
		this.#leaving = null;
	}

	close(): void {
		this.#cancelLeaving();
		this.#byHover = false;
		this.open = null;
		// A shut panel has no height to keep; an open draft holds the next one from the start.
		this.shapeHeld = this.#drafting;
	}

	/** Give the bar back, if it is still ours. A screen already replaced clears nothing. */
	release(owner: symbol): void {
		if (this.#owner !== owner) return;
		this.#owner = null;
		this.#screen = null;
		this.#tools = NOTHING;
		this.open = null;
	}

	// The screen's own search box (`WallControls`): it shares `q`, so the top box reads nothing.
	#ownBoxes = $state<symbol[]>([]);

	/** Whether the screen underneath draws a search box of its own. See above. */
	get ownBox(): boolean {
		return this.#ownBoxes.length > 0;
	}

	/** Say a box of the screen's own is drawn. Hands back the call that takes it away again. */
	claimOwnBox(token: symbol): () => void {
		/* Untracked: the box claims from an effect that would otherwise re-run on its own write. */
		untrack(() => (this.#ownBoxes = [...this.#ownBoxes, token]));
		return () => {
			untrack(() => (this.#ownBoxes = this.#ownBoxes.filter((one) => one !== token)));
		};
	}
}

export const screenBar = new ScreenBar();
