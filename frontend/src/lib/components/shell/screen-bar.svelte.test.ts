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
import { page } from '$app/state';
import { FIELD_FLOOR, screenBar } from './screen-bar.svelte';

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
		// further down, so shutting immediately would make it unreachable by the gesture that opened it.
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

	it('lets go immediately when the draft ends with the pointer elsewhere', () => {
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
 * What the top bar measures of itself: one row at every width, so nothing leaves it. It measures
 * the end group, which each side of the centre group keeps, and the screen's menus, which the
 * centre group's floor adds to the field's.
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
		screenBar.barEnd = 0;
		screenBar.centreFloor = FIELD_FLOOR;
		screenBar.compact = false;
		Reflect.deleteProperty(page, 'route');
	});

	/** A bar whose end group is parts this wide; their sum is what each side of the centre keeps. */
	function barWithEnds(...widths: number[]): { bar: HTMLElement; parts: HTMLElement[] } {
		const bar = document.createElement('header');
		const actions = document.createElement('div');
		actions.className = 'actions';
		const parts = widths.map((width) => {
			const part = document.createElement('div');
			part.getBoundingClientRect = () => ({ width }) as DOMRect;
			return part;
		});
		actions.append(...parts);
		bar.append(actions);
		return { bar, parts };
	}

	/** Say the bar has been measured again, the way the browser would. */
	function measured(): void {
		for (const report of reporters)
			report([] as unknown as ResizeObserverEntry[], null as unknown as ResizeObserver);
	}

	const onto = (id: string) => {
		Object.assign(page, { route: { id } });
		screenBar.publish(Symbol(id), {});
	};

	it('caps the centre group by the end group as drawn', () => {
		const { bar } = barWithEnds(100, 204);
		screenBar.watchRoom(bar);
		measured();
		expect(screenBar.barEnd).toBe(304);
	});

	it('hands nothing down to the row below, however short the bar', () => {
		const owner = Symbol('a wall');
		screenBar.watchRoom(barWithEnds(100, 204).bar);
		screenBar.publish(owner, { panels: [] });
		measured();
		expect(screenBar.tools.panels, 'a press was added to the row below').toEqual([]);
		for (const gone of ['roomOnTopBar', 'sizeOnBar', 'pasteOnBar', 'sizeHome'])
			expect(gone in screenBar, `the bar still decides ${gone}`).toBe(false);
		screenBar.release(owner);
	});

	it("adds the screen's menus to the field's floor, and only the field's where there are none", () => {
		const bar = document.createElement('header');
		const centre = document.createElement('div');
		centre.className = 'centre';
		const menus = document.createElement('div');
		menus.className = 'menus';
		menus.getBoundingClientRect = () => ({ width: 80 }) as DOMRect;
		centre.append(menus);
		bar.append(centre);
		screenBar.watchRoom(bar);
		measured();
		expect(screenBar.centreFloor).toBe(FIELD_FLOOR + 80);

		menus.remove();
		measured();
		expect(screenBar.centreFloor).toBe(FIELD_FLOOR);
	});

	it('lays every screen out for the widest end met, so moving between screens moves nothing', () => {
		const { bar, parts } = barWithEnds(100, 204);
		screenBar.watchRoom(bar);

		onto('/theater-wide');
		parts[1].getBoundingClientRect = () => ({ width: 292 }) as DOMRect;
		measured();
		expect(screenBar.barEnd).toBe(392);

		parts[1].getBoundingClientRect = () => ({ width: 204 }) as DOMRect;
		onto('/browse-narrow');
		expect(screenBar.barEnd, 'the centre widened between two screens').toBe(392);
		measured();
		expect(screenBar.barEnd, 'the narrower screen measured its own end over the widest').toBe(392);
	});

	it("remembers each screen's end across sittings, in this browser", async () => {
		localStorage.setItem(
			'sift.screen-bar.ends.1',
			JSON.stringify({ '/kept': 500, '/broken': 'x' })
		);
		vi.resetModules();
		const fresh = (await import('./screen-bar.svelte')).screenBar;
		fresh.watchRoom(barWithEnds(100, 204).bar);
		Object.assign(page, { route: { id: '/kept' } });
		fresh.publish(Symbol('kept'), {});
		expect(fresh.barEnd, 'the centre waited for a measurement').toBe(500);
		localStorage.removeItem('sift.screen-bar.ends.1');
	});

	/** Say the bar's content box is this wide, the way the browser would. */
	function barIs(bar: HTMLElement, inlineSize: number): void {
		for (const report of reporters)
			report(
				[
					{
						target: bar,
						contentBoxSize: [{ inlineSize, blockSize: 56 }],
						contentRect: { width: inlineSize }
					}
				] as unknown as ResizeObserverEntry[],
				null as unknown as ResizeObserver
			);
	}

	/* The start end at its least (a 36px toggle), an end group of 348, the field's floor of 227 and
	   no menus: one row needs 36 + 2 * 0 gaps + 227 + 348 = 611. */
	function barNeeding611(): { bar: HTMLElement; parts: HTMLElement[] } {
		const built = barWithEnds(80, 268);
		const lead = document.createElement('div');
		lead.className = 'lead';
		const toggle = document.createElement('button');
		toggle.getBoundingClientRect = () => ({ width: 36 }) as DOMRect;
		lead.append(toggle);
		built.bar.prepend(lead);
		return built;
	}

	it('draws the end group tight where the field would go under its floor, and whole again', () => {
		const { bar, parts } = barNeeding611();
		screenBar.watchRoom(bar);
		barIs(bar, 700);
		expect(screenBar.compact).toBe(false);

		barIs(bar, 600);
		expect(screenBar.compact, 'the field was left under its floor').toBe(true);
		// Drawn tight it measures 100 less; the threshold is still the whole group's.
		parts[1].getBoundingClientRect = () => ({ width: 168 }) as DOMRect;
		barIs(bar, 600);
		expect(screenBar.compact).toBe(true);
		expect(screenBar.barEnd, 'the centre is capped by the end as drawn').toBe(248);
		barIs(bar, 605);
		expect(screenBar.compact, 'it came back before the whole group fitted').toBe(true);

		barIs(bar, 611);
		expect(screenBar.compact, 'it stayed tight with room for the whole group').toBe(false);
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
