/*
 * The one door every menu in the application opens through, and the four shapes it takes.
 *
 * It serves the Jobs screen's bulk actions without pretending they are file verbs, and takes a
 * trigger snippet so a `SplitButton`'s trailing half can be the door while staying the shared
 * Button.
 *
 * What is pinned is what each caller depends on and what would be silent if it went:
 *
 * - three dots at the end of a row, words where the door is not at the end of one, and neither when
 *   the caller draws its own trigger;
 * - the name, on the button and on the menu, since twenty rows all announced as "More" is the fault
 *   the `label` prop exists to end;
 * - `open` bindable, because a Settings folder row opens this door from a press on the row;
 * - `disabled`, because a row being rebuilt must not offer verbs against the old one.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { createRawSnippet } from 'svelte';
import { reactiveProps, words as readable } from '$lib/design/testing.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { WHOLE_MENU } from './menu-whole';

import Button from './Button.svelte';
import buttonSource from './Button.svelte?raw';
import contextMenuSource from './ContextMenu.svelte?raw';
import MenuButton from './MenuButton.svelte';
import source from './MenuButton.svelte?raw';

let host: HTMLElement;

afterEach(() => {
	host?.remove();
	// The menu is portalled to the end of the document, so removing the host leaves it behind.
	document.body.innerHTML = '';
});

const rows = createRawSnippet(() => ({ render: () => '<span>a row</span>' }));

function draw(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	const reactive = reactiveProps({
		label: 'More for orla',
		open: false,
		children: rows,
		...props
	});
	mount(MenuButton, { target: host, props: reactive });
	flushSync();
	return reactive;
}

function door(): HTMLButtonElement {
	const found = host.querySelector('button');
	if (!found) throw new Error('there is no door');
	return found;
}

/* The library opens on POINTERDOWN, not on click: sending both is one press that opens and
   closes again, which reads in an assertion as a door that never opened. */
function press(button: HTMLElement) {
	button.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	flushSync();
}

describe('the door', () => {
	it('is three dots at the end of a row, named for whose row it is', () => {
		draw();

		expect(door().getAttribute('aria-label')).toBe('More for orla');
		/* An icon here is a LIGATURE (the glyph is a private-use character in the element's own
		   text) so `readable` takes it out and what is left is the words. There are none. */
		expect(readable(door()), 'the glyph door grew words').toBe('');
	});

	it('names the three dots in a tooltip as well as out loud', async () => {
		draw();

		door()
			.closest('.wrap')
			?.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
		await vi.waitFor(() =>
			expect(document.querySelector('[role="tooltip"]')?.textContent?.trim()).toBe('More for orla')
		);
	});

	it('wears words where a floating glyph would say nothing', () => {
		/* A file's own screen, an entity header, the Jobs screen: three dots beside a named button
		   says nothing about what is behind them. */
		draw({ words: 'Options' });

		expect(door().textContent).toContain('Options');
	});

	it('says it opens a menu', () => {
		draw();
		expect(door().getAttribute('aria-haspopup')).toBe('menu');
	});

	it('offers nothing while it is unavailable', () => {
		/* A row being rebuilt must not offer verbs against the row it was. */
		draw({ disabled: true });
		expect(door().disabled).toBe(true);
	});
});

describe('a trigger the caller draws', () => {
	it('uses the caller-s element and draws no door of its own', () => {
		/* The `SplitButton` case: the trailing half has to be the shared Button at the pair's own
		   tone and size, or the two halves stop reading as one control. */
		const trigger = createRawSnippet<[{ props: Record<string, unknown> }]>(() => ({
			render: () => '<button type="button" data-mine="yes">mine</button>'
		}));
		draw({ trigger });

		const buttons = host.querySelectorAll('button');
		expect(buttons).toHaveLength(1);
		expect(buttons[0].getAttribute('data-mine')).toBe('yes');
	});
});

describe('opening it', () => {
	it('tells a caller holding the state each time it opens and closes', () => {
		/* A list of rows keeping one menu open at a time by id reads this rather than binding, so a
		   door that opened without saying so would leave the previous row-s menu up beside it. */
		const onOpenChange = vi.fn();
		draw({ onOpenChange });

		press(door());
		expect(onOpenChange.mock.calls.map(([open]) => open)).toEqual([true]);

		press(door());
		expect(onOpenChange.mock.calls.map(([open]) => open)).toEqual([true, false]);
	});

	it('hands the open state back to a caller that bound it', () => {
		/* A library folder in Settings has no screen of its own to go to, so a plain press anywhere
		   on the row opens this, and the row has to be told when it closes, or it would think it
		   is still open for ever. */
		const props = draw();

		press(door());

		expect(props.open, 'the caller was never told it opened').toBe(true);
	});

	it('opens nothing while it is unavailable', () => {
		const onOpenChange = vi.fn();
		draw({ disabled: true, onOpenChange });

		press(door());

		expect(onOpenChange).not.toHaveBeenCalled();
	});
});

describe('the menu it opens', () => {
	/*
	 * Every floating menu in the app is `.ui-menu`, and that surface has a ceiling so a long menu
	 * does not run off the window. A ceiling alone would only clip the rows, so they go in the
	 * shared scrolling region: the two halves are one change.
	 */
	it('puts its rows in the shared scrolling region', () => {
		draw();
		press(door());

		const menu = document.querySelector('.ui-menu');
		expect(menu, 'the menu never opened').toBeTruthy();
		expect(menu?.querySelector('.scroll-root'), 'the rows are not in a Scroller').toBeTruthy();
		expect(menu?.textContent).toContain('a row');
	});
});

describe('the three dots', () => {
	afterEach(removeStyles);

	it("are a small square, and another file's `more` button is not made one", () => {
		draw();
		applyStyles(source);

		/* The facet panel's "View more" is the shared Button handed `more`, and the search box's
		   "Show more" row is a button wearing it too. Neither is the door. */
		const panel = document.createElement('div');
		document.body.append(panel);
		mount(Button, {
			target: panel,
			props: {
				tone: 'quiet',
				size: 'small',
				class: 'more',
				children: createRawSnippet(() => ({ render: () => '<span>View 4 more</span>' }))
			}
		});
		flushSync();
		const viewMore = panel.querySelector('button') as HTMLElement;
		applyStyles(buttonSource, viewMore);

		const dots = getComputedStyle(door());
		expect(dots.blockSize).toBeTruthy();
		expect(dots.inlineSize).toBe('2rem');
		expect(getComputedStyle(viewMore).blockSize).not.toBe(dots.blockSize);
		expect(getComputedStyle(viewMore).inlineSize).not.toBe('2rem');
	});
});

describe('where the menu opens', () => {
	/* A short window: the library reads the viewport from the root element's client box. */
	const WINDOW = { width: 1600, height: 1000 };

	beforeEach(() => {
		const root = document.documentElement;
		Object.defineProperty(root, 'clientWidth', { configurable: true, value: WINDOW.width });
		Object.defineProperty(root, 'clientHeight', { configurable: true, value: WINDOW.height });
	});

	afterEach(() => {
		removeStyles();
		const root = document.documentElement;
		delete (root as unknown as Record<string, unknown>).clientWidth;
		delete (root as unknown as Record<string, unknown>).clientHeight;
	});

	async function opened(props: Record<string, unknown> = {}): Promise<HTMLElement> {
		draw(props);
		press(door());
		let menu: HTMLElement | null = null;
		await vi.waitFor(
			() => {
				menu = document.querySelector<HTMLElement>(
					'[data-bits-floating-content-wrapper] > .ui-menu'
				);
				expect(menu, 'the menu never opened').toBeTruthy();
				const room = menu?.parentElement?.style.getPropertyValue(
					'--bits-floating-available-height'
				);
				expect(room).toMatch(/^\d+px$/);
			},
			{ timeout: 5000 }
		);
		applyStyles(contextMenuSource);
		return menu as unknown as HTMLElement;
	}

	it('opens whole like the right-click menu, taking the room the library measures as its ceiling', async () => {
		/* The same placement the right-click menu has, not a second copy of it: the room is the
		   window's less the shared padding, and the ceiling is that room rather than the shared
		   list ceiling, so a long menu flips upward and shows every row instead of scrolling. */
		const menu = await opened();
		const room = menu.parentElement?.style.getPropertyValue('--bits-floating-available-height');

		expect(menu.classList.contains('whole'), 'the door opens at the shared list ceiling').toBe(
			true
		);
		// The window less the shared padding at both ends, which the library keeps clear.
		expect(room).toBe(`${WINDOW.height - 2 * WHOLE_MENU.collisionPadding}px`);
		expect(getComputedStyle(menu).getPropertyValue('max-block-size')).toBe(
			'var(--bits-floating-available-height, var(--menu-max-height))'
		);
	});

	it('keeps the shared ceiling for a chooser that scrolls its own list', async () => {
		/* A composed picker lists the library's things, as many as there are. */
		const menu = await opened({ scrolls: false });

		expect(menu.classList.contains('whole')).toBe(false);
		expect(menu.classList.contains('ui-menu')).toBe(true);
	});
});

describe('which side a chooser opens on', () => {
	/* The side is the press's, read off the window when it opens. A chooser's rows arrive after it
	   is placed, and its surface is capped at the room on the side it took, so the library's own
	   flip never sees the list it is placing: Choose person near the window's foot would open
	   downwards with two rows showing and the whole screen above it free. */
	const HEIGHT = 1000;
	let was: number;

	beforeEach(() => {
		was = window.innerHeight;
		Object.defineProperty(window, 'innerHeight', { configurable: true, value: HEIGHT });
		/* The library reads the viewport from the root element's client box. */
		const root = document.documentElement;
		Object.defineProperty(root, 'clientWidth', { configurable: true, value: 1600 });
		Object.defineProperty(root, 'clientHeight', { configurable: true, value: HEIGHT });
	});

	afterEach(() => {
		Object.defineProperty(window, 'innerHeight', { configurable: true, value: was });
		const root = document.documentElement;
		delete (root as unknown as Record<string, unknown>).clientWidth;
		delete (root as unknown as Record<string, unknown>).clientHeight;
	});

	async function sideAt(top: number, props: Record<string, unknown> = {}): Promise<string> {
		draw({ scrolls: false, ...props });
		const button = door();
		button.getBoundingClientRect = () =>
			({ top, bottom: top + 32, left: 400, right: 500, width: 100, height: 32 }) as DOMRect;
		press(button);
		let side = '';
		await vi.waitFor(
			() => {
				const menu = document.querySelector<HTMLElement>('.ui-menu[data-side]');
				expect(menu, 'the chooser never opened').toBeTruthy();
				/* Placed once the library has measured the room on the side it took. */
				const room = menu?.parentElement?.style.getPropertyValue(
					'--bits-floating-available-height'
				);
				expect(room).toMatch(/^\d+(\.\d+)?px$/);
				side = menu?.dataset.side ?? '';
			},
			{ timeout: 5000 }
		);
		return side;
	}

	it('opens upwards from a press near the foot when there is more room above', async () => {
		expect(await sideAt(785)).toBe('top');
	});

	it('opens downwards from a press with the room for it below', async () => {
		expect(await sideAt(120)).toBe('bottom');
	});

	it('keeps the side a caller asks for', async () => {
		expect(await sideAt(785, { side: 'bottom' })).toBe('bottom');
	});
});
