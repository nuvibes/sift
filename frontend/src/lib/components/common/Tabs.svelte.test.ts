/*
 * Three readings of one thing, one of them showing.
 *
 * What is under test is the part that is invisible when it breaks: which pane a tab OPENS, what the
 * strip says out loud, and that arrowing along it does not open anything. The last one is not a
 * detail: a pane here can cost a request, and automatic activation would fetch a history on the
 * way past it to the pane somebody was actually going to.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import Tabs, { type TabChoice } from './Tabs.svelte';
import source from './Tabs.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';

/** One pane, saying which tab it belongs to, so "the right one is showing" is checkable. */
const pane = createRawSnippet<[string]>((id) => ({
	render: () => `<p data-pane="${id()}">The ${id()} pane</p>`
}));

const THREE: TabChoice[] = [
	{ id: 'about', label: 'About', count: 3 },
	{ id: 'media', label: 'Media', count: 8 },
	{ id: 'provenance', label: 'Provenance' }
];

let host: HTMLElement;
let mounted: Record<string, unknown> | null = null;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
});

function render(extra: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);

	const props = reactiveProps({
		tabs: THREE,
		value: 'about',
		label: 'File Info sections',
		pane,
		...extra
	});
	mounted = mount(Tabs, { target: host, props });
	flushSync();

	const triggers = () => [...host.querySelectorAll('[role="tab"]')] as HTMLButtonElement[];
	return {
		props,
		triggers,
		/**
		 * The pane a reader can actually reach. The panes sit side by side in a row that travels,
		 * so all three must stay in the layout and this component drops the library's `hidden`;
		 * what is asked for instead is the property `hidden` carried, not inert, which is what a
		 * reader and a screen reader both experience.
		 */
		showing: () =>
			[...host.querySelectorAll('[role="tabpanel"]')]
				.filter((one) => !one.hasAttribute('inert'))
				.map((one) => one.textContent?.trim()),
		/** Every pane, in the order they sit in the row. */
		panes: () => [...host.querySelectorAll('[role="tabpanel"]')] as HTMLElement[],
		/**
		 * Which pane the window is over, as the travel's own number says it: the number is set on
		 * the control itself, because the row of panes and the pill above them read one pair of
		 * numbers.
		 */
		at: () => (host.querySelector('.panes') as HTMLElement | null)?.style.getPropertyValue('--at'),
		/** How many panels the row is crossing, which is what its speed is made of. */
		panesCrossed: () =>
			(host.querySelector('.panes') as HTMLElement | null)?.style.getPropertyValue('--panes'),
		/** The one travelling rule, if any tab is showing at all. */
		pills: () => [...host.querySelectorAll('.rule')] as HTMLElement[],
		/** How tall the window has made itself, as the property it hands the stylesheet says it. */
		height: () =>
			(host.querySelector('.window') as HTMLElement | null)?.style.getPropertyValue('--tallest'),
		press: (label: string) => {
			triggers()
				.find((one) => one.textContent?.startsWith(label))
				?.click();
			flushSync();
		}
	};
}

describe('the strip', () => {
	it('is a tablist with a name, and says which tab is showing', () => {
		const drawn = render();

		expect(host.querySelector('[role="tablist"]')?.getAttribute('aria-label')).toBe(
			'File Info sections'
		);
		expect(drawn.triggers().map((one) => one.getAttribute('aria-selected'))).toEqual([
			'true',
			'false',
			'false'
		]);
	});

	it('draws a count where there is one and nothing where there is not', () => {
		/* Absent is not nought. A tab whose pane has not answered yet draws no number, because a
		   zero that becomes an eight a moment later reports a fault that is not there. */
		const drawn = render();

		expect(drawn.triggers().map((one) => one.textContent?.trim())).toEqual([
			'About3',
			'Media8',
			'Provenance'
		]);
	});

	it('groups a count the way every other count on screen is grouped', () => {
		// An ungrouped count reads "Done12000" beside a filter panel's grouped figures.
		const drawn = render({ tabs: [{ id: 'done', label: 'Done', count: 12000 }], value: 'done' });

		expect(drawn.triggers()[0].querySelector('.count')?.textContent).toBe((12000).toLocaleString());
		expect(drawn.triggers()[0].querySelector('.count')?.textContent).not.toBe('12000');
	});

	it('keeps a tab with nothing behind it, and draws no count on it', () => {
		/* The layout has to be the same shape on every file: a strip that loses a tab when a file
		   has no history is a strip whose words move under the hand. A zero is not worth reading
		   on every tab, so the word stands alone. */
		const drawn = render({ tabs: [...THREE.slice(0, 2), { id: 'p', label: 'P', count: 0 }] });

		expect(drawn.triggers()).toHaveLength(3);
		expect(drawn.triggers()[2].querySelector('.count')).toBeNull();
		expect(drawn.triggers()[2].textContent?.trim()).toBe('P');
		expect(drawn.triggers()[2].disabled).toBe(false);
	});

	it('ties each tab to the pane it opens', () => {
		// Neither id is anything a caller could know, which is why this component owns both halves.
		const drawn = render();
		const controls = drawn.triggers()[0].getAttribute('aria-controls');

		expect(controls).toBeTruthy();
		expect(document.getElementById(controls!)?.getAttribute('aria-labelledby')).toBe(
			drawn.triggers()[0].id
		);
	});
});

describe('opening one', () => {
	it('shows one pane at a time and tells the caller which', () => {
		const onchange = vi.fn();
		const drawn = render({ onchange });

		expect(drawn.showing()).toEqual(['The about pane']);

		drawn.press('Media');

		expect(drawn.showing()).toEqual(['The media pane']);
		expect(onchange).toHaveBeenCalledWith('media');
	});

	it('does not open a tab that is arrowed past', () => {
		/* The whole reason activation is manual. A pane can cost a request, and arrowing from About
		   to Media would fetch whatever Provenance holds on the way. */
		const onchange = vi.fn();
		const drawn = render({ onchange });
		drawn.triggers()[0].focus();

		drawn
			.triggers()[0]
			.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
		flushSync();

		expect(document.activeElement).toBe(drawn.triggers()[1]);
		expect(drawn.showing()).toEqual(['The about pane']);
		expect(onchange).not.toHaveBeenCalled();
	});

	it('opens the focused tab on Enter', () => {
		// The other half of the branch above: manual has to mean "on the press", not "never".
		const drawn = render();
		drawn.triggers()[1].focus();

		drawn
			.triggers()[1]
			.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
		flushSync();

		expect(drawn.showing()).toEqual(['The media pane']);
	});

	it('will not open a tab that is disabled', () => {
		const onchange = vi.fn();
		const drawn = render({
			tabs: [THREE[0], { ...THREE[1], disabled: true }, THREE[2]],
			onchange
		});

		drawn.press('Media');

		expect(drawn.showing()).toEqual(['The about pane']);
		expect(onchange).not.toHaveBeenCalled();
	});

	it('follows the value it is given', () => {
		// Bindable in both directions: the record panel sets it from what the account was last
		// reading, which happens after the strip is already on screen.
		const drawn = render();

		drawn.props.value = 'provenance';
		flushSync();

		expect(drawn.showing()).toEqual(['The provenance pane']);
	});
});

describe('the travel', () => {
	it('lays every pane out in a row and moves the row to the one showing', () => {
		/* The whole of the carousel, and it is checked as a POSITION rather than as an animation:
		   what makes About to History pass Media is that the three are side by side and the row
		   moves two panels, not that something faded. */
		const drawn = render();

		expect(drawn.panes().map((one) => one.textContent?.trim())).toEqual([
			'The about pane',
			'The media pane',
			'The provenance pane'
		]);
		/* And NONE of them carries the library's `hidden`, which is the half that makes it a row
		   rather than three stacked panes: `hidden` takes a box out of the layout altogether, so two
		   of the three would have no width and there would be nothing to travel past. Being in the
		   document is not the same as being laid out, and only this line tells the two apart. */
		expect(drawn.panes().some((one) => one.hasAttribute('hidden'))).toBe(false);
		expect(drawn.at()).toBe('0');

		drawn.props.value = 'provenance';
		flushSync();

		expect(drawn.at()).toBe('2');
	});

	it('takes the panes that are not showing out of reach and out of what is read out', () => {
		/* The property the library's `hidden` was carrying, kept by other means. See the component.
		   Inert is what makes `aria-hidden` honest: announcing nothing while still being focusable is
		   the one combination worse than neither. */
		const drawn = render();

		const marks = drawn
			.panes()
			.map((one) => [one.hasAttribute('inert'), one.getAttribute('aria-hidden')]);

		expect(marks).toEqual([
			[false, null],
			[true, 'true'],
			[true, 'true']
		]);
	});

	it('spends a panel of time on every panel it crosses', () => {
		/*
		 * The row must visibly pass through Media: one duration per panel crossed is one speed, and
		 * the count is the only part a stylesheet cannot work out.
		 */
		const drawn = render();

		drawn.props.value = 'media';
		flushSync();

		expect(drawn.panesCrossed()).toBe('1');

		drawn.props.value = 'about';
		flushSync();
		drawn.props.value = 'provenance';
		flushSync();

		expect(drawn.at()).toBe('2');
		expect(drawn.panesCrossed()).toBe('2');
	});

	it('leaves the window at the start for a value naming no tab', () => {
		// A caller between two lists, or one that has not chosen yet: the row does not travel off
		// its own end.
		const drawn = render({ value: 'nothing-here' });

		expect(drawn.at()).toBe('0');
	});

	it('lifts ONE segment of the strip and moves that one, rather than lighting another', () => {
		/*
		 * One highlight that travels: a ground each tab turns on and off cannot pass anything,
		 * while the panes underneath visibly pass Media. Exactly one highlight is the property,
		 * since three grounds fading into one another would look the same on a still and say
		 * nothing about travel. It moves on the same two numbers the panes do, so it is over Media
		 * when Media's pane is in the window.
		 */
		const drawn = render();

		expect(drawn.pills()).toHaveLength(1);
		// Inside the strip, so the strip's own box is what its travel is measured against.
		expect(drawn.pills()[0].closest('[role="tablist"]')).not.toBeNull();
		// And announced to nobody: `aria-selected` on the tab already says which one is showing.
		expect(drawn.pills()[0].getAttribute('aria-hidden')).toBe('true');

		drawn.press('Provenance');

		expect(drawn.pills()).toHaveLength(1);
		expect(drawn.at()).toBe('2');
		expect(drawn.panesCrossed()).toBe('2');
	});

	it('draws no highlight at all while no tab is the one showing', () => {
		/* The window has to be somewhere and sits at the start; the highlight does not have to be
		   anywhere, and one under the first tab would say that tab is chosen while the value names
		   none of them. */
		const drawn = render({ value: 'nothing-here' });

		expect(drawn.pills()).toHaveLength(0);
	});

	it('tells the strip how many tabs are sharing it out', () => {
		/* jsdom lays nothing out, so the pill's width cannot be read here: what can is the one
		   number the arithmetic needs from outside the stylesheet. Without it the calculation has
		   no divisor at all, which is a highlight of no width or of the whole strip rather than of
		   one tab. */
		const drawn = render();
		const strip = host.querySelector('[role="tablist"]') as HTMLElement;

		expect(strip.style.getPropertyValue('--tabs')).toBe('3');
		expect(drawn.pills()[0].parentElement).toBe(strip);
	});
});

describe('how tall the window is', () => {
	/*
	 * jsdom lays nothing out (every box is zero high and its `ResizeObserver` reports nothing)
	 * so the three heights are handed to the component the only way they can be: by answering the
	 * measurement itself. What is under test is which of the three answers it keeps.
	 */
	function paneHeights(by: Record<string, number>) {
		return vi.spyOn(Element.prototype, 'getBoundingClientRect').mockImplementation(function (
			this: Element
		) {
			const found = Object.keys(by).find((id) => this.querySelector(`[data-pane="${id}"]`));
			return { height: found === undefined ? 0 : by[found] } as DOMRect;
		});
	}

	it('is the tallest pane, and does not move when another tab is opened', () => {
		/*
		 * The strip sits in the middle of a scrolling page, so a window sized to the active pane
		 * would move everything below it on every press, pulling the popout out from under whoever
		 * was reading it. Asserted across all three tabs, because the tallest being the one that
		 * happens to be open is how this would pass by accident.
		 */
		const answering = paneHeights({ about: 120, media: 300, provenance: 40 });
		try {
			const drawn = render();

			expect(drawn.height()).toBe('300px');

			drawn.press('Media');
			expect(drawn.height()).toBe('300px');

			drawn.press('Provenance');
			expect(drawn.height()).toBe('300px');
		} finally {
			answering.mockRestore();
		}
	});

	it('leaves out a pane that says it scrolls, and caps that pane at the rest', () => {
		/*
		 * A history is however many things have happened, while About and Media are fixed lists of
		 * fields, so a long history must not make the box hundreds of rows tall. Asserted on the
		 * pane as well as the window: leaving it out of the measurement only stops it deciding the
		 * height, and being bounded by that height is the other half, or the pane paints past the
		 * window instead of scrolling inside it.
		 */
		const answering = paneHeights({ about: 120, media: 300, provenance: 4000 });
		try {
			const drawn = render({
				tabs: [THREE[0], THREE[1], { ...THREE[2], scrolls: true }]
			});

			expect(drawn.height()).toBe('300px');
			expect(
				[...host.querySelectorAll('.pane')].map((one) => one.classList.contains('bounded'))
			).toEqual([false, false, true]);
		} finally {
			answering.mockRestore();
		}
	});

	it('measures every pane where every one of them scrolls', () => {
		/*
		 * When every pane scrolls, nothing is left to cap them at, so the tallest of what there is
		 * is used; without this the window would be nought pixels high and the control would
		 * vanish.
		 */
		const answering = paneHeights({ about: 120, media: 300, provenance: 40 });
		try {
			const drawn = render({ tabs: THREE.map((one) => ({ ...one, scrolls: true })) });

			expect(drawn.height()).toBe('300px');
		} finally {
			answering.mockRestore();
		}
	});

	it('keeps the laid-out height of a pane that was measured while it was drawn smaller', () => {
		/*
		 * The pop-out player arrives scaled from 0.88 to 1, and the box as DRAWN is what
		 * `getBoundingClientRect` answers, so a record landing mid-arrival would be measured 12%
		 * short. A transform moves no layout, so nothing would measure it again and the window
		 * would clip the panel's last row. The observer's `borderBoxSize` is the layout box
		 * whatever is scaling it, and it has to win over the drawn answer the first frame had to
		 * use.
		 */
		const answering = paneHeights({ about: 264, media: 100, provenance: 40 });
		const Observer = globalThis.ResizeObserver;
		const callbacks: ResizeObserverCallback[] = [];
		globalThis.ResizeObserver = class {
			constructor(callback: ResizeObserverCallback) {
				callbacks.push(callback);
			}
			observe() {}
			unobserve() {}
			disconnect() {}
		} as unknown as typeof ResizeObserver;
		try {
			const drawn = render();
			expect(drawn.height()).toBe('264px');

			const about = host.querySelector('[data-pane="about"]')?.closest('.pane');
			const report = [{ target: about, borderBoxSize: [{ blockSize: 300, inlineSize: 800 }] }];
			callbacks.at(-1)?.(report as never, {} as ResizeObserver);
			flushSync();

			expect(drawn.height()).toBe('300px');
		} finally {
			globalThis.ResizeObserver = Observer;
			answering.mockRestore();
		}
	});

	it('says nothing at all until something has been measured', () => {
		/* `auto` is what the stylesheet falls back to, and it is the honest first paint: a window
		   told it is nought pixels tall before the panes exist draws an empty strip with nothing
		   under it. */
		const drawn = render({ tabs: [] });

		expect(drawn.height()).toBe('');
	});
});

describe('the segmented look', () => {
	afterEach(() => removeStyles());

	/* The strip read off the stylesheet, as the eye sees it: the track's ground, and what is
	   drawn under the tab showing. */
	function strip(look?: 'segmented') {
		render({ size: 'panel', ...(look ? { look } : {}) });
		const root = host.querySelector('.panes') as HTMLElement;
		applyStyles(source, root);
		const track = host.querySelector('[role="tablist"]') as HTMLElement;
		const tab = host.querySelector('[role="tab"]') as HTMLElement;
		return { track, tab, pill: host.querySelector('.pill'), rule: host.querySelector('.rule') };
	}

	it('sinks the strip into a track and lifts the tab showing on a segment', () => {
		const drawn = strip('segmented');

		expect(drawn.rule).toBeNull();
		expect(drawn.pill?.getAttribute('aria-hidden')).toBe('true');
		expect(getComputedStyle(drawn.track).background).toBe('var(--sift-surface-1)');
		expect(getComputedStyle(drawn.pill as Element).background).toBe('var(--sift-surface-4)');
		// Positioned, so the words paint over the segment travelling under them.
		expect(getComputedStyle(drawn.tab).position).toBe('relative');
	});

	it('leaves every other tab row on the rule', () => {
		const drawn = strip();

		expect(drawn.pill).toBeNull();
		expect(drawn.rule).not.toBeNull();
		expect(getComputedStyle(drawn.track).background).toBe('');
	});
});

/*
 * A row of addresses: the rule under the tab showing TRAVELS to the tab opened, one tab's pace per
 * tab crossed, as the File info tabs' segment does. Pinned here, at the primitive, because every
 * entity page and every Organize screen draws this row: a caller cannot opt out, and none has to
 * opt in.
 */
describe('the travelling rule on a row of addresses', () => {
	const PAGES = ['files', 'sets', 'loops', 'tags', 'sites'].map((id) => ({
		id,
		label: id,
		href: `/x?show=${id}`
	}));

	function row(current: string) {
		host = document.createElement('div');
		document.body.append(host);
		const props = reactiveProps({ tabs: PAGES, current, label: 'What to show' });
		mounted = mount(Tabs, { target: host, props });
		flushSync();
		const under = () => host.querySelector<HTMLElement>('.under');
		return { props, under };
	}

	it('crosses the tabs between, at one pace per tab', () => {
		const { props, under } = row('files');
		expect(under(), 'no rule drawn under the tab showing').not.toBeNull();
		expect(under()!.style.getPropertyValue('--crossed')).toBe('0');

		props.current = 'tags';
		flushSync();
		expect(under()!.style.getPropertyValue('--crossed')).toBe('3');
	});

	it('moves with its tab when the tab changes width inside a row that does not', () => {
		/* The typeface arrives after the first measurement and narrows every word; the row keeps
		   its width, so only a tab's own report can say the lit one moved. */
		const Observer = globalThis.ResizeObserver;
		const watched: { callback: ResizeObserverCallback; targets: Element[] }[] = [];
		globalThis.ResizeObserver = class {
			private readonly own: { callback: ResizeObserverCallback; targets: Element[] };
			constructor(callback: ResizeObserverCallback) {
				this.own = { callback, targets: [] };
				watched.push(this.own);
			}
			observe(target: Element) {
				this.own.targets.push(target);
			}
			unobserve() {}
			disconnect() {}
		} as unknown as typeof ResizeObserver;
		try {
			const { under } = row('tags');
			const lit = host.querySelector<HTMLElement>('.tab.here')!;
			const watching = watched.find((one) => one.targets.includes(lit));
			expect(watching, 'nothing watches the tab showing').toBeDefined();
			for (const tab of host.querySelectorAll('.tab')) expect(watching!.targets).toContain(tab);

			/* The first report is the only one when the typeface lands between the measurement and
			   the observer's first look, so it is read like any other. */
			Object.defineProperty(lit, 'offsetLeft', { configurable: true, value: 40 });
			watching!.callback([], {} as ResizeObserver);
			flushSync();
			expect(under()!.style.getPropertyValue('--x')).toBe('40px');
			expect(under()!.style.getPropertyValue('--crossed')).toBe('0');
		} finally {
			globalThis.ResizeObserver = Observer;
		}
	});

	it('travels from where the last copy of the row left it, for a row drawn afresh', async () => {
		/* A person's page draws its strip again for every tab it opens. */
		row('files');
		unmount(mounted!);
		mounted = null;
		host.remove();

		const { under } = row('sites');
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
		expect(under()!.style.getPropertyValue('--crossed')).toBe('4');
	});
});

/*
 * How long a move takes. One `--dur-fast` per tab crossed would make the first tab to the eighth
 * nearly a second, during which the row says the old tab is still showing. The pace is one rule for
 * everything in the row that travels, longer for a longer move and never past `--dur-slow`.
 */
describe('the pace of a move', () => {
	const pace = source.slice(
		source.indexOf('--pace: calc('),
		source.indexOf('.under {\n\t\t--crossed-tabs')
	);

	it('is one rule for the rule, the pill and the rail', () => {
		expect(source).toMatch(/\.under,\s*\.rule,\s*\.pill,\s*\.rail \{\s*--pace: calc\(/);
		expect(source.match(/var\(--pace\) var\(--ease\)/g)).toHaveLength(5);
	});

	it('grows with the distance and stops at the slow duration', () => {
		expect(pace).toContain('var(--dur-fast) + (var(--crossed-tabs) - 1) * var(--dur-instant) / 2');
		expect(pace).toContain('var(--dur-slow)');
	});

	it('is nothing for a move of nothing', () => {
		expect(pace).toContain('min(var(--crossed-tabs), 1) *');
	});

	it('does not multiply a whole duration by the tabs crossed', () => {
		expect(source).not.toMatch(/calc\(var\(--(?:crossed|panes)[^)]*\) \* var\(--dur-fast\)\)/);
	});
});
