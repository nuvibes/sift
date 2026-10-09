/* The row on a recognition feature's pane that says whether its switch is on and where it is. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { toasts } from '$lib/shell/toasts.svelte';
import { hiddenWhile, revealSetting } from './settings-anchor.svelte';
import SwitchPointer, { COPY } from './SwitchPointer.svelte';

const SWITCH = { key: 'faces.enabled', value: false, default: false, label: 'Recognize faces' };
const HIDES = { 'faces.threshold': 'How sure a match must be' };

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;
let scrolled: typeof Element.prototype.scrollIntoView;

beforeEach(() => {
	host = document.createElement('div');
	host.className = 'section-body';
	document.body.append(host);
	scrolled = Element.prototype.scrollIntoView;
	Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
	Element.prototype.scrollIntoView = scrolled;
	vi.restoreAllMocks();
});

function draw(on: boolean): void {
	drawn = mount(SwitchPointer, {
		target: host,
		props: { entry: SWITCH, on, hides: HIDES }
	}) as Record<string, unknown>;
	flushSync();
}

it('says whether the switch is on, under its own name and address', () => {
	draw(false);
	const row = host.querySelector('[id="faces.enabled"]');
	expect(row?.textContent).toContain('Recognize faces');
	expect(row?.textContent).toContain(COPY.off);
});

it('answers a link to a row the switch hides by ringing the switch and saying why', async () => {
	draw(false);
	const show = vi.spyOn(toasts, 'show');
	await expect(revealSetting('faces.threshold')).resolves.toBe(true);
	expect(show).toHaveBeenCalledWith(
		hiddenWhile('How sure a match must be', 'Recognize faces', COPY.off)
	);
	await vi.waitFor(() =>
		expect(host.querySelector<HTMLElement>('[id="faces.enabled"]')?.dataset.siftFound).toBe('')
	);
});

it('explains nothing while the switch is on, nor a row it does not hide', async () => {
	draw(true);
	const show = vi.spyOn(toasts, 'show');
	vi.useFakeTimers();
	try {
		const threshold = revealSetting('faces.threshold');
		const other = revealSetting('faces.other');
		await vi.advanceTimersByTimeAsync(20_000);
		await expect(threshold).resolves.toBe(false);
		await expect(other).resolves.toBe(false);
	} finally {
		vi.useRealTimers();
	}
	expect(show).not.toHaveBeenCalledWith(expect.stringContaining('is hidden while'));
});
