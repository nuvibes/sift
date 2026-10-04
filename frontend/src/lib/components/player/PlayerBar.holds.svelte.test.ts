/* The bar holds its drawer and itself up while any menu drawn inside it is open.
 *
 * A menu is portalled out of the drawer, so the pointer moving onto it reads to the drawer as the
 * pointer having left. A bar told which menus to wait for, by name, would shut the drawer under a
 * menu left off that list. The probe's menu is on no list.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import PlayerBarMenuProbe from './PlayerBarMenuProbe.test.svelte';

let host: HTMLElement;
let mounted: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host?.remove();
	for (const stale of document.querySelectorAll('[role="menu"]')) stale.remove();
});

function render(onhold = vi.fn(), onrelease = vi.fn()) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(PlayerBarMenuProbe, { target: host, props: { onhold, onrelease } });
	flushSync();
	return { onhold, onrelease };
}

function openDrawer() {
	(host.querySelector('[aria-label="More controls"]') as HTMLElement).closest('button')!.click();
	flushSync();
}

async function openMenu() {
	const trigger = host.querySelector('.tray [aria-haspopup]') as HTMLElement;
	trigger.focus();
	trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
	flushSync();
	await tick();
	flushSync();
}

function leaveDrawer() {
	(host.querySelector('.tray') as HTMLElement).dispatchEvent(
		new PointerEvent('pointerleave', { bubbles: false })
	);
	flushSync();
}

describe('a menu inside the drawer', () => {
	it('keeps the drawer open while it is up, with nothing telling the bar about it', async () => {
		render();
		openDrawer();
		await openMenu();
		expect(document.querySelector('[role="menu"]'), 'the menu never opened').not.toBeNull();

		leaveDrawer();

		expect(host.querySelector('.tray .bridge'), 'the drawer shut under its menu').not.toBeNull();
		expect(document.querySelector('[role="menu"]'), 'the menu went with the drawer').not.toBeNull();
	});

	it('holds the bar while it is open and gives the bar back its clock when it closes', async () => {
		const { onhold, onrelease } = render();
		openDrawer();
		onhold.mockClear();
		onrelease.mockClear();

		await openMenu();
		await tick();
		expect(onhold, 'opening the menu did not hold the bar').toHaveBeenCalled();
		expect(onrelease).not.toHaveBeenCalled();

		document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		(document.querySelector('[role="menu"]') as HTMLElement | null)?.dispatchEvent(
			new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })
		);
		flushSync();
		await tick();
		flushSync();
		await tick();
		expect(onrelease, 'closing the menu left the bar held').toHaveBeenCalled();
	});

	it('holds the bar while the pointer rests on the menu, and lets go when it leaves', async () => {
		const said: string[] = [];
		render(
			vi.fn(() => said.push('hold')),
			vi.fn(() => said.push('release'))
		);
		openDrawer();
		await openMenu();
		const menu = document.querySelector('[role="menu"]') as HTMLElement;
		expect(
			menu.closest('[data-bits-floating-content-wrapper]'),
			'not a floating menu'
		).not.toBeNull();

		// Onto the menu: the drawer is left first, then the pointer moves on the menu.
		leaveDrawer();
		menu.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
		expect(said.at(-1), 'the pointer on the menu left the bar on its clock').toBe('hold');

		document.body.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
		expect(said.at(-1), 'leaving the menu kept the bar held').toBe('release');
	});

	it('lets the drawer go once no menu is up', () => {
		render();
		openDrawer();
		leaveDrawer();
		expect(host.querySelector('.tray .bridge'), 'the drawer stayed with nothing open').toBeNull();
	});
});
