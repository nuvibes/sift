/* The bar across the top, and the rule about what may be on it.
 *
 * Only what spans every screen. Search is a way into the whole library, the vault control is also
 * the panic button and has to be reachable from wherever somebody is standing, and Add is the same
 * action whatever is on screen. Anything belonging to one page sits on that page's own header row,
 * and the test for that is here rather than there: a control creeping back onto this bar is the
 * failure, and this is the file that notices.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount } from 'svelte';
import { words } from '$lib/design/testing.svelte';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import TopBar from './TopBar.svelte';
import topBarSource from './TopBar.svelte?raw';
import searchBoxSource from './SearchBox.svelte?raw';
import screenMenusSource from './ScreenMenus.svelte?raw';
import { vault } from '$lib/shell/vault.svelte';
import { noServerAt } from '../../../test-setup';
import { phoneWidth } from '$lib/components/common/phone-width.svelte';
import { PHONE_SHEET, screenBar } from './screen-bar.svelte';
import { searchBox } from '$lib/search/search.svelte';
import { gridAutoplay } from '$lib/grid/grid.svelte';
import { pageTrail } from './trail.svelte';

/* Left unanswered on purpose: whether Smart Search is ready, read on the way past. */
noServerAt('/api/semantic/available');

const mocks = vi.hoisted(() => ({
	session: {
		isAdmin: true,
		isSignedIn: true,
		viewer: { id: 'u1', username: 'kate', role: 'admin' }
	}
}));
vi.mock('$lib/shell/session.svelte', () => ({ session: mocks.session }));
vi.mock('$app/state', () => ({
	navigating: { to: null, from: null, type: null, complete: null, delta: null, willUnload: false },
	page: { url: new URL('http://localhost/browse'), state: {}, route: { id: '/browse' } }
}));
vi.mock('$app/navigation', () => ({ goto: () => Promise.resolve(), afterNavigate: vi.fn() }));

let host: HTMLElement;

/* The vault control is drawn only once the vault's own state has arrived, which is deliberate:
 * drawn immediately, it would say "hidden items are hidden" and then correct itself, so every
 * reload of an open vault would flash shut. Without that state set here, every test would measure
 * a bar with no vault control on it at all, and two of them assert it IS there. */
beforeEach(() => {
	vault.loaded = true;
});

afterEach(() => {
	mocks.session.isAdmin = true;
	mocks.session.isSignedIn = true;
	vault.loaded = false;
	vault.unlocked = false;
	host?.remove();
});

/* What every control on the bar is called. The icon buttons name themselves through the glyph they
 * hold rather than on the button, so reading the button alone finds nothing at all. */
function named(root: HTMLElement): string[] {
	return [...root.querySelectorAll('button')].map((button) =>
		[
			button.getAttribute('aria-label') ?? '',
			...[...button.querySelectorAll('[aria-label]')].map(
				(inner) => inner.getAttribute('aria-label') ?? ''
			),
			button.textContent?.trim() ?? ''
		].join(' ')
	);
}

function render() {
	host = document.createElement('div');
	document.body.append(host);
	mount(TopBar, { target: host });
	flushSync();
	return host;
}

describe('the account control', () => {
	it('is not on the bar at all', () => {
		/*
		 * One account icon per screen: the sidebar carries a Profile row with that glyph. Sign out
		 * lives on the profile page that row goes to, and locking is Ctrl+L and the Hidden control
		 * beside it.
		 */
		render();

		expect(host.querySelector('.session')).toBeNull();
		expect(named(host).join(' ').toLowerCase()).not.toContain('signed in as');
	});
});

describe('what else is on it', () => {
	it('carries the sidebar toggle, the hidden-items control and Add', () => {
		/*
		 * What the bar draws when the screen under it has published nothing: the sidebar toggle,
		 * search, the vault and Add, plus the screen's own menus.
		 *
		 * The screen's menus live up here because the alternative is every screen growing its own
		 * row (a filter bar in the grid, an order dropdown on People, another on Sites) or one
		 * shared row under this one, which is two bars that both look global with the difference
		 * between them needing to be explained.
		 *
		 * There is no kept-filters trigger. What somebody has kept is kept across every screen, but
		 * reaching for a filter you kept is part of deciding how to filter, so the kept filters
		 * are at the foot of the filter panel.
		 */
		render();

		const joined = named(host).join(' | ').toLowerCase();

		expect(joined).toContain('sidebar');
		expect(joined).not.toContain('saved filters');
		expect(joined).toContain('hidden items');
		expect(joined).toContain('add');

		/*
		 * And the three that act on the screen are drawn too, on a screen that has published
		 * nothing at all.
		 *
		 * Absent there, the bar would be four controls wide on Browse, two on a wall of people and
		 * one on Tags, and the survivors would slide sideways each time to close the gap: learning
		 * where Sort is on one screen would buy nothing on the next. So the bar has one shape and
		 * only the state of a control changes, as the size slider does with `Nothing to resize on
		 * this screen` on it.
		 */
		expect(joined).toContain('filter');
		expect(joined).toContain('sort by');
		expect(joined).toContain('preview');
	});

	it('draws them unavailable, rather than merely drawing them', () => {
		/*
		 * The other half of the rule above, and the half that could rot silently: a bar that drew
		 * Filter and Sort on Settings and left them PRESSABLE would open a panel about files over a
		 * screen that has none. Drawn is not the claim: drawn and visibly refusing is.
		 *
		 * Asked of the real attribute rather than of a class, because `disabled` is what stops the
		 * click, what a screen reader reads out, and what the dimming rule keys off. A test that
		 * checked the opacity would pass on a control that still worked.
		 */
		render();

		/*
		 * The accessible name, not the text.
		 *
		 * These triggers are marks alone, so what is in the button is one ligature and nothing
		 * else. Reading the text would find "" for every control and this test would go green
		 * having compared three empty strings, which is the worst way for it to fail.
		 *
		 * `words` still strips the ligature, for the controls on this bar that do have a label.
		 */
		const nameOf = (one: Element) => (one.getAttribute('aria-label') || words(one)).toLowerCase();

		const off = [...host.querySelectorAll('button')].filter((one) => one.disabled);
		const names = off.map(nameOf);

		expect(names).toContain('filter');
		expect(names).toContain('sort by');

		// And there is no kept-filters trigger to be either: the kept filters are
		// drawn at the foot of the filter panel. Asserted rather than dropped, so its return would be
		// a decision somebody took rather than a line nobody noticed.
		const saved = [...host.querySelectorAll('button')].find((one) =>
			nameOf(one).includes('saved filters')
		);
		expect(saved).toBeUndefined();

		/* The slider is an input rather than a button. Reached by element and not through the button
		   sweep above: looked for among the BUTTONS, "tile size" could never be found or fail, since
		   a range input is never one. */
		const slider = host.querySelector('input[type="range"]');
		expect(slider).not.toBeNull();
		expect((slider as HTMLInputElement).disabled).toBe(true);
	});

	it('leaves the hidden-items control off until the vault has said which way it is', () => {
		/* The control names its own state ("Show hidden items" against "Hide hidden items") so
		 * drawing it before that state arrives means drawing it wrong and then correcting it. On a
		 * reload with the vault open that is a flash of "shut" over something that is not.
		 *
		 * The rest of the bar is unaffected: the gap is one control, not a bar that has not loaded. */
		vault.loaded = false;
		render();

		const joined = named(host).join(' | ').toLowerCase();
		expect(joined).not.toContain('hidden items');
		expect(joined).toContain('sidebar');
		expect(joined).toContain('add');
	});

	it('and offers the hidden-items control to a guest as well', () => {
		// Hiding is personal, so a guest has their own to open and this is what opens it.
		mocks.session.isAdmin = false;
		render();

		expect(named(host).join(' ').toLowerCase()).toContain('hidden items');
	});
});

/*
 * Where the bar's labels open.
 *
 * The bar is the top of the application, and above it is the window's own title strip, which is
 * drawn over everything. A label asked to open above a control on this bar lands under that strip
 * and is cut off. So every label on the bar opens below it; the screen menus open away from
 * whichever edge their row is on, which is below except while a screen fills the window.
 *
 * Read from the source because the label only exists while it is showing, and where it goes is
 * the one prop that says so for every control together, including the ones added later.
 */
describe('the tile size giving way to the field', () => {
	it('is off the bar while the bar says so, and back when it does not', () => {
		render();
		expect(host.querySelector('.size.gone')).toBeNull();
		screenBar.sizeOnBar = false;
		flushSync();
		expect(host.querySelector('.size.gone')).not.toBeNull();
		screenBar.sizeOnBar = true;
		flushSync();
		expect(host.querySelector('.size.gone')).toBeNull();
	});

	it("offers it on the screen's own row while it is off the bar", () => {
		render();
		screenBar.sizeOnBar = false;
		expect(screenBar.tools.panels?.map((one) => one.label)).toEqual(['Tile size']);
		screenBar.sizeOnBar = true;
		expect(screenBar.tools.panels ?? []).toEqual([]);
	});
});

describe('the labels on the bar', () => {
	/** Every `<Tooltip ...>` opening tag in a file, braces respected. */
	function tooltipTags(text: string): string[] {
		const tags: string[] = [];
		for (let at = text.indexOf('<Tooltip'); at !== -1; at = text.indexOf('<Tooltip', at + 1)) {
			let depth = 0;
			let end = at;
			for (; end < text.length; end += 1) {
				const char = text[end];
				if (char === '{') depth += 1;
				else if (char === '}') depth -= 1;
				else if (char === '>' && depth === 0) break;
			}
			tags.push(text.slice(at, end + 1));
		}
		return tags;
	}

	// SearchBox's two modes are the Tabs primitive's icon tabs, whose tooltips Tabs itself
	// opens at the bottom; the box writes no label of its own.
	it.each([
		['TopBar', topBarSource, /placement="bottom"/],
		['ScreenMenus', screenMenusSource, /placement=\{labelSide\}/]
	])('%s opens every label away from the top of the window', (_name, text, side) => {
		const tags = tooltipTags(text);
		expect(tags.length, 'no labels found, so this proves nothing').toBeGreaterThan(0);
		for (const tag of tags) expect(tag, tag).toMatch(side);
	});
});

/*
 * At a phone's width the bar is one line of squares: the search and one control for Filter and Sort
 * at the start, Hidden and Add at the end. The unit environment answers only a plain `screen` media
 * rule, so the phone rules are read with their condition swapped for it; which CONTROLS are drawn
 * is a branch in the markup, on `phone`, and is set directly.
 */
describe("the bar at a phone's width", () => {
	afterEach(() => {
		removeStyles();
		phoneWidth.yes = false;
		screenBar.close();
		// What a screen published stays in the store until another screen replaces it.
		const done = Symbol('done');
		screenBar.publish(done, {});
		screenBar.release(done);
	});

	it("packs the squares that decide the screen at the start and the window's at the end", () => {
		render();
		const bar = host.querySelector('.topbar') as HTMLElement;
		const media = '@media (max-width: 767px)';
		expect(topBarSource, 'the bar has no phone rule').toContain(media);
		applyStyles(topBarSource.replaceAll(media, '@media screen'), bar);

		expect(getComputedStyle(bar).gridTemplateColumns).toBe('max-content minmax(0, 1fr)');
		expect(getComputedStyle(host.querySelector('.lead') as HTMLElement).justifyContent).toBe(
			'flex-start'
		);
		expect(getComputedStyle(host.querySelector('.actions') as HTMLElement).justifyContent).toBe(
			'flex-end'
		);
		// The rail is off the screen and there is no hover: neither control has a job here.
		expect(getComputedStyle(host.querySelector('.collapse') as HTMLElement).display).toBe('none');
		expect(getComputedStyle(host.querySelector('.preview') as HTMLElement).display).toBe('none');
	});

	it('draws a search square in place of the field, which opens the search the shortcut opens', () => {
		/*
		 * The field squeezed beside the squares would come out about 72px wide with its words cut at
		 * three letters. Drawn in place rather than beside a hidden field: a field hidden by a stylesheet
		 * is a second search box in the tab order.
		 */
		phoneWidth.yes = true;
		render();

		expect(host.querySelector('.search'), 'the field is still on the bar').toBeNull();
		const search = host.querySelector('button[aria-label="Search"]') as HTMLButtonElement;
		expect(search, 'no search square').not.toBeNull();
		const before = searchBox.sheetWanted;
		search.click();
		flushSync();
		expect(searchBox.sheetWanted).toBe(before + 1);
	});

	it('draws Filter and Sort as one control that opens the sheet, and no pair', () => {
		phoneWidth.yes = true;
		screenBar.publish(Symbol('wall'), {
			filterable: true,
			sorts: [{ value: 'newest', label: 'Newest first' }]
		});
		render();

		expect(host.querySelector('button[aria-label="Filter"]'), 'the pair is still drawn').toBeNull();
		const both = host.querySelector('button[aria-label="Filter and sort"]') as HTMLButtonElement;
		expect(both.disabled).toBe(false);
		both.click();
		flushSync();
		expect(screenBar.open).toBe(PHONE_SHEET);
		expect(both.getAttribute('aria-expanded')).toBe('true');
	});

	it('opens the sheet on a screen that orders and does not filter', () => {
		/* The sheet holds the orders too, so a screen with orders and no facets still has one. */
		phoneWidth.yes = true;
		screenBar.publish(Symbol('wall'), {
			filterable: 'Nothing to filter here',
			sorts: [{ value: 'newest', label: 'Newest first' }]
		});
		render();

		const both = host.querySelector('button[aria-label="Filter and sort"]') as HTMLButtonElement;
		expect(both.disabled).toBe(false);
	});

	it("dims the control, with the screen's reason, where it neither filters nor orders", () => {
		phoneWidth.yes = true;
		screenBar.publish(Symbol('settings'), { filterable: 'Nothing to filter in Settings' });
		render();

		const both = host.querySelector('button[aria-label="Filter and sort"]') as HTMLButtonElement;
		expect(both.disabled).toBe(true);
		expect(topBarSource).toContain('whyNot(screenBar.tools.filterable, NOT_HERE.filter)');
	});

	it('keeps the search field on one line with its mode pair, taking what the pair leaves', () => {
		render();
		const media = '@media (max-width: 767px)';
		const box = host.querySelector('.search') as HTMLElement;
		applyStyles(searchBoxSource.replaceAll(media, '@media screen'), box);

		const fields = host.querySelector('.fields') as HTMLElement;
		expect(getComputedStyle(fields).flexWrap).toBe('nowrap');
		const field = host.querySelector('.text-input') as HTMLElement;
		expect(getComputedStyle(field).flexGrow).toBe('1');
		expect(getComputedStyle(field).minInlineSize).toBe('0px');
	});

	it('leaves every control in its row on a desktop window', () => {
		render();
		applyStyles(topBarSource, host.querySelector('.topbar'));
		expect(getComputedStyle(host.querySelector('.collapse') as HTMLElement).display).toBe(
			'contents'
		);
	});
});

/*
 * Filter, Sort by and the search box are one centre group between two equal ends, so its midpoint
 * is the bar's; the ends pack against it, with the collapse and Add at the edges.
 */
describe('the centre group and the two ends', () => {
	afterEach(() => {
		removeStyles();
		screenBar.barEnd = 0;
	});

	it('draws the menus and the search box as one group between equal ends', () => {
		render();
		const bar = host.querySelector('.topbar') as HTMLElement;
		applyStyles(topBarSource, bar);

		const centre = bar.querySelector(':scope > .centre') as HTMLElement;
		expect(centre.querySelector('[role="group"][aria-label="What is on screen"]')).not.toBeNull();
		expect(centre.querySelector('form.search')).not.toBeNull();
		expect(
			bar.querySelector('.lead [role="group"]'),
			'the menus are still at the start'
		).toBeNull();
		expect(getComputedStyle(bar).gridTemplateColumns).toBe(
			'minmax(max-content, 1fr) auto minmax(max-content, 1fr)'
		);
	});

	it('caps the group by the end group, so each end has room for it', () => {
		screenBar.barEnd = 312;
		render();
		const bar = host.querySelector('.topbar') as HTMLElement;
		expect(bar.style.getPropertyValue('--bar-end')).toBe('312px');
		expect(bar.style.getPropertyValue('--field-floor')).toBe('227px');
		applyStyles(topBarSource, bar);
		const cap = getComputedStyle(bar.querySelector('.centre') as HTMLElement).maxInlineSize;
		expect(cap.replace(/\s+/g, ' ')).toBe(
			'max(var(--field-floor), 100cqi - 2 * (var(--bar-end) + var(--space-3)))'
		);
	});

	it('packs the ends against the group, the collapse and Add at the edges', () => {
		render();
		const bar = host.querySelector('.topbar') as HTMLElement;
		applyStyles(topBarSource, bar);

		// The trail has no floor of its own and never sizes its end: it folds into what is left.
		const trail = getComputedStyle(host.querySelector('.trail') as HTMLElement);
		expect(trail.minInlineSize).toBe('0px');
		expect(trail.contain).toBe('inline-size');
		expect(getComputedStyle(host.querySelector('.actions') as HTMLElement).justifyContent).toBe(
			'flex-start'
		);
		const trailing = host.querySelector('.trailing') as HTMLElement;
		expect(getComputedStyle(trailing).marginInlineStart).toBe('auto');
		// The end group is measured whole, so neither of its parts may shrink.
		expect(getComputedStyle(trailing).flexShrink).toBe('0');
		expect(getComputedStyle(host.querySelector('.near') as HTMLElement).flexShrink).toBe('0');
		expect(getComputedStyle(bar).getPropertyValue('gap')).toBe('var(--space-3)');
	});
});

/*
 * Hidden holds one place on every screen: straight after the play control, before anything a
 * screen puts up on this bar. Theater puts up its Sound control and its key sheet, and the row
 * reads Play everything, Hidden, Sound, rather than the vault wandering to after them.
 */
describe('the order of the controls about the whole window', () => {
	afterEach(() => {
		const done = Symbol('done');
		screenBar.publish(done, {});
		screenBar.release(done);
	});

	it('draws Hidden between the play control and what the screen puts up', () => {
		const sound = createRawSnippet(() => ({
			render: () => '<button aria-label="Mute everything"></button>'
		}));
		screenBar.publish(Symbol('theater'), {
			playable: true,
			playLabel: 'Play everything',
			topExtra: sound
		});
		render();

		const order = [...(host.querySelector('.actions') as HTMLElement).querySelectorAll('button')]
			.map((button) => button.getAttribute('aria-label') ?? '')
			.filter((label) => /Play everything|hidden items|Mute everything/.test(label));
		expect(order).toEqual(['Play everything', 'Show hidden items', 'Mute everything']);
	});
});

/*
 * The preview control is named for what pressing it will do. Named for the state the tiles are
 * in, the tooltip over the lit control would say "Preview everything visible" while everything
 * visible was already previewing, and read backwards on every press.
 */
describe('the preview control on a wall of files', () => {
	afterEach(() => {
		if (gridAutoplay.mode === 'visible') gridAutoplay.toggle();
		const done = Symbol('done');
		screenBar.publish(done, {});
		screenBar.release(done);
	});

	it('offers everything visible from hover, and hover back once pressed', () => {
		screenBar.publish(Symbol('browse'), { playable: true });
		render();
		const names = () => named(host).filter((name) => /^Preview /.test(name));

		expect(names()[0]).toMatch(/^Preview everything visible/);

		gridAutoplay.toggle();
		flushSync();

		expect(names()[0], 'the lit control still named the state it was in').toMatch(
			/^Preview on hover/
		);
	});
});

describe("the screen's trail, on the search box's line", () => {
	/* A frame says its trail and the bar draws it, so a trailed screen starts on the same line as
	   every other. On a phone the bar is a row of squares and the frame draws the trail instead. */
	const owner = Symbol('a frame');

	afterEach(() => {
		pageTrail.unsay(owner);
		phoneWidth.yes = false;
		screenBar.sizeOnBar = true;
	});

	it('draws the trail the frame said, the current page last and not a link', () => {
		pageTrail.say(owner, [{ label: 'People', href: '/people' }, { label: 'Somebody' }]);
		const bar = render();

		const trail = bar.querySelector('.lead nav[aria-label="Breadcrumb"]');
		expect(trail, 'no trail in the bar').not.toBeNull();
		expect(trail!.querySelector('a')?.textContent?.trim()).toBe('People');
		expect(trail!.querySelector('[aria-current="page"]')?.textContent?.trim()).toBe('Somebody');
	});

	it('folds it to the press alone before the tile size leaves', () => {
		pageTrail.say(owner, [{ label: 'People', href: '/people' }, { label: 'Somebody' }]);
		const bar = render();
		screenBar.sizeOnBar = false;
		flushSync();

		const trail = bar.querySelector('.lead nav[aria-label="Breadcrumb"]')!;
		expect(trail.querySelector('a, [aria-current="page"]')).toBeNull();
		expect(trail.querySelector('button.fold')).not.toBeNull();
	});

	it('keeps the room on a screen with no trail, so the bar keeps one shape', () => {
		const bar = render();
		expect(bar.querySelector('.lead .trail')).not.toBeNull();
		expect(bar.querySelector('nav[aria-label="Breadcrumb"]')).toBeNull();
	});

	it('draws no trail on a phone, where the page draws it', () => {
		phoneWidth.yes = true;
		pageTrail.say(owner, [{ label: 'People', href: '/people' }, { label: 'Somebody' }]);
		const bar = render();
		expect(bar.querySelector('nav[aria-label="Breadcrumb"]')).toBeNull();
	});
});
