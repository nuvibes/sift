/* Sending somebody to ONE setting, not to the screen it is on. */
import { requestsInFlight } from '$lib/api/client';
import { motion } from '$lib/shell/motion.svelte';
import type { SettingEntry } from '$lib/settings-ui/settings';
import { toasts } from '$lib/shell/toasts.svelte';
import { place, type ToastWords } from '$lib/components/common/toast-pieces';
import { byName, drilldown } from '$lib/settings-ui/drilldown.svelte';

/** How long the ring stays before it fades. The fade itself is the stylesheet's. */
export const RING_MS = 2000;

/** How often to look. One `getElementById` against a document that does not contain it. */
const LOOK_EVERY_MS = 50;

/** A frame, for a fold that was just opened to be drawn before the row in it is scrolled to. */
const DRAW_MS = 16;

/* How long a pane that has FINISHED LOADING may go on not drawing the row before the hunt ends. */
const SETTLED_MS = 2000;

/* The backstop, for a pane that never stops loading. */
const CEILING_MS = 60_000;

/* How long a rung row is kept in view while the pane goes on filling in around it. */
const FOLLOW_MS = 15_000;

/** What the person does that means they have taken over, so the row stops being followed. */
const TAKING_OVER = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

/** What a hunt that found nothing says. Not silence: a link followed in good faith has to answer. */
export const ROW_NOT_FOUND = "That setting isn't on this page. It may have moved or been removed.";

/** Why a pane's way out is not drawn: it is drawn only once there is something for it to delete. */
export const NOTHING_TO_DELETE = "Nothing has been read yet, so there's nothing to delete.";

/** Why a setting's row is not drawn this moment, from the pane that would draw it. */
interface NotDrawn {
	because: ToastWords;
	near?: string;
}

/** What each mounted pane can say about the rows it is not drawing. See the head of this file. */
const explainers = new Set<(key: string) => NotDrawn | null>();

/** Explain the rows this pane leaves out, for as long as it is mounted. */
export function explainAbsentRows(explain: (key: string) => NotDrawn | null): () => void {
	explainers.add(explain);
	return () => {
		explainers.delete(explain);
	};
}

/** The one sentence for a row a choice hides: the row, the choice, and the answer it holds now. */
export function hiddenWhile(row: string, choice: string, answer: string): string {
	return `\u201c${row}\u201d is hidden while \u201c${choice}\u201d is set to \u201c${answer}\u201d.`;
}

/** A row drawn on another screen and filed under this pane's section: the sentence ends in that
 * screen's name as a link ("Which copy to keep" is set on Near duplicates). */
export function drawnOn(row: string, screen: { href: string; label: string }): NotDrawn {
	return { because: [`\u201c${row}\u201d is set on `, place(screen.label, screen.href)] };
}

/** `hiddenWhile` read off the registry: the row a choice hides, the choice, and the answer it
 * holds now in the choice's own words. */
export function hiddenBy(
	row: SettingEntry | undefined,
	choice: SettingEntry | undefined,
	value: unknown
): string | null {
	if (!row?.label || !choice?.label) return null;
	const at = (choice.choices ?? []).indexOf(value);
	const answer = choice.choice_labels?.[at] ?? String(value);
	return hiddenWhile(row.label, choice.label, answer);
}

function whyNotDrawn(key: string): NotDrawn | null {
	for (const explain of explainers) {
		const why = explain(key);
		if (why) return why;
	}
	return null;
}

/** The settings pane on screen, if one is. `SettingsPane` draws its body under this class. */
function paneOnScreen(): Element | null {
	return document.querySelector('.section-body');
}

/** The attribute the stylesheet keys on. Set here, removed here, read nowhere else. */
const FOUND = 'siftFound';

let clearRing: ReturnType<typeof setTimeout> | undefined;
let hunting: ReturnType<typeof setInterval> | undefined;
/** Ends the following of a row already rung. Held so a newer link can end it. */
let letGo: (() => void) | undefined;
/** Ends the hunt in flight, answering its promise. Held so a newer link can end an older one. */
let abandon: (() => void) | undefined;

/** Stop whatever a previous call was doing. Two links pressed quickly must not leave two rings. */
function callOff(): void {
	if (clearRing !== undefined) clearTimeout(clearRing);
	if (hunting !== undefined) clearInterval(hunting);
	clearRing = undefined;
	hunting = undefined;
	abandon?.();
	abandon = undefined;
	letGo?.();
	letGo = undefined;
	if (typeof document === 'undefined') return;
	for (const lit of document.querySelectorAll<HTMLElement>('[data-sift-found]')) {
		delete lit.dataset[FOUND];
	}
}

/** Take away whatever is HIDING a row that is in the document. */
function unhide(row: HTMLElement): boolean {
	let changed = false;
	for (let at = row.parentElement; at; at = at.parentElement) {
		if (at instanceof HTMLDetailsElement && !at.open) {
			at.open = true;
			changed = true;
		}
	}
	if (row.closest('.section-body.away')) {
		drilldown.close();
		changed = true;
	}
	return changed;
}

/** Where a row's choice is made inside a menu: the part of the row holding the menu's door. */
const DOOR = '[data-setting-door] [aria-haspopup="menu"]';

/** Set while the hunt itself presses a door, so the press is not read as the person taking over. */
let pressingDoor = false;

/** Open the menu a row's choice is made in, where the row has one. */
function openDoor(row: HTMLElement): void {
	const door = row.querySelector<HTMLButtonElement>(DOOR);
	if (door === null || door.disabled || door.getAttribute('aria-expanded') === 'true') return;
	pressingDoor = true;
	try {
		door.dispatchEvent(
			new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
		);
	} finally {
		pressingDoor = false;
	}
}

function show(row: HTMLElement): void {
	/* `center`, not `start`: a row put at the very top of a scrolling pane sits under whatever
	   is pinned above it, and the reader is looking at the row below the one they were sent to. */
	row.scrollIntoView({ behavior: motion.reduced ? 'auto' : 'smooth', block: 'center' });
	row.dataset[FOUND] = '';
	/* A row shown again (see `follow`) rings for its full time from the place it ends up. */
	if (clearRing !== undefined) clearTimeout(clearRing);
	clearRing = setTimeout(() => {
		delete row.dataset[FOUND];
		clearRing = undefined;
	}, RING_MS);
}

/** The element that scrolls this row: the nearest ancestor that scrolls, else the document. */
function scrollerOf(row: HTMLElement): Element {
	for (let at = row.parentElement; at; at = at.parentElement) {
		if (/(auto|scroll)/.test(getComputedStyle(at).overflowY)) return at;
	}
	return document.scrollingElement ?? document.documentElement;
}

/** Where the row sits in what its scroller scrolls: moved only by what is drawn above it, never
 *  by scrolling, which is how a smooth scroll in progress is told apart from a pane that grew. */
function placeOf(row: HTMLElement, scroller: Element): number {
	const edge = scroller === document.scrollingElement ? 0 : scroller.getBoundingClientRect().top;
	return row.getBoundingClientRect().top - edge + scroller.scrollTop;
}

/** Ring the row, then keep it in view while the pane goes on loading around it. See `FOLLOW_MS`. */
function point(row: HTMLElement): void {
	show(row);
	follow(row);
	openDoor(row);
}

function follow(row: HTMLElement): void {
	const scroller = scrollerOf(row);
	const pane = paneOnScreen();
	let place = placeOf(row, scroller);
	let drawn = 0;
	let seen = 0;
	const watcher = new MutationObserver(() => (drawn += 1));
	if (pane !== null) watcher.observe(pane, { childList: true, subtree: true });
	const began = Date.now();
	let quietSince = began;
	const stop = () => {
		watcher.disconnect();
		clearInterval(ticking);
		for (const kind of TAKING_OVER) window.removeEventListener(kind, takenOver, true);
		if (letGo === stop) letGo = undefined;
	};
	/* The hunt's own press on a row's door is not the person taking over. */
	const takenOver = () => {
		if (!pressingDoor) stop();
	};
	const ticking = setInterval(() => {
		const now = Date.now();
		if (!row.isConnected || paneOnScreen() === null || now - began >= FOLLOW_MS) return stop();
		const moved = placeOf(row, scroller);
		if (moved !== place) {
			place = moved;
			show(row);
		}
		const busy = requestsInFlight() || drawn !== seen;
		seen = drawn;
		if (busy) quietSince = now;
		else if (now - quietSince >= SETTLED_MS) stop();
	}, LOOK_EVERY_MS);
	for (const kind of TAKING_OVER) window.addEventListener(kind, takenOver, true);
	letGo = stop;
}

/** The row a key names, on the section being opened. */
function rowOn(key: string, section: string | undefined): HTMLElement | null {
	const fits = (row: HTMLElement): boolean => {
		const pane = row.closest<HTMLElement>('.section-body');
		return section === undefined || !pane?.dataset.section || pane.dataset.section === section;
	};
	const first = document.getElementById(key);
	if (first === null || fits(first)) return first;
	// The first is on another pane: a second row with the same id, if one is drawn yet.
	for (const row of document.querySelectorAll<HTMLElement>('[id]')) {
		if (row.id === key && row !== first && fits(row)) return row;
	}
	return null;
}

/** Find the row this key names, open whatever hides it, and point at it. */
export function revealSetting(key: string, section?: string): Promise<boolean> {
	if (typeof document === 'undefined') return Promise.resolve(false);
	callOff();

	return new Promise<boolean>((resolve) => {
		/* Counts what the PANE draws, once there is a pane: a pane still filling itself is busy
		   even between requests, because a second request often starts from the first one's
		   answer. */
		let drawn = 0;
		let watching: Element | null = null;
		const watcher = new MutationObserver(() => (drawn += 1));

		let answered = false;
		const answer = (found: boolean) => {
			if (answered) return;
			answered = true;
			watcher.disconnect();
			if (hunting !== undefined) clearInterval(hunting);
			hunting = undefined;
			abandon = undefined;
			resolve(found);
		};
		abandon = () => answer(false);

		/* Ask whatever owns the key on EVERY look, not once before the first. */
		/* The row being looked for: the key's own, or, once its pane has said why that one is
		   not drawn, the row that decides it, looked for the same way (it may be on a sub-page
		   of its own), with the pane's sentence said once it is rung. */
		let target = key;
		let said: NotDrawn | null = null;

		const look = (): boolean => {
			drilldown.reveal(target, section);
			const row = rowOn(target, section);
			if (!row) return said === null && explained();
			ring(row);
			if (said !== null) toasts.show(said.because);
			answer(true);
			return true;
		};

		const ring = (row: HTMLElement) => {
			if (unhide(row)) {
				/* Opened or closed something: let it draw before measuring where the row is. */
				/* Held where the ring's timer is held, so a newer link calls this off as well. */
				clearRing = setTimeout(() => point(row), DRAW_MS);
			} else {
				point(row);
			}
		};

		/* A row the pane is deliberately not drawing: ring what decides it and say why. */
		const explained = (): boolean => {
			if (paneOnScreen() === null || requestsInFlight()) return false;
			const why = whyNotDrawn(key);
			if (why === null) return false;
			if (why.near === undefined) {
				toasts.show(why.because);
				answer(false);
				return true;
			}
			/* The row that decides it is hunted for as the key's own was: opened from behind a
			   sub-page or a fold, waited for while it draws, and the sentence said when it is rung. */
			target = why.near;
			said = why;
			return look();
		};

		if (look()) return;

		const began = Date.now();
		let idleSince: number | null = null;
		let lastDrawn = drawn;
		hunting = setInterval(() => {
			if (look()) return;
			const now = Date.now();
			const pane = paneOnScreen();
			if (pane === null && watching !== null) {
				/* Settings was closed under the hunt. Nobody is looking for the row any more, and a
				   sentence about it arriving over the screen they went back to would be noise. */
				answer(false);
				return;
			}
			if (pane !== null && pane !== watching) {
				watcher.disconnect();
				watcher.observe(pane, { childList: true, subtree: true });
				watching = pane;
				drawn += 1;
			}
			const changed = drawn !== lastDrawn;
			lastDrawn = drawn;
			/* Not mounted yet counts as loading: the pane is on its way, not finished without it. */
			const loading = pane === null || requestsInFlight() || changed;
			idleSince = loading ? null : (idleSince ?? now);
			const settled = idleSince !== null && now - idleSince >= SETTLED_MS;
			if (settled || now - began >= CEILING_MS) {
				answer(false);
				/* A pane that said why the row is not drawn has accounted for it, even where the
				   row that decides it never came: its sentence, not "not on this page". */
				if (said !== null) toasts.show(said.because);
				else if (pane !== null) toasts.show(ROW_NOT_FOUND);
			}
		}, LOOK_EVERY_MS);
	});
}

/** What a name is compared as: its words in lower case, so "Sign-in" finds "Sign in". */
function plain(text: string | null | undefined): string {
	return (text ?? '')
		.toLowerCase()
		.replace(/[^\p{L}\p{N}]+/gu, ' ')
		.trim();
}

/* Where a pane writes a name somebody can be sent to, the most telling first: the heading over a
 * group, then the name of a fold, a card or a row. */
const NAMED = ['h2, h3', 'summary, legend, .name'] as const;

/** What is rung for a name: the heading's block or the whole row, not the bare words. */
const HOLDS_A_NAME = '.section-heading, .row, summary';

/** The place on the section being opened that is called this: a heading first, then a row or
 * fold. */
function namedOn(name: string, section: string | undefined): HTMLElement | null {
	const wanted = plain(name);
	if (wanted === '') return null;
	const pages = document.querySelectorAll<HTMLElement>('.sub-page, .section-body');
	for (const names of NAMED) {
		for (const page of pages) {
			const at = page.dataset.section;
			if (section !== undefined && at && at !== section) continue;
			for (const one of page.querySelectorAll<HTMLElement>(names)) {
				if (plain(one.textContent) === wanted) {
					return one.closest<HTMLElement>(HOLDS_A_NAME) ?? one;
				}
			}
		}
	}
	return null;
}

/** Point at a thing a pane draws that has a name and no key: the heading over a group, a card, a
 * row drawn by hand. */
export function revealNamed(name: string, section?: string): Promise<boolean> {
	if (typeof document === 'undefined') return Promise.resolve(false);
	callOff();

	return new Promise<boolean>((resolve) => {
		let answered = false;
		const answer = (found: boolean) => {
			if (answered) return;
			answered = true;
			if (hunting !== undefined) clearInterval(hunting);
			hunting = undefined;
			abandon = undefined;
			resolve(found);
		};
		abandon = () => answer(false);

		const look = (): boolean => {
			drilldown.reveal(byName(name));
			const place = namedOn(name, section);
			if (!place) return false;
			if (unhide(place)) clearRing = setTimeout(() => point(place), DRAW_MS);
			else point(place);
			answer(true);
			return true;
		};

		if (look()) return;

		const began = Date.now();
		let idleSince: number | null = null;
		let mounted = false;
		hunting = setInterval(() => {
			if (look()) return;
			const now = Date.now();
			const pane = paneOnScreen();
			if (pane === null && mounted) return answer(false);
			mounted = mounted || pane !== null;
			const loading = pane === null || requestsInFlight();
			idleSince = loading ? null : (idleSince ?? now);
			const settled = idleSince !== null && now - idleSince >= SETTLED_MS;
			if (settled || now - began >= CEILING_MS) answer(false);
		}, LOOK_EVERY_MS);
	});
}

/* There is deliberately no exported `stopRevealing`: nothing would call it. */
