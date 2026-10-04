/* The rule that decides whether a menu on the top bar is open, and it is not one rule.
 *
 * A menu here can be opened two ways and the two have to CLOSE differently. Pointing at one opens
 * it and wandering off shuts it again, the way a menu bar has always behaved. Pressing one opens it
 * and nothing but another press shuts it, because a panel somebody is working inside has to survive
 * the pointer leaving it: ticking four facets means going out to the wall and back.
 *
 * The corner where the two meet is the one that matters: the pointer opens a menu after the
 * dwell and the press that follows arrives at a menu that is ALREADY open. Toggling there shuts it, which is the most ordinary gesture there is
 * (point, then click) being read as "close this".
 *
 * The timings are the module's own. `leaving` waits 220ms before shutting, so every test that cares
 * about it drives the clock rather than waiting on it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { screenBar } from './screen-bar.svelte';

const FILTERS = 'filters';
/* Two ids the bar really uses. The order is a menu on the row rather than a panel, but what these
   tests are about is the store's rule for ANY two panels, so a second id is all that is needed and
   the kept searches are a real one. */
const SORT = 'a second panel';
const SAVED = 'saved';

/** Long enough for the grace to have run out, whatever it is set to. */
const AFTER_THE_GRACE = 1000;

beforeEach(() => {
	vi.useFakeTimers();
	screenBar.close();
});

afterEach(() => {
	screenBar.close();
	document.body.innerHTML = '';
	vi.useRealTimers();
});

describe('pressing', () => {
	it('opens what was pressed, and a second press on the same one shuts it', () => {
		screenBar.toggle(FILTERS);
		expect(screenBar.open).toBe(FILTERS);

		screenBar.toggle(FILTERS);
		expect(screenBar.open).toBe(null);
	});

	it('moves to the other menu rather than shutting, when a different one is pressed', () => {
		screenBar.toggle(FILTERS);
		screenBar.toggle(SORT);
		expect(screenBar.open).toBe(SORT);
	});

	it('leaves a PRESSED panel open however far the pointer goes', () => {
		screenBar.toggle(FILTERS);
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);
		expect(screenBar.open).toBe(FILTERS);
	});
});

describe('pointing', () => {
	it('opens, and shuts again once the pointer has been gone for the grace', () => {
		screenBar.showByHover(FILTERS);
		expect(screenBar.open).toBe(FILTERS);

		screenBar.leaving();
		// Still open while the grace runs: the trigger is on the top bar and the panel is drawn
		// further down, so shutting at once would make it unreachable by the gesture that opened it.
		vi.advanceTimersByTime(100);
		expect(screenBar.open).toBe(FILTERS);

		vi.advanceTimersByTime(AFTER_THE_GRACE);
		expect(screenBar.open).toBe(null);
	});

	it('stays open when the pointer comes back before the grace runs out', () => {
		screenBar.showByHover(FILTERS);
		screenBar.leaving();
		vi.advanceTimersByTime(100);
		screenBar.stillHere();
		vi.advanceTimersByTime(AFTER_THE_GRACE);
		expect(screenBar.open).toBe(FILTERS);
	});

	it('stays open while something inside it has a layer of its own up', () => {
		/*
		 * The facet column chooser. Its list is rendered into a portal at the end of the document
		 * rather than inside the panel, so moving the pointer onto it leaves the panel as far as
		 * the DOM is concerned, and the panel must not shut while somebody is choosing from it.
		 */
		screenBar.showByHover(FILTERS);
		const list = document.createElement('div');
		list.setAttribute('role', 'listbox');
		document.body.append(list);

		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);
		expect(screenBar.open).toBe(FILTERS);

		// And it shuts once that layer has gone and the pointer leaves again.
		list.remove();
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);
		expect(screenBar.open).toBe(null);
	});
});

describe('pointing and then pressing', () => {
	it('PINS what the pointer opened rather than shutting it', () => {
		screenBar.showByHover(FILTERS);
		screenBar.toggle(FILTERS);
		expect(screenBar.open).toBe(FILTERS);
	});

	it('and the pinned panel then survives the pointer leaving', () => {
		screenBar.showByHover(FILTERS);
		screenBar.toggle(FILTERS);

		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);
		expect(screenBar.open).toBe(FILTERS);
	});

	it('takes a second press to shut it, which is the only way left to ask', () => {
		screenBar.showByHover(FILTERS);
		screenBar.toggle(FILTERS);
		screenBar.toggle(FILTERS);
		expect(screenBar.open).toBe(null);
	});

	it('still moves to the other menu when the press is on a different one', () => {
		screenBar.showByHover(FILTERS);
		screenBar.toggle(SORT);
		expect(screenBar.open).toBe(SORT);
	});
});

describe('opened from somewhere else on the screen', () => {
	it('is a press, not a point: a cell on the wall opens the panel and it stays', () => {
		/* `show` is what a control OUTSIDE the bar calls: a tile's own filter button, which is on the
		   wall while the panel it opens is on the bar. Getting from one to the other means the pointer
		   crossing everything in between, so this cannot be a hover-opened panel. */
		screenBar.show(FILTERS);
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);
		expect(screenBar.open).toBe(FILTERS);
	});
});

describe('shutting', () => {
	it('cancels a close already counting down, so nothing reopens behind it', () => {
		screenBar.showByHover(FILTERS);
		screenBar.leaving();
		screenBar.close();
		expect(screenBar.open).toBe(null);

		screenBar.toggle(SORT);
		vi.advanceTimersByTime(AFTER_THE_GRACE);
		// The pending close belonged to the panel that has already gone. If it were still armed it
		// would land here and shut a panel that was pressed after it.
		expect(screenBar.open).toBe(SORT);
	});

	it('forgets that the pointer was what opened it', () => {
		screenBar.showByHover(FILTERS);
		screenBar.close();
		screenBar.toggle(FILTERS);

		// Pressed from shut, so this is a press and the pointer has no say in it.
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);
		expect(screenBar.open).toBe(FILTERS);
	});
});

describe('a panel somebody is typing in has not been left', () => {
	/*
	 * Renaming a saved filter must work however its menu was opened. Point at the Saved menu and it
	 * opens; press the pencil and a box appears; move the hand towards the keyboard and the pointer
	 * leaves the panel. A hover-opened panel must not shut then and lose the half-typed name, just
	 * as a click-opened one does not. Two ways of opening one menu behaving differently is the
	 * worst kind of fault, because whoever hits it cannot say what they did differently.
	 */
	function aPanelHolding(what: HTMLElement): void {
		const panel = document.createElement('div');
		panel.className = 'bar-panel';
		panel.append(what);
		document.body.append(panel);
		what.focus();
	}

	it('stays open while the caret is in a box inside it', () => {
		const box = document.createElement('input');
		aPanelHolding(box);

		screenBar.showByHover(SAVED);
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);

		expect(screenBar.open, 'the box was taken away mid-word').toBe(SAVED);
	});

	it('shuts once the box has gone, so it is not pinned for ever', () => {
		const box = document.createElement('input');
		aPanelHolding(box);
		screenBar.showByHover(SAVED);
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);
		expect(screenBar.open).toBe(SAVED);

		box.blur();
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);

		expect(screenBar.open).toBe(null);
	});

	it('does not count a box that is somewhere else on the page', () => {
		/* The top bar's own search field is a text box and is not in a panel. A guard that asked only
		   "is a caret anywhere" would pin every hovered panel open while somebody was typing a
		   search, which is most of the time this bar is used. */
		const elsewhere = document.createElement('input');
		document.body.append(elsewhere);
		elsewhere.focus();

		screenBar.showByHover(SAVED);
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);

		expect(screenBar.open).toBe(null);
	});

	it('does not count a BUTTON inside the panel, only something that takes text', () => {
		/* A focus test would have been the shorter rule and the wrong one: clicking a facet leaves the
		   focus on that checkbox, so every hovered filter panel would stay open after any tick: a
		   behaviour change nobody asked for, dressed as a bug fix. What must never happen is a box
		   being taken away mid-word. */
		const pressed = document.createElement('button');
		aPanelHolding(pressed);

		screenBar.showByHover(SAVED);
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);

		expect(screenBar.open).toBe(null);
	});
});

describe('a panel holding something unfinished', () => {
	/*
	 * A panel with a Save and a Cancel in it is a MODE, and the pointer drifting off must not abandon
	 * it. What made this necessary was worse than drift: pressing Edit on a kept filter collapses the
	 * chips row (what it would say is in the panel now) so the panel jumps up by that row's height
	 * under a pointer nobody moved, `mouseleave` fires because the LAYOUT moved, and the editor
	 * vanishes in the same gesture that opened it.
	 */
	function panelHolding(markup: string) {
		const panel = document.createElement('div');
		panel.className = 'bar-panel';
		panel.innerHTML = markup;
		document.body.append(panel);
	}

	it('does not fall shut on the pointer leaving', () => {
		panelHolding('<div data-unfinished><button>Save</button></div>');
		screenBar.showByHover(FILTERS);
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);

		expect(screenBar.open).toBe(FILTERS);
	});

	it('still falls shut when nothing in it is half done', () => {
		/* The positive control. Without it a store that never closed on leaving at all would satisfy
		   the check above, and never closing is the likelier way to get this wrong. */
		panelHolding('<div><button>Save</button></div>');
		screenBar.showByHover(FILTERS);
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);

		expect(screenBar.open).toBeNull();
	});

	it('is shut outright by anything that asks, mode or not', () => {
		/* The veto is the HOVER close alone. Escape and the trigger call `close()`, and a mode nobody
		   can leave deliberately would be a worse fault than the one this fixes. */
		panelHolding('<div data-unfinished><button>Save</button></div>');
		screenBar.showByHover(FILTERS);
		screenBar.close();

		expect(screenBar.open).toBeNull();
	});

	it('ignores something unfinished OUTSIDE the panel', () => {
		/* The selector is anchored on `.bar-panel` deliberately: a half-filled form on the screen
		   behind would otherwise pin every panel in the application open. */
		const stray = document.createElement('div');
		stray.setAttribute('data-unfinished', '');
		document.body.append(stray);
		screenBar.showByHover(FILTERS);
		screenBar.leaving();
		vi.advanceTimersByTime(AFTER_THE_GRACE);

		expect(screenBar.open).toBeNull();
	});
});

/*
 * A draft keeps the panel's box.
 *
 * Editing a kept filter changes the panel's shape as it is worked: switching a column to all of
 * these reflows it, and Cancel or Save takes the editing region away. A panel that gets shorter
 * under a still pointer leaves the pointer outside it, the hover close runs, and the panel would
 * vanish on the press. The store says when the shape is held; `FilterBar` holds the slot at that height.
 */
describe('a draft keeps the panel its box', () => {
	afterEach(() => {
		screenBar.drafting(false);
		screenBar.leaving();
		screenBar.close();
	});

	it('holds while a draft is open, whatever the pointer does', () => {
		screenBar.showByHover(FILTERS);
		screenBar.stillHere();
		screenBar.drafting(true);
		expect(screenBar.shapeHeld).toBe(true);

		screenBar.leaving();
		expect(screenBar.shapeHeld).toBe(true);
	});

	it('holds past Cancel or Save while the pointer is still inside, and lets go when it leaves', () => {
		/* The press that ends the draft is inside the panel, so ending it is not leaving it. */
		screenBar.showByHover(FILTERS);
		screenBar.stillHere();
		screenBar.drafting(true);
		screenBar.drafting(false);
		expect(screenBar.shapeHeld).toBe(true);
		vi.advanceTimersByTime(AFTER_THE_GRACE);
		expect(screenBar.open).toBe(FILTERS);

		screenBar.leaving();
		expect(screenBar.shapeHeld).toBe(false);
	});

	it('lets go at once when the draft ends with the pointer elsewhere', () => {
		/* The positive control: a store that held forever would pass the two above. */
		screenBar.showByHover(FILTERS);
		screenBar.drafting(true);
		screenBar.leaving();
		screenBar.drafting(false);
		expect(screenBar.shapeHeld).toBe(false);
	});

	it('holds nothing when no draft was ever open', () => {
		screenBar.showByHover(FILTERS);
		screenBar.stillHere();
		expect(screenBar.shapeHeld).toBe(false);
	});
});

/*
 * Whether the named menus fit, which is a question about the bar and not the window.
 *
 * The window's width is not what the bar's three columns share: the rail takes 208px of it when
 * open, and in the desktop window the caption buttons take about 138 more. At half-screen on a
 * large monitor the search field would be left too narrow and its keyboard hint would wrap, so the
 * bar measures itself.
 *
 * These drive a `ResizeObserver` by hand. The one in `test-setup` reports nothing, deliberately, so
 * a test that needs a size supplies it, and the size supplied is the only thing asserted on.
 */
describe('the room on the top bar', () => {
	/** Every callback handed to a `ResizeObserver` while the fake is installed. */
	let reporters: ResizeObserverCallback[] = [];
	let disconnected = 0;

	beforeEach(() => {
		reporters = [];
		disconnected = 0;
		vi.stubGlobal(
			'ResizeObserver',
			class {
				#report: ResizeObserverCallback;
				constructor(report: ResizeObserverCallback) {
					this.#report = report;
				}
				observe(): void {
					reporters.push(this.#report);
				}
				unobserve(): void {}
				disconnect(): void {
					disconnected += 1;
				}
			}
		);
	});

	afterEach(() => {
		vi.unstubAllGlobals();
		// The store is a module singleton, so a width left set here would be the next test's answer.
		screenBar.roomOnTopBar = true;
		screenBar.trailGives = 0;
	});

	/** Say the bar's content box is this wide, the way the browser would. */
	function barIs(inlineSize: number): void {
		for (const report of reporters)
			report(
				[
					{ contentBoxSize: [{ inlineSize, blockSize: 56 }], contentRect: { width: inlineSize } }
				] as unknown as ResizeObserverEntry[],
				null as unknown as ResizeObserver
			);
	}

	it('keeps them up here while the bar has room for them beside the field', () => {
		screenBar.watchRoom(document.createElement('header'));
		barIs(780);

		expect(screenBar.roomOnTopBar).toBe(true);
	});

	it('sends them down when the bar has less, however wide the WINDOW is', () => {
		/* The distinction this rule is about. Nothing here touches `matchMedia`, and the
		   answer still moves, because a rail 208px wide and 138px of caption buttons come out of
		   this bar without the window changing size at all. */
		screenBar.watchRoom(document.createElement('header'));
		barIs(646);

		expect(screenBar.roomOnTopBar).toBe(false);
	});

	it('reads the CONTENT box, which is what the padding for the caption buttons comes out of', () => {
		/* A border box of 806 with 170px of padding is a content box of 636, and it is the 636 that
		   the three columns have to share. Reading the wrong one is the same fault as reading the
		   window, one level down. */
		screenBar.watchRoom(document.createElement('header'));
		barIs(636);

		expect(screenBar.roomOnTopBar).toBe(false);
	});

	it('falls back to `contentRect` where an engine fills in nothing else', () => {
		screenBar.watchRoom(document.createElement('header'));
		for (const report of reporters)
			report(
				[{ contentRect: { width: 640 } } as unknown as ResizeObserverEntry],
				null as unknown as ResizeObserver
			);

		expect(screenBar.roomOnTopBar).toBe(false);
	});

	it("keeps them up here at a phone's width, where the bar is the search box and squares", () => {
		/* The sum behind the threshold is the desktop bar's. On a phone the collapse, the preview
		   and the tile size are gone and Add is a square, so Filter and Sort fit at the line's end. */
		vi.stubGlobal('matchMedia', (query: string) => ({
			matches: query === '(max-width: 767px)',
			addEventListener: () => {},
			removeEventListener: () => {}
		}));
		screenBar.watchRoom(document.createElement('header'));
		barIs(358);

		expect(screenBar.roomOnTopBar).toBe(true);
	});

	/*
	 * A screen that adds controls to the bar needs more room than the sum says, and the field is
	 * what tells: Theater at a half-screen width would leave it about 150px and two rows tall with
	 * its menus up.
	 */
	it('sends them down when the FIELD is left less than its floor, and keeps them down', () => {
		const bar = document.createElement('header');
		const field = document.createElement('form');
		field.className = 'search';
		bar.append(field);
		let fieldWidth = 150;
		field.getBoundingClientRect = () => ({ width: fieldWidth }) as DOMRect;
		screenBar.watchRoom(bar);

		barIs(864);
		expect(screenBar.roomOnTopBar, 'the menus stayed up over a squeezed field').toBe(false);

		// Down on the screen's own row, the field has its room back; that is not a reason to go up.
		fieldWidth = 400;
		barIs(864);
		expect(screenBar.roomOnTopBar, 'the menus went back up to squeeze it again').toBe(false);

		// A bar wide enough for the field's floor beside them is.
		barIs(950);
		expect(screenBar.roomOnTopBar).toBe(true);
	});

	/*
	 * The trail's room is held on every screen, and at 1280 it would leave Theater's field short of
	 * its floor: the menus would go down to a row of their own and Theater's title would stand a
	 * row lower than every other screen's. The room gives way first, down to its least.
	 */
	it("takes what the field is short of from the trail's room before sending the menus down", () => {
		const bar = document.createElement('header');
		const trail = document.createElement('div');
		trail.className = 'trail';
		const field = document.createElement('form');
		field.className = 'search';
		bar.append(trail, field);
		// The two share one row: what the room gives up, the field gains.
		let fieldLeft = 175;
		trail.getBoundingClientRect = () => ({ width: 210 - screenBar.trailGives }) as DOMRect;
		field.getBoundingClientRect = () => ({ width: fieldLeft + screenBar.trailGives }) as DOMRect;
		screenBar.watchRoom(bar);

		barIs(1048);
		expect(screenBar.trailGives, 'the room gave up what the field was short of').toBe(52);
		expect(screenBar.roomOnTopBar, 'the menus left the bar over a room that could give').toBe(true);

		// More short than the room has above its least: it gives all it can, and the menus go down.
		fieldLeft = 23;
		barIs(1048);
		expect(screenBar.trailGives).toBe(146);
		expect(screenBar.roomOnTopBar).toBe(false);

		screenBar.publish(Symbol('browse'), {});
		expect(screenBar.trailGives, 'another screen inherited what this one took').toBe(0);
	});

	it('starts a new screen from the sum again', () => {
		const bar = document.createElement('header');
		const field = document.createElement('form');
		field.className = 'search';
		bar.append(field);
		let fieldWidth = 150;
		field.getBoundingClientRect = () => ({ width: fieldWidth }) as DOMRect;
		screenBar.watchRoom(bar);
		screenBar.publish(Symbol('theater'), {});
		barIs(864);
		expect(screenBar.roomOnTopBar).toBe(false);

		fieldWidth = 400;
		screenBar.publish(Symbol('browse'), {});
		expect(screenBar.roomOnTopBar, 'Browse inherited what Theater needed').toBe(true);
	});

	it('gives back a teardown that stops watching', () => {
		/* The bar is the only thing that observes, and it is drawn once, but an effect that reruns
		   without this leaves an observer per run, all writing the same field from different
		   elements. */
		const stop = screenBar.watchRoom(document.createElement('header'));
		stop();

		expect(disconnected).toBe(1);
	});

	it('does nothing at all where there is no ResizeObserver, rather than throwing', () => {
		/* jsdom has none. The guard is the same one every other observer in this codebase carries,
		   and without it every test that renders the top bar dies on construction. */
		vi.stubGlobal('ResizeObserver', undefined);

		expect(() => screenBar.watchRoom(document.createElement('header'))()).not.toThrow();
	});
});
