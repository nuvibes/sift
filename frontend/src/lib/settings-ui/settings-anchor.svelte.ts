/*
 * Sending somebody to ONE setting, not to the screen it is on.
 *
 * ## Why this exists
 *
 * A sentence of the form "that is switched off in Settings, under Performance" names a place and
 * leaves the reader to go and find it. On a pane with thirty rows on it, naming the pane is barely
 * more use than naming the application. So a setting has an address:
 * `/settings/playback#playback.resume_enabled` opens the pane, scrolls the row into view and rings
 * it.
 *
 * ## Why the ring, and why it fades
 *
 * Landing somewhere is not the same as being SHOWN something. A pane scrolled to an arbitrary
 * offset looks exactly like a pane somebody scrolled themselves, and the row that was the whole
 * point of the link is one of thirty identical rows. The ring answers "which one", and it fades
 * because after it has been read it is a permanent decoration on a row that is not special, and the
 * next thing the reader does is change it.
 *
 * ## Why it looks for the row rather than being told when it is drawn
 *
 * The pane the row is on is chosen by the address, mounted by the router, and then fills itself
 * from a request, so the element does not exist when the navigation finishes, and there is no one
 * event that means "it does now". Every pane would have to report readiness, which is a seam
 * through twenty files to solve a problem in one. So this looks, cheaply and often, and the promise
 * it returns is what a caller awaits.
 *
 * ## When it stops looking: WAITED FOR, not timed
 *
 * A fixed clock cannot know how long a pane takes: on a big library the Performance pane takes
 * longer than any number chosen on a small one, and every link into it would give up a second
 * before the row existed, silently. What it can know is whether the pane is still LOADING, and
 * not from the pane's own drawing: most panes draw nothing at all while their request is out rather
 * than a skeleton. So the clock that ends the hunt runs only while the pane is mounted, NO REQUEST
 * IS ON ITS WAY (`requestsInFlight`, counted where every request is made) and nothing inside the
 * pane has changed; only a pane that has finished and still has no such row is a row that is not
 * coming. A hard ceiling stays behind that for a pane that never settles, and neither end is
 * silent: a hunt that finds nothing says so.
 *
 * ## A setting can be one level down, or folded away
 *
 * A group of settings that answer one question draws one control on the pane and puts the rest on a
 * sub-page (see `PresetGroup`). A link naming one of them would hunt for a row that is real,
 * mounted and on a page nobody has opened. So the drill-in is ASKED on every look: whatever owns
 * the key (a sub-page, a fold that draws its rows only when open) opens itself through
 * `drilldown.own`, and the hunt then finds the row the ordinary way. A row inside a closed
 * `<details>` is in the document already and is simply opened; a row on the pane BEHIND an open
 * sub-page closes that sub-page first. Nothing here knows which keys those are, which is the point.
 *
 * ## A row that is not drawn at all, and why that is not "moved or removed"
 *
 * Some rows are real, registered and searchable, and are simply not drawn this moment: the folder a
 * saved screenshot goes to is drawn only while Save is the answer, a backup's How often only while
 * it runs on its own, the device rows on General only in the app, the player's volume nowhere but
 * on the player. A search or a pasted path landing on one would hunt, find nothing and say the
 * setting may have moved or been removed, which is false: it is exactly where it was said to be,
 * waiting on a choice beside it.
 *
 * So a pane that draws a row only under a condition EXPLAINS the row's absence here
 * (`explainAbsentRows`), and the hunt asks before it gives up: the row that decides is rung in the
 * missing row's place and the sentence says what hides it ("How often" is hidden while "Automatic
 * backup" is set to "Only when you run it"). The pane answers from what it has already loaded and
 * nothing while it is still loading, so a row that is merely late is still waited for. Only a row
 * nobody can account for says it is not on the page.
 */
import { requestsInFlight } from '$lib/api/client';
import { motion } from '$lib/shell/motion.svelte';
import type { SettingEntry } from '$lib/settings-ui/settings';
import { toasts } from '$lib/shell/toasts.svelte';
import { place, type ToastWords } from '$lib/components/common/toast-pieces';
import { byName, drilldown } from '$lib/settings-ui/drilldown.svelte';

/** How long the ring stays before it fades. The fade itself is the stylesheet's. Also how long
 *  the search box says where a pasted path went, so the two answers end together. */
export const RING_MS = 2000;

/** How often to look. One `getElementById` against a document that does not contain it. */
const LOOK_EVERY_MS = 50;

/** A frame, for a fold that was just opened to be drawn before the row in it is scrolled to. */
const DRAW_MS = 16;

/* How long a pane that has FINISHED LOADING may go on not drawing the row before the hunt ends.
 *
 * Counted only while nothing in the pane is loading, so it is not a guess at how long a fetch
 * takes. It is the time a drawn pane gets to draw one more thing after its data has arrived,
 * which is a render or two. Two seconds is generous for that and short enough that the sentence
 * below arrives while the person is still looking for the row. */
const SETTLED_MS = 2000;

/* The backstop, for a pane that never stops loading. Past this the request behind it has failed
 * or hung, which the pane says for itself; the hunt stops rather than looking for ever. */
const CEILING_MS = 60_000;

/* How long a rung row is kept in view while the pane goes on filling in around it.
 *
 * Finding the row is not the end of the pane loading. On Performance the "Is Sift keeping up?"
 * row is drawn immediately and the device, GPU and measurement blocks ABOVE it arrive afterwards,
 * so a row scrolled to and rung would be pushed two screens down by the blocks landing above it:
 * a ring nobody could see. So after the row is rung it is FOLLOWED: while the pane is still
 * loading, a row whose place in the pane moved is scrolled back into view and rung again. It ends
 * when the pane has been quiet for `SETTLED_MS`, when the person scrolls, types or clicks (their
 * own scrolling is never fought), when a newer link or closing settings calls it off, or at this
 * backstop for a pane that never goes quiet.
 */
const FOLLOW_MS = 15_000;

/** What the person does that means they have taken over, so the row stops being followed. */
const TAKING_OVER = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

/** What a hunt that found nothing says. Not silence: a link followed in good faith has to answer. */
export const ROW_NOT_FOUND = "That setting isn't on this page. It may have moved or been removed.";

/** Why a pane's way out is not drawn: it is drawn only once there is something for it to delete. */
export const NOTHING_TO_DELETE = "Nothing has been read yet, so there's nothing to delete.";

/**
 * Why a setting's row is not drawn this moment, from the pane that would draw it.
 *
 * `near` is the row that decides it (the choice that hides it), rung in its place so the person
 * lands on the thing to change. A row drawn on another screen ends the sentence in that screen's
 * name, as a link, the way every toast that names a place does.
 */
interface NotDrawn {
	because: ToastWords;
	near?: string;
}

/** What each mounted pane can say about the rows it is not drawing. See the head of this file. */
const explainers = new Set<(key: string) => NotDrawn | null>();

/**
 * Explain the rows this pane leaves out, for as long as it is mounted.
 *
 * The explainer answers null for a key it draws, a key it does not know, and every key while its
 * own data is still loading: a null keeps the hunt waiting for the row. Returns the release, for an
 * `$effect` to hand back, so a pane that is gone explains nothing.
 */
export function explainAbsentRows(explain: (key: string) => NotDrawn | null): () => void {
	explainers.add(explain);
	return () => {
		explainers.delete(explain);
	};
}

/**
 * The one sentence for a row a choice hides: the row, the choice, and the answer it holds now.
 *
 * Every pane says it this way so the reader learns one shape. Quoted, because each of the three is
 * a label that reads as part of the sentence otherwise ("Save screenshots to is hidden while When
 * you take a screenshot is...").
 */
export function hiddenWhile(row: string, choice: string, answer: string): string {
	return `\u201c${row}\u201d is hidden while \u201c${choice}\u201d is set to \u201c${answer}\u201d.`;
}

/**
 * A row drawn on another screen and filed under this pane's section: the sentence ends in that
 * screen's name as a link ("Which copy to keep" is set on Near duplicates).
 */
export function drawnOn(row: string, screen: { href: string; label: string }): NotDrawn {
	return { because: [`\u201c${row}\u201d is set on `, place(screen.label, screen.href)] };
}

/**
 * `hiddenWhile` read off the registry: the row a choice hides, the choice, and the answer it holds
 * now in the choice's own words. Null when either declaration is not on offer, so the caller says
 * nothing rather than a sentence with a hole in it.
 */
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

/**
 * Take away whatever is HIDING a row that is in the document.
 *
 * A closed `<details>` keeps its rows in the document and draws none of them, so `scrollIntoView`
 * on one does nothing and the ring lights an invisible box. And the pane behind an open sub-page is
 * mounted but hidden (see `SettingsPane`), so a row on it is found and cannot be seen. True when
 * something was opened or closed, so the caller lets it draw before scrolling.
 */
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

/**
 * Open the menu a row's choice is made in, where the row has one.
 *
 * A task's When is chosen in the menu behind the chevron beside Run now, so a row rung for it would
 * be found and still not show the thing it was found for: the answers were behind a press nobody
 * had been told to make. A row whose choice is behind a menu marks that part `data-setting-door`
 * and the ring opens it, the way the keyboard does (Enter on the door), so the menu comes up with
 * the answer in force under the focus. A door that is disabled or already open is left alone.
 */
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
	/* `center`, not `start`: a row put at the very top of a scrolling pane sits under whatever is
	   pinned above it, and the reader is looking at the row below the one they were sent to. */
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

/**
 * The row a key names, on the section being opened.
 *
 * Two panes can carry one key's row: the pointer row on a feature's own pane that says where its
 * switch went keeps the switch's address (`SwitchPointer`). Pressing "Change in Importing" looks
 * for the row while the pane it was pressed on is still mounted, and the first row with that id
 * in the document is the pointer itself: a hunt taking it would ring it and stop at Importing's top.
 * So a row inside another section's pane (`data-section` on `SettingsPane`) is not the one; a row
 * outside any pane, or where no section was named, still counts.
 */
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

/**
 * Find the row this key names, open whatever hides it, and point at it.
 *
 * Resolves true once the row is rung, or the row that decides it is rung in its place (see
 * `explainAbsentRows`), false when the hunt ended without it: because the pane finished drawing
 * without it, a newer link took over, or the backstop passed. A hunt that ends
 * by itself empty-handed SAYS so; one a newer link replaced does not, since the person has already
 * gone somewhere else.
 */
export function revealSetting(key: string, section?: string): Promise<boolean> {
	if (typeof document === 'undefined') return Promise.resolve(false);
	callOff();

	return new Promise<boolean>((resolve) => {
		/* Counts what the PANE draws, once there is a pane: a pane still filling itself is busy
		   even between requests, because a second request often starts from the first one's
		   answer. The pane only: the screen behind a settings panel can be busy for ever (a
		   progress bar, a preview playing) and must not keep the hunt alive. */
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

		/* Ask whatever owns the key on EVERY look, not once before the first.
		 *
		 * A link followed from another screen gets here before the pane has mounted, so the group
		 * that owns the key has not claimed it yet and one attempt would always answer false,
		 * the exact reason the row itself is hunted for rather than fetched once. Harmless where
		 * nothing owns the key, which is most of them: it is a map lookup that misses. */
		/* The row being looked for: the key's own, or, once its pane has said why that one is not
		   drawn, the row that decides it, looked for the same way (it may be on a sub-page of its
		   own), with the pane's sentence said once it is rung. */
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

		/* A row the pane is deliberately not drawing: ring what decides it and say why. Asked only
		   while no request is out, so an answer is never read off a pane still filling in. True
		   when the hunt ends here: the row that decides it rung immediately, or a sentence with no
		   row to ring. */
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
				/* A pane that said why the row is not drawn has accounted for it, even where the row
				   that decides it never came: its sentence, not "not on this page". */
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

/**
 * The place on the section being opened that is called this: a heading first, then a row or fold.
 *
 * On the section's own pane or the sub-page over it, never another section's pane still mounted
 * while this one is on its way (the reason `rowOn` asks the same).
 */
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

/**
 * Point at a thing a pane draws that has a name and no key: the heading over a group, a card, a
 * row drawn by hand.
 *
 * A search result or a pasted path naming one that opened its section at the top and stopped would,
 * on a long pane, leave the reader to find a heading that may be two screens down or inside a
 * closed fold. So the name is looked for where a pane writes names (`NAMED`), whatever hides it is
 * opened (a fold, the sub-page that claims it by `byName`), and it is rung the way a row is.
 *
 * Waited for as a row is, while the pane loads. A name the pane never draws ends the hunt in
 * silence: the section is open at its top, which is where the result said it lives, and a sentence
 * about a heading would be noise. Resolves true once the name is rung.
 */
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

/* There is deliberately no exported `stopRevealing`: nothing would call it. The hunt gives up on
 * its own, and each hunt calls `callOff` before it starts, so two links pressed quickly
 * cannot leave two rings. An exported name with no reader is an invitation for a second one.
 * See `public-surface.test.ts`, which refuses them.
 */
