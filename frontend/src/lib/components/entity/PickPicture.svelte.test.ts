/*
 * The chooser's last step frames the picture with the picture editor's own crop control. The
 * header's Reframe is proved in `EntityHeader.svelte.test.ts`; this is the other screen: every pick
 * ends in the framing step, and that step is `edit/CropStage` held to the cover's 3:4, whose frame
 * is what the page is handed.
 */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const mocks = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('$lib/api/client', () => ({
	api: { get: mocks.get, post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

import PickPicture from './PickPicture.svelte';

let host: HTMLElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
	mocks.get.mockReset();
	mocks.get.mockResolvedValue({ items: [{ id: 'a1', media_type: 'image', duration_ms: null }] });
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
});

/** Give the framing step's measuring picture a shape, as a browser would when it loads. */
function pictureLoads(width: number, height: number): void {
	const measure = document.body.querySelector<HTMLImageElement>('img.measure');
	expect(measure, 'the framing step fetches the picture').not.toBeNull();
	Object.defineProperty(measure, 'naturalWidth', { value: width });
	Object.defineProperty(measure, 'naturalHeight', { value: height });
	measure?.dispatchEvent(new Event('load'));
	flushSync();
}

function button(words: string): HTMLButtonElement | undefined {
	return [...document.body.querySelectorAll<HTMLButtonElement>('button')].find(
		(one) => one.textContent?.trim() === words || one.getAttribute('aria-label') === words
	);
}

it('ends every pick in the editor crop, held to 3:4, and hands the page its frame', async () => {
	const onpick = vi.fn(async () => {});
	drawn = mount(PickPicture, {
		target: host,
		props: { open: true, name: 'Somebody', query: {}, onpick }
	}) as Record<string, unknown>;
	await vi.waitFor(() => expect(button('Use this as the picture')).toBeDefined());
	button('Use this as the picture')?.click();
	flushSync();
	pictureLoads(1600, 900);

	const grips = [...document.body.querySelectorAll('[data-grip]')].map((one) =>
		one.getAttribute('data-grip')
	);
	expect(grips.sort()).toEqual(['e', 'move', 'n', 'ne', 'nw', 's', 'se', 'sw', 'w']);
	expect(
		document.body.querySelector('input[type="range"]'),
		'no zoom slider of its own'
	).toBeNull();

	// An edge pulled in: the shape lock brings the height with it.
	const edge = document.body.querySelector<HTMLElement>('[data-grip="e"]');
	edge?.dispatchEvent(
		new KeyboardEvent('keydown', { key: 'ArrowLeft', shiftKey: true, bubbles: true })
	);
	flushSync();
	button('Use this picture')?.click();

	await vi.waitFor(() => expect(onpick).toHaveBeenCalled());
	const [assetId, atMs, frame] = onpick.mock.calls[0] as unknown as [
		string,
		null,
		{ x: number; y: number; w: number; h: number }
	];
	expect([assetId, atMs]).toEqual(['a1', null]);
	expect((frame.w * (1600 / 900)) / frame.h).toBeCloseTo(3 / 4, 3);
	expect(frame.h).toBeLessThan(1);
});
