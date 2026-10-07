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
import { screenBar } from './screen-bar.svelte';
import { stage } from './stage.svelte';

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
 * Whether the named menus fit, which is a question about the bar and not the window.
 *
 * The window's width is not what the bar's three columns share: the rail takes 208px of it when
 * open, and in the desktop window the caption buttons take about 138 more, so the bar measures
 * itself.
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
		screenBar.barEnd = 0;
		screenBar.sizeOnBar = true;
		screenBar.pasteOnBar = true;
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

	/* Ends of 100 and 204: each side keeps 304, so the sum is 2 * 304 + 88 + 227 = 923. */
	it('keeps them up here while the bar holds both ends, the menus and the field floor', () => {
		screenBar.watchRoom(barWithEnds(100, 204).bar);
		barIs(923);

		expect(screenBar.barEnd).toBe(304);
		expect(screenBar.roomOnTopBar).toBe(true);
	});

	it('sends them down when the bar has less, however wide the WINDOW is', () => {
		/* Nothing here touches `matchMedia`: the rail and the caption buttons come out of this bar
		   without the window changing size at all. */
		screenBar.watchRoom(barWithEnds(100, 204).bar);
		barIs(922);

		expect(screenBar.roomOnTopBar).toBe(false);
	});

	it('reads the CONTENT box, which is what the padding for the caption buttons comes out of', () => {
		screenBar.watchRoom(barWithEnds(100, 204).bar);
		for (const report of reporters)
			report(
				[
					{ contentBoxSize: [{ inlineSize: 900, blockSize: 56 }], contentRect: { width: 1000 } }
				] as unknown as ResizeObserverEntry[],
				null as unknown as ResizeObserver
			);

		expect(screenBar.roomOnTopBar).toBe(false);
	});

	it('falls back to `contentRect` where an engine fills in nothing else', () => {
		screenBar.watchRoom(barWithEnds(100, 204).bar);
		for (const report of reporters)
			report(
				[{ contentRect: { width: 900 } } as unknown as ResizeObserverEntry],
				null as unknown as ResizeObserver
			);

		expect(screenBar.roomOnTopBar).toBe(false);
	});

	it('measures the end group again when a screen adds to it, and needs more room', () => {
		const { bar, parts } = barWithEnds(100, 204);
		screenBar.watchRoom(bar);
		barIs(1000);
		expect(screenBar.roomOnTopBar).toBe(true);

		// Theater's controls about the whole window widen the end by 88.
		parts[0].getBoundingClientRect = () => ({ width: 188 }) as DOMRect;
		barIs(1000);
		expect(screenBar.barEnd).toBe(392);
		expect(screenBar.roomOnTopBar, 'the menus stayed up beside a wider end').toBe(false);
	});

	it("keeps them up here at a phone's width, where the bar is the search box and squares", () => {
		/* The sum behind the threshold is the desktop bar's. On a phone the collapse, the preview
		   and the tile size are gone and Add is a square, so Filter and Sort fit at the line's end. */
		vi.stubGlobal('matchMedia', (query: string) => ({
			matches: query === '(max-width: 767px)',
			addEventListener: () => {},
			removeEventListener: () => {}
		}));
		screenBar.watchRoom(barWithEnds(100, 204).bar);
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

	/** Ends of 100 and a part holding the tile size (122) beside Add (66), 16 apart: 304 in all. */
	function barWithSize(beside = true): { bar: HTMLElement; size: { standing: boolean } } {
		const { bar, parts } = barWithEnds(100, 0);
		const size = { standing: true };
		const slider = document.createElement('label');
		slider.className = 'size';
		slider.getBoundingClientRect = () => ({ width: size.standing ? 122 : 0 }) as DOMRect;
		parts[1].append(slider);
		if (beside) parts[1].append(document.createElement('div'));
		parts[1].style.columnGap = '16px';
		const room = beside ? 66 : 0;
		parts[1].getBoundingClientRect = () =>
			({ width: room + (size.standing ? 122 + (beside ? 16 : 0) : 0) }) as DOMRect;
		return { bar, size };
	}

	/* Each side keeps the whole 304 beside the field's floor: 2 * 304 + 227 = 835. */
	it('takes the tile size off before the centre group would slide, and back at the same width', () => {
		const { bar, size } = barWithSize();
		screenBar.watchRoom(bar);

		barIs(835);
		expect(screenBar.sizeOnBar).toBe(true);
		barIs(834);
		expect(screenBar.sizeOnBar, 'the group slid with the tile size still up').toBe(false);

		size.standing = false;
		barIs(834);
		expect(screenBar.barEnd, 'the centre is capped by what is drawn').toBe(166);
		expect(screenBar.sizeOnBar).toBe(false);
		barIs(835);
		expect(screenBar.sizeOnBar, 'the tile size came back late').toBe(true);
	});

	it('moves no threshold of the menus by leaving', () => {
		const { bar, size } = barWithSize();
		screenBar.watchRoom(bar);
		barIs(834);
		size.standing = false;
		barIs(900);

		expect(screenBar.roomOnTopBar, 'the menus came up beside a narrower end').toBe(false);
		barIs(923);
		expect(screenBar.roomOnTopBar).toBe(true);
	});

	/* Without the tile size the end is 166, Add's 36 px paste half in it: 2 * 166 + 227 = 559. */
	it('folds the paste half into Add after the tile size, and back at the same width', () => {
		const { bar, size } = barWithSize();
		const trailing = bar.querySelectorAll<HTMLElement>('.actions > *')[1];
		const paste = { standing: true };
		const add = document.createElement('div');
		add.className = 'add';
		const half = document.createElement('span');
		half.className = 'half trail';
		half.getBoundingClientRect = () => ({ width: paste.standing ? 36 : 0 }) as DOMRect;
		add.append(half);
		trailing.append(add);
		trailing.getBoundingClientRect = () =>
			({ width: (paste.standing ? 66 : 30) + (size.standing ? 138 : 0) }) as DOMRect;
		screenBar.watchRoom(bar);

		barIs(834);
		expect(screenBar.sizeOnBar).toBe(false);
		expect(screenBar.pasteOnBar, 'the paste half folded before the tile size left').toBe(true);
		size.standing = false;
		barIs(559);
		expect(screenBar.pasteOnBar).toBe(true);
		barIs(558);
		expect(screenBar.pasteOnBar, 'the group slid with the paste half still up').toBe(false);

		paste.standing = false;
		barIs(558);
		expect(screenBar.barEnd, 'the centre is capped by what is drawn').toBe(130);
		expect(screenBar.pasteOnBar).toBe(false);
		barIs(559);
		expect(screenBar.pasteOnBar, 'the paste half came back late').toBe(true);
		barIs(834);
		expect(screenBar.sizeOnBar, "the paste half's leaving moved the tile size's threshold").toBe(
			false
		);
	});

	it('counts the gap with it only where something stands beside it', () => {
		/* A guest's: no Add, so the tile size frees its own width and no gap. */
		const { bar, size } = barWithSize(false);
		screenBar.watchRoom(bar);
		barIs(670);
		expect(screenBar.sizeOnBar).toBe(false);
		size.standing = false;
		barIs(671);
		expect(screenBar.sizeOnBar, 'the tile size came back late').toBe(true);
	});

	it("offers the tile size on the screen's own row while it is off the top bar", () => {
		const home = { id: 'tile-size', icon: 'grid_view' as const, label: 'Tile size', content: {} };
		screenBar.sizeHome = home as unknown as typeof screenBar.sizeHome;
		const owner = Symbol('a wall');
		screenBar.publish(owner, {});
		try {
			expect(screenBar.tools.panels).toBeUndefined();
			screenBar.sizeOnBar = false;
			expect(screenBar.tools.panels).toEqual([home]);
			stage.filling = true;
			expect(
				screenBar.tools.panels,
				'offered in a filled screen, where the top bar is not'
			).toBeUndefined();
		} finally {
			stage.filling = false;
			screenBar.release(owner);
			screenBar.sizeHome = null;
		}
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

	it('lays every screen out for the widest met, decided on the width the bar has now', () => {
		const { bar, parts } = barWithEnds(100, 204);
		screenBar.watchRoom(bar);
		const onto = (id: string) => {
			Object.assign(page, { route: { id } });
			screenBar.publish(Symbol(id), {});
		};

		onto('/theater-wide');
		parts[1].getBoundingClientRect = () => ({ width: 292 }) as DOMRect;
		barIs(1000);
		expect(screenBar.roomOnTopBar).toBe(false);
		expect(screenBar.barEnd).toBe(392);

		parts[1].getBoundingClientRect = () => ({ width: 204 }) as DOMRect;
		onto('/browse-narrow');
		expect(screenBar.roomOnTopBar, 'the menus came up between two screens').toBe(false);
		expect(screenBar.barEnd, 'the centre widened between two screens').toBe(392);
		barIs(1000);
		expect(screenBar.barEnd, 'the narrower screen measured its own end over the widest').toBe(392);
		expect(screenBar.roomOnTopBar, 'the narrower screen put the menus back up').toBe(false);

		// The window widened while another screen was on.
		barIs(2000);
		onto('/theater-wide');
		expect(screenBar.roomOnTopBar, 'an old width kept the menus down').toBe(true);
	});

	it('keeps the menus down on every screen once one needed more room at this width', () => {
		const bar = document.createElement('header');
		const field = document.createElement('form');
		field.className = 'search';
		bar.append(field);
		let fieldWidth = 150;
		field.getBoundingClientRect = () => ({ width: fieldWidth }) as DOMRect;
		screenBar.watchRoom(bar);
		const onto = (id: string) => {
			Object.assign(page, { route: { id } });
			screenBar.publish(Symbol(id), {});
		};

		onto('/squeezed');
		barIs(864);
		expect(screenBar.roomOnTopBar).toBe(false);
		fieldWidth = 400;
		onto('/roomy');
		barIs(864);
		expect(screenBar.roomOnTopBar, 'the menus came up on a screen that needs less').toBe(false);
		barIs(2000);
		expect(screenBar.roomOnTopBar).toBe(true);
	});

	it('remembers what each screen needed across sittings, in this browser', async () => {
		localStorage.setItem(
			'sift.screen-bar.needs',
			JSON.stringify({ '/kept': { room: 0, end: 500, barEnd: 480 }, '/broken': { room: 'x' } })
		);
		vi.resetModules();
		const fresh = (await import('./screen-bar.svelte')).screenBar;
		fresh.watchRoom(barWithEnds(100, 204).bar);
		const onto = (id: string) => {
			Object.assign(page, { route: { id } });
			fresh.publish(Symbol(id), {});
		};
		onto('/kept');
		expect(fresh.barEnd, 'the centre waited for a measurement').toBe(480);

		onto('/other');
		barIs(1200);
		onto('/kept');
		expect(fresh.roomOnTopBar, 'the menus stood up beside an end of 500').toBe(false);
		localStorage.removeItem('sift.screen-bar.needs');
	});

	it("decides the tile size on a screen met again by that screen's own end group", () => {
		const { bar, parts } = barWithEnds(100, 204);
		screenBar.watchRoom(bar);
		const onto = (id: string) => {
			Object.assign(page, { route: { id } });
			screenBar.publish(Symbol(id), {});
		};

		onto('/theater');
		barIs(800);
		expect(screenBar.sizeOnBar).toBe(false);
		parts[0].getBoundingClientRect = () => ({ width: 10 }) as DOMRect;
		onto('/browse');
		barIs(800);
		expect(screenBar.sizeOnBar).toBe(true);

		onto('/theater');
		expect(screenBar.sizeOnBar, "the last screen's end decided until measured").toBe(false);
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
