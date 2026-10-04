/* What the editor panel promises, rendered rather than read off the source.
 *
 * Six claims are load-bearing here and none of them survive being checked by reading the file:
 *
 * 1. **What is offered depends on what kind of file it is.** A photograph is not trimmed and a
 *    video is not cropped, and the server refuses both, so a panel offering either is a button
 *    that cannot work.
 * 2. **It asks the server again every time anything changes**, including every frame of a drag.
 *    A panel answering from a first reply describes a rectangle nobody drew.
 * 3. **A refusal is shown and the button goes dead**, rather than the press being allowed and the
 *    failure arriving as a toast a moment later.
 * 4. **Dragging a handle produces the rectangle in the PICTURE's pixels**, not the ones on screen.
 *    Getting that conversion wrong is a crop that looks right and is not.
 * 5. **One Save carries everything that was done, in order.** Sent one at a time they would be one
 *    file each, and only the last would be the one anybody wanted.
 * 6. **The size it aims at is the picture as it is SEEN**, which for a photograph a camera turned
 *    is not the size Sift recorded.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { words } from '$lib/design/testing.svelte';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import EditDialog from './EditDialog.svelte';
import type { EditableAsset } from '$lib/edit/edit.svelte';

const answers = vi.hoisted(() => ({
	frame: vi.fn(),
	preflight: vi.fn(),
	start: vi.fn()
}));

vi.mock('$lib/edit/edit.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	frame: answers.frame,
	preflight: answers.preflight,
	start: answers.start
}));

function verdict(over: Partial<Record<string, unknown>> = {}) {
	return {
		asset_id: 'a1',
		allowed: true,
		reason: null,
		output_filename: 'photo-cropped-800x600.jpg',
		converted_from: null,
		lossy: false,
		approximate_start: false,
		left: 0,
		top: 0,
		width: 800,
		height: 600,
		frame_width: 800,
		frame_height: 600,
		result_width: 800,
		result_height: 600,
		...over
	};
}

/* Whole files rather than the three or four fields each test leans on. A detail response carries
   every one of these, so a fixture that carries fewer is a file the server never sends. */
const A_PHOTOGRAPH: EditableAsset = {
	id: 'a1',
	media_type: 'image',
	width: 800,
	height: 600,
	duration_ms: null,
	filename: 'a1.jpg',
	art: null,
	sprite: null
};
const A_VIDEO: EditableAsset = {
	id: 'a2',
	media_type: 'video',
	width: 1920,
	height: 1080,
	duration_ms: 600_000,
	filename: 'a2.mp4',
	art: null,
	sprite: null
};

let host: HTMLElement;

beforeEach(() => {
	answers.frame.mockReset().mockResolvedValue({ asset_id: 'a1', width: 800, height: 600 });
	answers.preflight.mockReset().mockResolvedValue(verdict());
	answers.start
		.mockReset()
		.mockResolvedValue({ job_id: 'j1', output_filename: 'photo-cropped-800x600.jpg' });
});

afterEach(() => {
	host?.remove();
	document.body.innerHTML = '';
});

async function render(asset: EditableAsset = A_PHOTOGRAPH, mode: 'trim' | 'gif' = 'trim') {
	host = document.createElement('div');
	document.body.append(host);
	const props = reactiveProps({ open: true, asset, mode });
	mount(EditDialog, { target: host, props });
	flushSync();
	await settle();
	return props;
}

/* The panel waits for the rectangle to hold still before it asks the server, so settling here is a
   real wait rather than a few microtasks: without it every assertion about what was asked would be
   made before anything had been. */
const ASK_AFTER = 150;

async function settle() {
	flushSync();
	await new Promise((wake) => setTimeout(wake, ASK_AFTER + 30));
	await Promise.resolve();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
}

function labels(): string[] {
	return [...document.querySelectorAll('button')].map((one) => words(one) || describeIt(one));
}

function describeIt(node: Element): string {
	return node.getAttribute('aria-label') ?? '';
}

function press(label: string): void {
	const found = [...document.querySelectorAll('button')].find(
		(one) => words(one) === label || describeIt(one) === label
	);
	if (!found) throw new Error(`no button called ${label}`);
	(found as HTMLButtonElement).click();
}

function onScreen(): string {
	return words(document.body);
}

function saveButton(): HTMLButtonElement | undefined {
	return [...document.querySelectorAll('button')].find((one) =>
		/^Save/.test((one.textContent ?? '').trim())
	) as HTMLButtonElement | undefined;
}

/** The last thing the server was asked about. */
function asked(): { steps: Record<string, unknown>[]; filename: string | null } {
	return answers.preflight.mock.calls.at(-1)?.[1];
}

/** A handle on the crop rectangle, and the stage measured to a known size. */
function grip(which: string): HTMLElement {
	const stage = document.querySelector('figure') as HTMLElement;
	stage.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 300 }) as DOMRect;
	const found = document.querySelector(`[data-grip="${which}"]`) as HTMLElement;
	found.setPointerCapture = () => {};
	return found;
}

function drag(node: HTMLElement, to: { x: number; y: number }): void {
	node.dispatchEvent(new PointerEvent('pointerdown', { clientX: 0, clientY: 0, bubbles: true }));
	node.dispatchEvent(
		new PointerEvent('pointermove', { clientX: to.x, clientY: to.y, bubbles: true })
	);
	node.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
}

describe('which door the panel was opened by', () => {
	it('is named for what it does to this kind of file', async () => {
		/*
		 * "Modify" over a picture, not "Edit": much of the app uses "Edit" for renaming, and this
		 * row sits right above Rename, so the two must not read as the same offer.
		 */
		await render(A_PHOTOGRAPH);
		expect(onScreen()).toContain('Modify this picture');

		document.body.innerHTML = '';
		await render(A_VIDEO);
		expect(onScreen()).toContain('Trim this video');
	});

	it('opens straight into making a GIF when that is the row that was pressed', async () => {
		/* Create GIF is its own row, and this is what it buys: the panel arrives already on the
		   GIF, rather than on Trim with a switch to find inside it. Asserted through what is
		   SENT, which is the only thing that decides what comes out the other end. */
		await render(A_VIDEO, 'gif');

		expect(asked().steps[0].operation).toBe('gif');
	});

	it('opens on the cut when it was reached the ordinary way', async () => {
		await render(A_VIDEO);

		expect(asked().steps[0].operation).toBe('trim');
	});
});

describe('the editor panel', () => {
	it('offers a photograph the gestures you do to a photograph', async () => {
		await render(A_PHOTOGRAPH);
		expect(labels()).toEqual(
			expect.arrayContaining([
				'Free',
				'Square',
				'Turn left',
				'Turn right',
				'Mirror across',
				'Mirror down'
			])
		);
		// There is no separate resize: a rectangle drawn freely is the same instruction.
		expect(labels()).not.toEqual(expect.arrayContaining(['Resize', '5s', '15s']));
	});

	it('offers a video the lengths worth asking for, and nothing a photograph is offered', async () => {
		await render(A_VIDEO);
		expect(labels()).toEqual(expect.arrayContaining(['5s', '10s', '15s', '30s', '60s']));
		expect(labels()).not.toEqual(expect.arrayContaining(['Square', '4:5', 'Resize']));
	});

	it('shows the video itself, which is what the marks are drawn on', async () => {
		// The frames are the real thing rather than a strip built after import, so a file added a
		// moment ago is as editable as one that has been here a year.
		await render(A_VIDEO);
		const shown = document.querySelector('video');
		expect(shown).not.toBeNull();
		expect(shown?.getAttribute('src')).toContain('/api/assets/a2/stream');
	});

	it('asks nothing at all about a photograph nothing has been done to yet', async () => {
		// There is no edit to ask about. Inventing one to ask with would name the copy after
		// something nobody did.
		await render(A_PHOTOGRAPH);
		expect(answers.preflight).not.toHaveBeenCalled();
		expect(saveButton()?.disabled).toBe(true);
	});

	it('asks the server what a cut would do, before offering to do it', async () => {
		await render(A_VIDEO);
		expect(answers.preflight).toHaveBeenCalled();
		expect(answers.start).not.toHaveBeenCalled();
	});

	it('asks again when a button is pressed', async () => {
		await render(A_PHOTOGRAPH);
		press('Turn right');
		await settle();

		expect(asked().steps).toEqual([{ operation: 'rotate', turn: 'right' }]);
	});

	it('turns a handle dragged on screen into a rectangle in the picture own pixels', async () => {
		/* The whole point of the conversion. The panel draws the picture at whatever width it has
		   and the server is told about a rectangle in the 800 by 600 the photograph really is, so a
		   handle dragged to the middle of a 400-pixel-wide element is 400 across in real pixels. */
		await render(A_PHOTOGRAPH);
		drag(grip('se'), { x: 200, y: 150 });
		await settle();

		expect(asked().steps).toEqual([
			{ operation: 'crop', left: 0, top: 0, width: 400, height: 300 }
		]);
	});

	it('will not let a handle be dragged out of the picture', async () => {
		await render(A_PHOTOGRAPH);
		drag(grip('se'), { x: 5000, y: 5000 });
		await settle();

		// Still the whole picture, so there is nothing to crop and nothing is asked.
		expect(answers.preflight).not.toHaveBeenCalled();
	});

	it('carries several things in one Save, in the order they were done', async () => {
		await render(A_PHOTOGRAPH);
		drag(grip('se'), { x: 200, y: 150 });
		await settle();
		press('Turn right');
		await settle();

		// The turn is applied to the picture first and the rectangle travels with it, so the
		// rectangle that goes is the one drawn, turned, and it goes in one request.
		expect(asked().steps).toEqual([
			{ operation: 'rotate', turn: 'right' },
			{ operation: 'crop', left: 300, top: 0, width: 300, height: 400 }
		]);
	});

	it('aims at the picture as it is SEEN, not as it is stored', async () => {
		/* A photograph a phone turned: stored 4000 by 3000 and drawn 3000 by 4000. The browser obeys
		   the camera's note without being asked, so a panel aiming at the stored size draws its
		   rectangle over one picture and cuts it out of another.

		   Read off what is SENT rather than off a sentence: the rectangle that opens covers the
		   whole picture, so it is taller than it is wide only if the frame the panel is working in
		   is the turned one. */
		answers.frame.mockResolvedValue({ asset_id: 'a1', width: 3000, height: 4000 });
		await render({ ...A_PHOTOGRAPH, width: 4000, height: 3000 });
		drag(grip('se'), { x: 200, y: 150 });
		await settle();

		const crop = asked().steps.find((one) => one.operation === 'crop');
		expect(crop).toBeDefined();
		expect(Number(crop?.width)).toBeLessThanOrEqual(3000);
		expect(Number(crop?.height)).toBeGreaterThan(Number(crop?.width));
	});

	it('reports the size the SERVER will really produce, not the one it sent', async () => {
		/*
		 * A crop lands on the picture's colour blocks whether anybody rounds it or not, so the
		 * server rounds it and says so. The panel says the size once, in the line about what the
		 * copy comes out as, rather than echoing its own numbers.
		 */
		answers.preflight.mockResolvedValue(
			verdict({
				left: 400,
				top: 300,
				width: 784,
				height: 604,
				result_width: 784,
				result_height: 604
			})
		);
		await render(A_PHOTOGRAPH);
		drag(grip('se'), { x: 200, y: 150 });
		await settle();

		expect(onScreen()).toContain('The copy comes out 784 by 604');
	});

	it('shows the refusal and will not let the button be pressed', async () => {
		answers.preflight.mockResolvedValue(
			verdict({ allowed: false, reason: 'That rectangle falls outside the picture.' })
		);
		await render(A_VIDEO);

		expect(onScreen()).toContain('falls outside the picture');
		expect(saveButton()?.disabled).toBe(true);
		expect(answers.start).not.toHaveBeenCalled();
	});

	it('offers the name the copy will have, with the extension fixed beside it', async () => {
		await render(A_VIDEO);
		const field = document.querySelector('input[type="text"]') as HTMLInputElement;
		expect(field.value).toBe('photo-cropped-800x600');
		expect(onScreen()).toContain('.jpg');
	});

	it('sends a typed name without an extension on it', async () => {
		// The extension is decided by what the copy is encoded as rather than by anybody typing, and
		// a perfectly good picture named `.txt` cannot be opened by name.
		await render(A_VIDEO);
		const field = document.querySelector('input[type="text"]') as HTMLInputElement;
		field.value = 'the good one';
		field.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		expect(asked().filename).toBe('the good one');
	});

	it('says a photograph off a phone is coming back as a jpeg, before it runs', async () => {
		answers.preflight.mockResolvedValue(verdict({ converted_from: 'heic' }));
		await render(A_VIDEO);
		expect(onScreen()).toContain('HEIC');
		expect(onScreen()).toContain('saved as a JPEG');
	});

	it('says a cut may begin a moment early, which is the price of it being instant', async () => {
		answers.preflight.mockResolvedValue(
			verdict({ approximate_start: true, output_filename: 'clip-from-1m30s.mp4' })
		);
		await render(A_VIDEO);
		expect(onScreen()).toContain('may begin up to a moment');
	});

	it('expresses a cut as the piece between the two handles', async () => {
		await render(A_VIDEO);
		expect(asked().steps).toEqual([{ operation: 'trim', start_ms: 0, duration_ms: 600_000 }]);
	});

	it('records a length asked for as a clip, and says how long the copy is', async () => {
		await render(A_VIDEO);
		press('15s');
		await settle();

		expect(asked().steps[0]).toMatchObject({ operation: 'clip', duration_ms: 15_000 });
		expect(onScreen()).toContain('0:15 long');
	});

	it('keeps a length inside a video shorter than the length asked for', async () => {
		// Ten minutes is the fixture; asking for a minute from the very end has to come back.
		await render({ ...A_VIDEO, duration_ms: 8_000 });
		press('60s');
		await settle();

		expect(asked().steps[0]).toMatchObject({ operation: 'clip', start_ms: 0, duration_ms: 8_000 });
	});
});
