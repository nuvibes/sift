/*
 * The drawer: shut is out of sight and out of reach, open slides in; over the page it stands on the
 * shield and a press outside or Escape closes it; beside the page it leaves the page live.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import source from './Drawer.svelte?raw';
import Probe from './DrawerProbe.test.svelte';

let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	removeStyles();
	document.body.innerHTML = '';
});

function draw(props: Record<string, unknown>) {
	instance = mount(Probe, { target: document.body, props });
	flushSync();
	const sheet = document.querySelector('aside.drawer') as HTMLElement;
	applyStyles(source, sheet);
	return sheet;
}

describe('Drawer', () => {
	it('is out of sight and out of reach while shut, and slides in when open', () => {
		const shut = draw({ open: false });
		expect(shut.getAttribute('aria-hidden')).toBe('true');
		expect(shut.inert).toBe(true);
		expect(getComputedStyle(shut).visibility).toBe('hidden');
		expect(getComputedStyle(shut).transform).toBe('translateX(100%)');
		unmount(instance!);
		instance = null;
		document.body.innerHTML = '';

		const open = draw({ open: true });
		expect(open.getAttribute('aria-label')).toBe('Swap');
		expect(getComputedStyle(open).visibility).toBe('visible');
		expect(getComputedStyle(open).transform).toBe('none');
		expect(open.querySelector('.inside')?.textContent).toBe('Picked');
		expect(open.querySelector('.leave')).not.toBeNull();
	});

	it('comes up from the bottom when asked', () => {
		const sheet = draw({ open: false, side: 'bottom' });
		expect(sheet.dataset.side).toBe('bottom');
		expect(getComputedStyle(sheet).transform).toBe('translateY(100%)');
	});

	it('over the page: stands on the shield, and a press outside or Escape closes it', () => {
		const onclose = vi.fn();
		const sheet = draw({ open: true, onclose });
		expect(document.querySelector('.page-shield')).not.toBeNull();

		sheet
			.querySelector('.inside')!
			.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		flushSync();
		expect(onclose).not.toHaveBeenCalled();

		document
			.querySelector('.page-thing')!
			.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		flushSync();
		expect(onclose).toHaveBeenCalledTimes(1);
		expect(sheet.getAttribute('aria-hidden')).toBe('true');
	});

	it('closes on Escape over the page', () => {
		const onclose = vi.fn();
		draw({ open: true, onclose });
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();
		expect(onclose).toHaveBeenCalledTimes(1);
	});

	it('beside the page: no shield, and pressing the page leaves it open', () => {
		const onclose = vi.fn();
		const sheet = draw({ open: true, beside: true, onclose });
		expect(document.querySelector('.page-shield')).toBeNull();

		document
			.querySelector('.page-thing')!
			.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();
		expect(onclose).not.toHaveBeenCalled();
		expect(sheet.getAttribute('aria-hidden')).toBe('false');
	});
});

/* A finger drawn down (or up) the head: down and up again immediately, as a quick stroke is. */
function stroke(target: Element, dy: number, pointerType = 'touch', dx = 0) {
	const at = (type: string, x: number, y: number) =>
		target.dispatchEvent(
			new PointerEvent(type, {
				bubbles: true,
				pointerType,
				isPrimary: true,
				pointerId: 4,
				clientX: x,
				clientY: y
			})
		);
	at('pointerdown', 200, 100);
	at('pointerup', 200 + dx, 100 + dy);
	flushSync();
}

describe('a sheet from the bottom, put away by a finger drawn down its head', () => {
	it('closes on a downward stroke on the head, and says so', () => {
		const onclose = vi.fn();
		const sheet = draw({ open: true, side: 'bottom', onclose });

		stroke(sheet.querySelector('.head')!, 140);

		expect(onclose).toHaveBeenCalledTimes(1);
		expect(sheet.getAttribute('aria-hidden')).toBe('true');
	});

	it('stays for a stroke up, a short one, a slanted one, a mouse, and one on the rows', () => {
		const onclose = vi.fn();
		const sheet = draw({ open: true, side: 'bottom', onclose });
		const head = sheet.querySelector('.head')!;

		stroke(head, -140);
		stroke(head, 30);
		stroke(head, 100, 'touch', 90);
		stroke(head, 140, 'mouse');
		stroke(sheet.querySelector('.inside')!, 140);

		expect(onclose).not.toHaveBeenCalled();
		expect(sheet.getAttribute('aria-hidden')).toBe('false');
	});

	it('is not a stroke on a sheet from the right, or on one beside the page', () => {
		const onclose = vi.fn();
		const right = draw({ open: true, onclose });
		stroke(right.querySelector('.head')!, 140);
		unmount(instance!);
		instance = null;
		document.body.innerHTML = '';
		const beside = draw({ open: true, side: 'bottom', beside: true, onclose });
		stroke(beside.querySelector('.head')!, 140);

		expect(onclose).not.toHaveBeenCalled();
	});

	it('leaves the stroke to script on the head, a finger high', () => {
		const rule = source.slice(source.indexOf(".drawer[data-side='bottom'] .head"));
		expect(rule).toMatch(/^[^}]*touch-action: none;/);
		expect(rule).toMatch(/^[^}]*min-block-size: var\(--touch-target\);/);
	});
});
