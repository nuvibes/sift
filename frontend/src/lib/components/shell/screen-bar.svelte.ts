/*
 * What the bar across the top is holding on behalf of the screen underneath it.
 *
 * The bar is drawn once by the layout, and only the screen knows what it can do, so the screen
 * publishes it here. Navigating mounts the new screen before unmounting the old, so a publisher
 * takes a token (a symbol) and only its holder can clear it, as `searchBox.claim` does. The query
 * itself stays in the address, so a filtered view is a link; a screen publishes only what it IS.
 */

import type { Attachment } from 'svelte/attachments';
import type { Component, Snippet } from 'svelte';
import { untrack } from 'svelte';
import type { IconName } from '$lib/design/icons';
import type { Subject } from './facet-labels';
import type { Verb } from '$lib/components/common/verbs';
import type { NarrowedToUsername } from '$lib/grid/grid.svelte';
import { PHONE_WIDTH } from '$lib/components/common/phone-width.svelte';

/** One entry in an order dropdown: the value the screen sorts by, and what to call it. */
export interface SortChoice {
	value: string;
	label: string;
	/**
	 * A row that is a PRESS rather than an order (`Shuffle again`): drawn as a row, never the
	 * value, so it can be pressed again. Carried here too, so the flag survives the trip to the bar
	 * (`action` in `common/Select.svelte`).
	 */
	action?: boolean;
	/** Drawn dimmed and never chosen: an order this wall cannot be put in now. `detail` says why. */
	disabled?: boolean;
	/** A second line under the name: what the order is measured against, or why it is dimmed. */
	note?: string;
}

/**
 * What the Filter control is filtering, when it is not the address bar.
 *
 * Nearly always the address, so a filtered view is a link. The other targets are a saved filter's
 * draft (nothing moves until Save) and a Theater cell (a wall has several sources), and this shape
 * lets the whole Filter panel serve them unchanged. `URLSearchParams`, since the query language
 * reads and writes `q` and a key per facet.
 */
export interface Narrowing {
	read: () => URLSearchParams;
	write: (next: URLSearchParams) => void;
	/** What a control that WILL filter the target spreads, so pointing at it says which target that
	 *  is. Theater's lights the cells `write` lands on; see `$lib/theater/narrowing`. */
	pointing?: Pointing;
	/** What the target filters by on its own control, which the panel never writes and counts
	 *  every column within, that one's own included: a theater cell's media kinds. */
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

/*
 * What one control on the bar can do on the screen underneath it.
 *
 * `true` means it acts; a STRING means it does not, and says why when pointed at. The bar's SHAPE
 * is the same on every screen and only a control's STATE changes, because a control that vanishes
 * is one people stop reaching for. Dimmed, not blurred, which reads as loading. One field, never a
 * flag beside a reason that could disagree with it.
 */
export type Capability = true | string;

/**
 * The orders a screen is actually offering, out of what it published: one reader for the union, so
 * no two call sites unpack it differently.
 */
export function ordersOffered(sorts: readonly SortChoice[] | string | undefined): SortChoice[] {
	// Copied, because the chooser takes a mutable list and a screen's table is `as const`.
	return Array.isArray(sorts) ? [...sorts] : [];
}

/** Whether a control may act, given what its screen said. Anything but `true` is a no. */
export function ableTo(capability: Capability | undefined): boolean {
	return capability === true;
}

/*
 * What to say when a screen said nothing at all (Settings, Downloads, a queue, a login): the bar is
 * still above them, and its controls still answer when pointed at.
 */
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

/*
 * A panel the bar drops open under itself: the ONE way a panel opens from this bar. It takes no
 * props and reads the stores its screen writes, so the bar never knows what a panel is about.
 */
interface BarPanel {
	/** Stable, and unique on this screen. It is what the bar remembers as open. */
	id: string;
	icon: IconName;
	/** The tooltip, and what a screen reader is told the button opens. */
	label: string;
	content: Component<Record<string, never>>;
	/*
	 * Whether this belongs at the HEAD of the row rather than at its tail: the head is where Filter
	 * and Order decide what the screen shows, so a panel answering that sits there even on a screen
	 * with no query language.
	 */
	lead?: boolean;
	/**
	 * Draw the TRIGGER on the top bar instead of on this row; the panel opens where it always does.
	 * For a panel about the whole application, such as Theater's key sheet.
	 */
	atTheTop?: boolean;
}

/** A menu of this screen's own on the bar's row: a list, one of which is current. See `menus`. */
interface BarMenu {
	/** Stable, and unique on this screen. It is what the bar remembers as open. */
	id: string;
	icon: IconName;
	/** The tooltip, and what a screen reader is told the control is. */
	label: string;
	options: SortChoice[];
	value: string;
	onChoose: (next: string) => void;
	/**
	 * A picture for each option, where the words are not the answer (Theater's layouts), drawn by
	 * the shared chooser (`Select.preview`). Absent means a list of words.
	 */
	preview?: Snippet<[SortChoice]>;
}

/** The built-in panel: the facets, which belong to the query language rather than to any screen. */
export const FILTERS_PANEL = 'filters';

/*
 * The order: a MENU, but one of the row's, because the row holds one open thing at a time. Its id
 * lives in this store for that alone; `FilterBar` registers no panel for it.
 */
export const SORT_MENU = 'sort';

/*
 * The sheet a phone opens for BOTH of them, Filter and Sort, from one control, since a thumb needs
 * one target for one errand. Its own id, because a screen may order without filtering and the facet
 * panel's id is refused there (`showing` in `FilterBar`).
 */
export const PHONE_SHEET = 'filter-and-sort';

/*
 * Whether what is open is a MENU rather than a panel: a menu is a floating layer reporting its own
 * pointer, a panel is reported by the drawer. Module-private (`public-surface.test.ts`).
 */
function isAMenu(id: string | null, tools: ScreenTools): boolean {
	if (id === null) return false;
	if (id === SORT_MENU) return true;
	// A screen's own menu is the same control as the order, read from the published list.
	return (tools.menus ?? []).some((one) => one.id === id);
}

/*
 * No sort panel: a list you pick one from is a menu, and only a workspace you watch the wall
 * through earns a panel. No saved panel either: kept filters are drawn at the foot of the filter
 * panel (`SavedFilters`), where the decision is being made.
 */

/** Everything the bar can draw for a screen. Every part is optional; a screen fills what it has. */
interface ScreenTools {
	/*
	 * What this screen IS, as filters, and cannot stop being (a person's page is `people:<them>`):
	 * drawn as a chip that cannot be taken off, since that would navigate off the screen.
	 */
	query?: Record<string, string>;

	/*
	 * How many things the screen is showing, as its header counts them; absent while counting. The
	 * Added span column says it in place of values.
	 */
	count?: number;

	/*
	 * The username `?username=` filters this wall to, as the server named it, or null: a username has
	 * no page, and an id cannot be read on a chip.
	 */
	username?: NarrowedToUsername | null;

	/*
	 * Whether the query language applies here: true on every wall, a reason on a screen that is not
	 * one (the button is still drawn, dimmed; see `Capability`).
	 */
	filterable?: Capability;

	/*
	 * WHICH NOUN THIS WALL IS SHOWING, which decides the facet panel's columns, the same wherever
	 * that noun is listed. Absent means files.
	 */
	subject?: Subject;

	/*
	 * THE VERBS A FILTER'S CHIP HAS, for a chip that names a thing with verbs of its own: a chip
	 * for one artist on the Music wall renames that artist. Handed the column, the value and the
	 * words the chip shows; answers the declared verbs, drawn as the chip's right-click menu, or an
	 * empty list for a chip that is only a filter. Absent everywhere a chip is only a filter.
	 */
	chipVerbs?: (field: string, value: string, label: string) => readonly Verb[];

	/*
	 * The facets every row on this wall has ONE value of, so the panel leaves them out: a column of
	 * one row says only what the wall already is. The Loops wall is the files with a Loop, and
	 * Favorites the files hearted, so neither draws that column. Absent everywhere else, which is
	 * every facet offered: Browse and Theater leave out nothing.
	 */
	fixed?: readonly string[];

	/*
	 * The address's name for the words this wall's own box searches by, where it is not `q` (see
	 * `wall-words`). The bar draws them as a words chip whose cross takes them off. Absent, the
	 * words are the typed part of `q`.
	 */
	words?: string;

	/*
	 * What Filter filters here, when the address is the wrong answer (`Narrowing`). Absent on every
	 * screen whose filter is a page of files at a URL.
	 */
	narrowing?: Narrowing;

	/*
	 * A control the screen puts at the HEAD of the filter panel, such as which Theater cell is
	 * being edited. Drawn by the screen, as `extra` is, so the bar never knows what a cell is.
	 */
	narrowingLead?: Snippet;

	/*
	 * What the thing being filtered is CALLED, drawn at the head of the chips row, since on a wall
	 * the chips do not say which source they are on. Grey like a kept filter's name, because the
	 * accent means a filter in force.
	 */
	narrowingName?: string;

	/*
	 * Menus of this screen's own, drawn beside Filter and Order on the same row with the app's own
	 * chooser: a list you pick one of, such as Theater's Layout.
	 */
	menus?: BarMenu[];

	/*
	 * The orders this screen offers, and the one it is in. Empty dims the control. A STRING is the
	 * sentence saying why there are none (`Capability` applied to a list), as Theater's "choose a
	 * cell". One field, so nothing comes apart.
	 */
	sorts?: readonly SortChoice[] | string;
	sort?: string;
	onSort?: (next: string) => void;

	/* Whether the tile-size slider has anything to resize: drawn everywhere, dimmed where not. */
	resizable?: Capability;

	/*
	 * Whether the tiles here can PLAY, a separate question from resizing: a wall of cards resizes
	 * and has nothing to play.
	 */
	playable?: Capability;

	/*
	 * Whether what is on this screen is currently moving, and what pressing the control does: the
	 * hover previews on a wall of files, the feeds on Theater. The screen answers, as for `sorts`.
	 */
	playing?: boolean;
	onPlay?: () => void;
	/** What the control is called on this screen, both as a tooltip and to a screen reader. */
	playLabel?: string;
	/** The key this screen answers the control with, read from the act table (`keyOf`), if any. */
	playShortcut?: string;

	/** Anything this screen has that nothing else does, drawn at the end of the bar. */
	extra?: Snippet;

	/**
	 * The same, but drawn on the TOP bar between the play control and the vault's: for a control
	 * about the whole window, such as the wall's silence.
	 */
	topExtra?: Snippet;

	/**
	 * Drawn on this row immediately after the chip naming what the row is filtering: Theater's
	 * play-everything and silence-everything, reachable while the top bar is hidden.
	 */
	besideTheName?: Snippet;

	/**
	 * Fade this row out, because the screen under it says so: Theater's wall answers the window's
	 * edges, and the row must go with its own bar rather than lie across the pictures.
	 */
	quiet?: boolean;

	/*
	 * The panels this screen can drop open, each drawn as a button on the bar. Where they drop
	 * (under the bar, or up from it while the window is filled) is the stage's business.
	 */
	panels?: BarPanel[];
}

const NOTHING: ScreenTools = {};

/*
 * The room the named menus need on the top bar, measured across the bar's own content box.
 *
 * Not a media query, because the rail and the desktop caption buttons take slices the window's
 * width does not show. 723 is the menus' end, the actions' end (the larger, admin's) and the two
 * column gaps beside the search field's floor; `e2e/top-bar.spec.ts` pins it at seven widths.
 * Below it the menus move to the screen's own row and the field keeps its floor down to the
 * window's minimum; the field also drops its keyboard hint below its floor (`SearchBox`).
 */
const ROOM_FOR_THE_MENUS = 723;

/** The search field's floor: at 228px it holds one row, at 226 it wraps. See the sum above. */
const FIELD_FLOOR = 227;

/** How the bar finds the search field inside itself, to read what it was left. */
const SEARCH_FIELD = 'form.search';

/** How the bar finds the room it holds for the screen's trail (`TopBar`'s `.trail`). */
const TRAIL_ROOM = '.trail';

/** The least room a trail keeps: the fold's press and the start of where you are (`--trail-least`). */
const TRAIL_LEAST = 64;

/*
 * Under the phone's width (`PHONE_WIDTH`) the bar is a row of squares (the search, Filter and Sort
 * together, Hidden, Add) and the sum above does not apply.
 */

class ScreenBar {
	#tools = $state<ScreenTools>(NOTHING);
	/** Who filled it. Only they may empty it. See the ordering note above. */
	#owner: symbol | null = null;

	/*
	 * Which panel is open, by id, or none. Here so a screen can open its own (a cell's filter button
	 * opens the bar's panel). Not reset by `publish`, which runs from an effect; cleared when the bar
	 * is given back.
	 */
	open = $state<string | null>(null);

	/*
	 * The kept filter the screen under the bar IS, by id, or none: worked out once by the bar
	 * (`appliedKept`) so a sitting can name it without a second comparison.
	 */
	keptInForce = $state<string | null>(null);

	/*
	 * Whether the named menus fit on the top bar. Only one of the two homes may draw them, since a
	 * copy hidden by a stylesheet stays in the tab order and the accessibility tree.
	 */
	roomOnTopBar = $state(true);

	/*
	 * How much of the trail's room this screen takes back for its own controls, in pixels. A screen
	 * with extra controls (Theater) gives up trail room first, down to its least, before the menus
	 * leave the bar, so its title is not a row lower than other screens'. `TopBar` subtracts it.
	 */
	trailGives = $state(0);

	/* The trail's room given up whole: the menus have left and the field is still short. */
	trailBare = $state(false);

	/**
	 * Watch the top bar and answer `roomOnTopBar` from what it actually has: its content box, since
	 * padding is what the caption buttons take. Hands back the teardown.
	 */
	watchRoom(bar: HTMLElement): () => void {
		// jsdom has no `ResizeObserver`.
		if (typeof ResizeObserver === 'undefined') return () => {};
		const phone = typeof matchMedia === 'function' ? matchMedia(PHONE_WIDTH) : null;
		const field = bar.querySelector<HTMLElement>(SEARCH_FIELD);
		const trail = bar.querySelector<HTMLElement>(TRAIL_ROOM);
		let width = Number.POSITIVE_INFINITY;
		/*
		 * The width this screen's menus need: the sum, RAISED when the field is seen below its floor
		 * with the menus up (by what it is short, since the field is the one track that gives way).
		 * Never lowered until the screen changes, or the menus would bounce up and down a frame apart.
		 */
		let needed = ROOM_FOR_THE_MENUS;
		const decide = () => {
			this.roomOnTopBar = (phone?.matches ?? false) || width >= needed;
		};
		const observer = new ResizeObserver((entries) => {
			for (const entry of entries) {
				if (field !== null && entry.target === field) continue;
				width = entry.contentBoxSize?.[0]?.inlineSize ?? entry.contentRect.width;
			}
			if (field !== null && this.roomOnTopBar && !(phone?.matches ?? false)) {
				const has = field.getBoundingClientRect().width;
				if (has > 0 && has < FIELD_FLOOR && Number.isFinite(width)) {
					let short = Math.ceil(FIELD_FLOOR - has);
					/* The trail's room first, down to its least; the field takes what it gives up. */
					const room = trail?.getBoundingClientRect().width ?? 0;
					const given = Math.min(short, Math.max(0, Math.floor(room - TRAIL_LEAST)));
					if (given > 0) {
						this.trailGives += given;
						short -= given;
					}
					if (short > 0) needed = Math.max(needed, Math.ceil(width + short));
				}
			} else if (field !== null && !(phone?.matches ?? false)) {
				const has = field.getBoundingClientRect().width;
				if (has > 0 && has < FIELD_FLOOR) this.trailBare = true;
			}
			decide();
		});
		/* A new screen starts from the sum again: what the last one needed says nothing about it. */
		this.#screenChanged = () => {
			needed = ROOM_FOR_THE_MENUS;
			this.trailGives = 0;
			this.trailBare = false;
			decide();
		};
		observer.observe(bar);
		// The field too: extra controls squeeze it without the bar changing size.
		if (field !== null) observer.observe(field);
		phone?.addEventListener('change', decide);
		return () => {
			observer.disconnect();
			this.#screenChanged = null;
			phone?.removeEventListener('change', decide);
		};
	}

	/** Told when a different screen takes the bar. Set by `watchRoom`, which owns the reading. */
	#screenChanged: (() => void) | null = null;

	get tools(): ScreenTools {
		return this.#tools;
	}

	/** Say what this screen can do. Called by the screen, with a token of its own. */
	publish(owner: symbol, tools: ScreenTools): void {
		const another = this.#owner !== owner;
		this.#owner = owner;
		this.#tools = tools;
		if (another) this.#screenChanged?.();
	}

	/*
	 * Whether what is open was opened by POINTING at it rather than by pressing it: a hovered menu
	 * falls shut when the pointer wanders off, a pressed one stays while somebody works inside it.
	 */
	#byHover = false;

	/*
	 * The grace between leaving and shutting, so the pointer can cross from the trigger to the panel
	 * further down the page.
	 */
	#leaving: ReturnType<typeof setTimeout> | null = null;

	/*
	 * Open this panel, or shut it if it is the one already open. A PRESS, so it stays until pressed.
	 *
	 * A press on a menu open by HOVER pins it rather than shutting it: pressing what you point at is
	 * the natural follow-through, and a browser test presses before the page holds still, so the
	 * dwell would otherwise open it and the press shut it.
	 */
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

	/*
	 * WHETHER THE PANEL KEEPS ITS LAID-OUT HEIGHT, which is how a piece of work in it survives a
	 * change of shape under a still pointer.
	 *
	 * Editing a kept filter reflows the panel, and a shorter panel leaves the pointer below its edge
	 * without moving: `mouseleave` fires and the panel would shut on the press meant to keep working.
	 * So while a draft is open the panel may grow and never shrink (`FilterBar`), and when the draft
	 * ends under the pointer the height holds until the pointer genuinely leaves.
	 */
	shapeHeld = $state(false);
	#drafting = false;
	#inside = false;

	/** Whether a kept filter's draft is open in the panel. Called by the bar as the draft opens and ends. */
	drafting(open: boolean): void {
		this.#drafting = open;
		if (open) this.shapeHeld = true;
		// Ended under the pointer (Cancel, Save): held until it leaves; otherwise let go now.
		else if (!this.#inside) this.shapeHeld = false;
	}

	/*
	 * Whether somebody is typing in the panel, in which case they have not left it: a hover-opened
	 * panel must not take a half-typed name away, as a pressed one does not. A caret, not focus,
	 * since a ticked checkbox keeps focus. Asked of the document at the moment of shutting.
	 */
	#someoneIsTyping(): boolean {
		if (typeof document === 'undefined') return false;
		const active = document.activeElement;
		if (!(active instanceof HTMLElement)) return false;
		if (!active.matches('input, textarea, [contenteditable="true"]')) return false;
		return active.closest('.bar-panel') !== null;
	}

	/*
	 * Whether something inside the panel has opened a layer of its own: a dropdown is portalled to
	 * the end of the document, so pointing at its list leaves the panel as far as the DOM knows.
	 * Asked of the document at the moment of shutting; the library offers nothing to subscribe to.
	 */
	#aLayerIsOpen(): boolean {
		if (typeof document === 'undefined') return false;
		return document.querySelector('[role="listbox"], [role="menu"], [role="dialog"]') !== null;
	}

	/*
	 * Whether the open panel is holding a piece of work somebody has not finished.
	 *
	 * A panel with Save and Cancel is a mode a drifting pointer must not abandon. Declared by the work
	 * itself (`data-unfinished`) rather than guessed here. It vetoes the hover close only: Escape,
	 * the trigger and `close()` still shut it, since a mode you cannot leave would be worse.
	 */
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
			/* Still choosing from a layer the panel opened. Not asked of a menu, which is itself a
			   layer and would never shut; a menu reports its own pointer (`onListPointer`). */
			if (!isAMenu(this.open, this.#tools) && this.#aLayerIsOpen()) return;
			// Still typing into it. Nor is that.
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
		this.#tools = NOTHING;
		this.open = null;
	}

	/*
	 * THE SCREEN'S OWN SEARCH BOX, while one is drawn: the box above a wall (`WallControls`).
	 *
	 * A wall's box keeps its words under `q`, which the top box (the LIBRARY's search) also follows,
	 * so two boxes would show one set of letters for two questions. While a wall's box is here, the
	 * top box reads nothing from the address. Claimed by the box itself, the one thing every such
	 * wall draws; a token each, since the next wall's box mounts before the last one's goes.
	 */
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
