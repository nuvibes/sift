/*
 * A press outside an open menu closes the menu, and the page under it never hears that press.
 *
 * The browser decides which element a press lands on by hit testing, which jsdom does not do. So
 * what is proved here is the arrangement that makes the browser's answer the shield: one is drawn
 * in the menu's own portal, directly before the menu, covering the window on the menu's layer and
 * taking the pointer whatever the page says. Then the press is delivered where the browser
 * delivers it, and the tile under the menu is asked what it heard.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import source from './PageShield.svelte?raw';
import PageShield from './PageShield.svelte';
import Probe from './PageShieldProbe.test.svelte';

for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	removeStyles();
	document.body.innerHTML = '';
});

const openMenus = () =>
	[...document.querySelectorAll('[role="menu"]')].filter(
		(menu) => menu.getAttribute('data-state') === 'open'
	);

/*
 * Whether the open menu has started listening for a press outside it.
 *
 * The library opens the menu immediately and starts listening a timer's turn later, so a press in
 * between is not heard by anybody. Under load the runner can reach its press inside that turn,
 * which would make these tests fail at random. What is waited for is the library's own register of
 * listening layers, never a length of time.
 */
type Layer = { opts: { ref: { current: Element | null } } };
function listening(): boolean {
	const menu = openMenus()[0];
	const layers = (globalThis as { bitsDismissableLayers?: Map<Layer, unknown> })
		.bitsDismissableLayers;
	return [...(layers?.keys() ?? [])].some((layer) => layer.opts.ref.current === menu);
}

async function openAddTo(): Promise<{ heard: string[]; shield: HTMLElement }> {
	host = document.createElement('div');
	document.body.append(host);
	const heard: string[] = [];
	instance = mount(Probe, { target: host, props: { heard } });
	flushSync();
	const door = [...host.querySelectorAll('button')].find((one) =>
		one.textContent?.includes('Add to')
	);
	door?.click();
	await vi.waitFor(() => expect(openMenus()).toHaveLength(1), { timeout: 5000 });
	await vi.waitFor(() => expect(listening(), 'the menu never listened for a press').toBe(true), {
		timeout: 5000
	});
	const shield = document.querySelector<HTMLElement>('.page-shield');
	expect(shield, 'no shield under the open menu').toBeTruthy();
	return { heard, shield: shield as HTMLElement };
}

/* Somewhere clear of the menu: the library also asks whether the point is outside the menu's box,
   which in a document with no layout is a box of nothing at the origin. */
const AWAY = { bubbles: true, button: 0, clientX: 500, clientY: 500 };

/**
 * A whole press, in the order a hand makes it: the menu closes on the way down.
 *
 * The way down is repeated until the menu has gone. The library registers its listener and then
 * ignores an outside press for its first few milliseconds, a grace its register does not show, so
 * one press can land inside it on a loaded runner. Every repeat lands on the shield and none on
 * the tile, which is the claim under test, and the release and the click happen once.
 */
/** The way down alone, repeated past the library's grace until the menu has gone. */
async function pressDown(target: Element): Promise<void> {
	for (let attempt = 0; attempt < 40 && openMenus().length > 0; attempt += 1) {
		target.dispatchEvent(new MouseEvent('pointerdown', AWAY));
		await new Promise((done) => setTimeout(done, 25));
	}
	expect(openMenus(), 'the menu never closed on a press outside it').toHaveLength(0);
}

async function press(target: Element): Promise<void> {
	await pressDown(target);
	target.dispatchEvent(new MouseEvent('pointerup', AWAY));
	target.dispatchEvent(new MouseEvent('click', AWAY));
}

describe('the shield under an open menu', () => {
	it('is drawn in the menu portal, just before the menu, over the whole window', async () => {
		const { shield } = await openAddTo();

		const menu = openMenus()[0].closest('[data-bits-floating-content-wrapper]') as Element;
		// Before the menu on the same layer, so the menu paints over it and it over the page.
		expect(shield.compareDocumentPosition(menu) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		// Outside the page's own tree: it is not an ancestor or a child of the tile.
		expect(host.contains(shield)).toBe(false);

		applyStyles(source, shield);
		const style = getComputedStyle(shield);
		expect(style.position).toBe('fixed');
		expect(style.pointerEvents).toBe('auto');
		expect(style.zIndex).toBe('var(--z-menu)');
	});

	it('closes the menu on a press outside it, and the tile under it hears nothing', async () => {
		const { heard, shield } = await openAddTo();

		await press(shield);

		expect(openMenus()).toHaveLength(0);
		expect(heard).toEqual([]);
	});

	it('stays until the gesture that closed the menu has ended', async () => {
		/* The menu closes on the press; the release and the click after it belong to the same press,
		   and with the shield gone they would land on the tile and open it. */
		const { shield } = await openAddTo();

		await pressDown(shield);
		flushSync();
		expect(document.querySelector('.page-shield')).toBe(shield);

		window.dispatchEvent(new MouseEvent('pointerup', AWAY));
		shield.dispatchEvent(new MouseEvent('click', AWAY));
		await vi.waitFor(() => expect(document.querySelector('.page-shield')).toBeNull(), {
			timeout: 3000
		});
	});

	it('lets a row of the menu be picked, and the tile still hears nothing', async () => {
		const { heard } = await openAddTo();

		const row = [...document.querySelectorAll('[role="menuitem"]')].find(
			(one) => one.textContent?.trim() === 'Collection'
		) as HTMLElement;
		row.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		row.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
		row.click();

		await vi.waitFor(() => expect(heard).toContain('picked Collection'), { timeout: 3000 });
		expect(heard).not.toContain('tile pressed');
		expect(heard).not.toContain('tile opened');
	});

	it('is drawn under a right-click menu too', async () => {
		host = document.createElement('div');
		document.body.append(host);
		instance = mount(Probe, { target: host, props: { heard: [] } });
		flushSync();

		host
			.querySelector('[data-context-menu-trigger]')
			?.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 10, clientY: 10 }));

		await vi.waitFor(() => expect(openMenus()).toHaveLength(1), { timeout: 5000 });
		expect(document.querySelectorAll('.page-shield')).toHaveLength(1);
	});

	it('is not drawn while no menu is open', () => {
		host = document.createElement('div');
		document.body.append(host);
		instance = mount(Probe, { target: host, props: { heard: [] } });
		flushSync();

		expect(document.querySelector('.page-shield')).toBeNull();
	});
});

/*
 * A finger's tap ends at its click, and a phone's browser raises that click a turn or more after the
 * finger is up. A shield that went at the release would be gone when the click was aimed, and the
 * tap that shut a sheet would press whatever lay under it. The shield alone here, its surface
 * already gone.
 */
describe('the gesture a shield outlives its surface for', () => {
	const turn = () => new Promise((done) => setTimeout(done, 0));

	function shieldPressed(): { props: { up: boolean }; shield: HTMLElement } {
		host = document.createElement('div');
		document.body.append(host);
		const props = $state({ up: true });
		instance = mount(PageShield, { target: host, props });
		flushSync();
		const shield = document.querySelector<HTMLElement>('.page-shield') as HTMLElement;
		// The press that shuts the surface: a sheet closes on the way down.
		shield.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		props.up = false;
		flushSync();
		return { props, shield };
	}

	const standing = () => document.querySelector('.page-shield');

	it('stands from the release until the click that finishes the tap', async () => {
		const { shield } = shieldPressed();

		window.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
		await turn();
		await turn();
		flushSync();
		expect(standing(), 'gone before the tap raised its click').toBe(shield);

		shield.dispatchEvent(new MouseEvent('click', { bubbles: true, detail: 1 }));
		await turn();
		flushSync();
		expect(standing()).toBeNull();
	});

	it('goes immediately when the browser takes the press over for a scroll', async () => {
		shieldPressed();

		window.dispatchEvent(new MouseEvent('pointercancel', { bubbles: true }));
		await turn();
		flushSync();
		expect(standing()).toBeNull();
	});

	it('goes immediately when the press became a hold', async () => {
		const { shield } = shieldPressed();

		shield.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
		await turn();
		flushSync();
		expect(standing()).toBeNull();
	});

	it('goes on its own when the click never comes', async () => {
		vi.useFakeTimers();
		try {
			shieldPressed();
			window.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));

			vi.advanceTimersByTime(400);
			flushSync();
			expect(standing(), 'gone long before a slow tap could click').not.toBeNull();

			vi.advanceTimersByTime(200);
			flushSync();
			expect(standing()).toBeNull();
		} finally {
			vi.useRealTimers();
		}
	});
});

/*
 * A list opened by pointing at a row of triggers still takes a click beside it, and leaves the row
 * itself pressable. Hit testing follows the clip, so the hole is where the pointer reaches the row.
 */
describe('the hole a shield leaves over the element it spares', () => {
	const ROW = { left: 100, top: 10, right: 300, bottom: 46 };

	function spared(): HTMLElement {
		const row = document.createElement('div');
		row.getBoundingClientRect = () =>
			({ ...ROW, x: ROW.left, y: ROW.top, width: 200, height: 36, toJSON: () => ROW }) as DOMRect;
		document.body.append(row);
		return row;
	}

	it('cuts the spared box out of the window, and covers the rest', () => {
		host = document.createElement('div');
		document.body.append(host);
		instance = mount(PageShield, { target: host, props: { up: true, spare: spared() } });
		flushSync();

		const clip = document.querySelector<HTMLElement>('.page-shield')?.getAttribute('style') ?? '';
		expect(clip).toContain('evenodd');
		// The whole window first, then the row's four corners as the ring cut out of it.
		expect(clip).toContain('0 0, 100% 0, 100% 100%, 0 100%');
		expect(clip).toContain('100px 10px, 300px 10px, 300px 46px, 100px 46px');
	});

	it('follows the row when it moves under an open list, with the window the same size', () => {
		/* The sidebar folding away moves the row without resizing the window: what reports it is
		   a change of size in the row or in the column that holds it. */
		const watched: Element[] = [];
		const report: Array<() => void> = [];
		vi.stubGlobal(
			'ResizeObserver',
			class {
				constructor(callback: () => void) {
					report.push(callback);
				}
				observe(box: Element): void {
					watched.push(box);
				}
				unobserve(): void {}
				disconnect(): void {}
			}
		);
		try {
			const column = document.createElement('main');
			document.body.append(column);
			const row = spared();
			column.append(row);
			host = document.createElement('div');
			document.body.append(host);
			instance = mount(PageShield, { target: host, props: { up: true, spare: row } });
			flushSync();
			expect(watched).toContain(row);
			expect(watched).toContain(column);

			row.getBoundingClientRect = () =>
				({
					left: 20,
					top: 10,
					right: 220,
					bottom: 46,
					x: 20,
					y: 10,
					width: 200,
					height: 36
				}) as DOMRect;
			for (const callback of report) callback();
			flushSync();

			const clip = document.querySelector<HTMLElement>('.page-shield')?.getAttribute('style') ?? '';
			expect(clip).toContain('20px 10px, 220px 10px, 220px 46px, 20px 46px');
		} finally {
			vi.unstubAllGlobals();
		}
	});

	it('covers the whole window when nothing is spared', () => {
		host = document.createElement('div');
		document.body.append(host);
		instance = mount(PageShield, { target: host, props: { up: true } });
		flushSync();

		expect(document.querySelector('.page-shield')?.getAttribute('style') ?? '').not.toContain(
			'clip-path'
		);
	});
});
