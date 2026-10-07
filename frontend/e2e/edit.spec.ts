import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';
import { settled } from './settled';

/* The editor, driven in a real browser.
 *
 * Everything the editor DECIDES is the server's and is tested against the real rules next door.
 * What only a browser can answer is the geometry. A rectangle is dragged by a handle over an
 * element, converted into the picture's own pixels, and sent, and every one of those steps can
 * be correct on its own while the joins between them are not.
 *
 * So these do not re-check the arithmetic. They check the joins: which gestures a kind of file is
 * offered, what a drag actually puts in the request, that what the panel reports is the server's
 * rectangle rather than the one it sent, that a rectangle follows the picture when it is turned,
 * and that the sheet does not move under the pointer while somebody is dragging on it.
 */

type Media = 'image' | 'video' | 'gif';

function anAsset(media: Media, over: Record<string, unknown> = {}) {
	return {
		id: 'e1',
		media_type: media,
		width: media === 'video' ? 1920 : 1600,
		height: media === 'video' ? 1080 : 1200,
		duration_ms: media === 'video' ? 40_000 : null,
		favorite: false,
		rating: null,
		concealed: false,
		original_filename: media === 'video' ? 'holiday.mp4' : 'holiday.jpg',
		...over
	};
}

type Verdict = {
	asset_id: string;
	allowed: boolean;
	reason: string | null;
	output_filename: string | null;
	converted_from: string | null;
	lossy: boolean;
	approximate_start: boolean;
	left: number | null;
	top: number | null;
	width: number | null;
	height: number | null;
	frame_width: number | null;
	frame_height: number | null;
	result_width: number | null;
	result_height: number | null;
};

function aVerdict(over: Partial<Verdict> = {}): Verdict {
	return {
		asset_id: 'e1',
		allowed: true,
		reason: null,
		output_filename: 'holiday-cropped-800x600.jpg',
		converted_from: null,
		lossy: false,
		approximate_start: false,
		left: 0,
		top: 0,
		width: 1600,
		height: 1200,
		frame_width: 1600,
		frame_height: 1200,
		result_width: 1600,
		result_height: 1200,
		...over
	};
}

type Step = Record<string, unknown>;

/** The steps the last request carried, or nothing when none has gone out. */
function lastSteps(asked: Record<string, unknown>[]): Step[] {
	return (asked.at(-1)?.steps as Step[]) ?? [];
}

/* Every preflight the panel sends, in order, and the verdict it is answered with.
 *
 * The requests are collected rather than asserted one at a time on purpose: the panel re-asks on
 * every change, so what matters is what the LAST one said: an assertion on "a request went out
 * carrying these numbers" would pass on the request made before the drag.
 */
async function serve(
	page: Page,
	media: Media,
	options: {
		verdict?: (body: Record<string, unknown>) => Verdict;
		asset?: Record<string, unknown>;
		/** What the panel is told the picture measures as it is SEEN. */
		frame?: { width: number; height: number };
	} = {}
): Promise<{ asked: Record<string, unknown>[]; started: Record<string, unknown>[] }> {
	const asset = anAsset(media, options.asset);
	const asked: Record<string, unknown>[] = [];
	const started: Record<string, unknown>[] = [];

	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [asset], total: 1, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/e1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(asset) })
	);
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/e1/view', (route) => route.fulfill({ status: 204, body: '' }));
	await page.route('**/api/assets/e1/stream', (route) => route.fulfill({ status: 404 }));

	/* Editing lands a new file beside the original, so it is offered only where the library has a
	   folder to write into; one is served here, or every editor below opens on a greyed row. */
	await page.route('**/api/library/roots', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ roots: [{ id: 'r1', name: 'Library' }] })
		})
	);
	await page.route(
		(url) => url.pathname === '/api/library/folders',
		(route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({
					folders: [
						{
							id: 'f1',
							root_id: 'r1',
							parent_id: null,
							name: 'Photos',
							rel_path: 'Photos',
							writable: true
						}
					]
				})
			})
	);

	await page.route('**/api/assets/e1/edit/frame', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				asset_id: 'e1',
				width: options.frame?.width ?? asset.width,
				height: options.frame?.height ?? asset.height
			})
		})
	);

	await page.route('**/api/assets/e1/edit/preflight', (route) => {
		const body = route.request().postDataJSON() as Record<string, unknown>;
		asked.push(body);
		const answer = options.verdict ? options.verdict(body) : aVerdict();
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(answer)
		});
	});
	await page.route('**/api/assets/e1/edit', (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		started.push(route.request().postDataJSON() as Record<string, unknown>);
		route.fulfill({
			status: 202,
			contentType: 'application/json',
			body: JSON.stringify({ job_id: 'j1', output_filename: 'holiday-cropped-800x600.jpg' })
		});
	});

	return { asked, started };
}

/**
 * Open the editor on the served file.
 *
 * The verb is a parameter because it is not one word: a video is trimmed and a picture is edited,
 * and one panel is behind both. Passing the wrong one here is a thirty-second timeout rather than a
 * sentence, so the test below asserts which file gets which word.
 */
/**
 * Open the file's own Options menu.
 *
 * The verbs on this screen are rows behind one door, the three dots every other surface in Sift
 * opens its verbs from, so it has no word on it and is found by its accessible name.
 */
async function openTheOptions(page: Page): Promise<void> {
	await page.getByRole('button', { name: 'Options for this file', exact: true }).click();
}

async function openTheEditor(page: Page, verb: 'Modify' | 'Trim' = 'Modify'): Promise<void> {
	await page.goto('/asset/e1');
	await openTheOptions(page);
	await page.getByRole('menuitem', { name: verb }).click();
	await expect(page.getByRole('alertdialog')).toBeVisible();
	await settled(page.getByRole('alertdialog'));
}

/** Drag one of the rectangle's handles to a point given as a fraction of the picture. */
async function dragGrip(page: Page, grip: string, to: { x: number; y: number }): Promise<void> {
	/* Through Playwright's own checks, which wait until the handle is still and is what a press
	   there lands on. A press sent by coordinates while the menu that opened this dialog is still
	   closing lands on a page refusing clicks, and the drag never starts. */
	// Fonts loaded and the box still: a late face or the entrance would reflow the stage under it.
	const stage = page.locator('figure.stage');
	await page.evaluate(() => document.fonts.ready);
	await expect
		.poll(async () => {
			const before = JSON.stringify(await stage.boundingBox());
			await page.waitForTimeout(150);
			return before === JSON.stringify(await stage.boundingBox());
		})
		.toBe(true);
	for (let attempt = 0; attempt < 2; attempt += 1) {
		await page.locator(`[data-grip="${grip}"]`).hover();
		// Read before the press, as the stage does; a stage that moved under the drag is dragged again.
		const box = (await stage.boundingBox())!;
		await page.mouse.down();
		await page.mouse.move(box.x + box.width * to.x, box.y + box.height * to.y, { steps: 10 });
		await page.mouse.up();
		const after = (await stage.boundingBox())!;
		if (Math.abs(after.x - box.x) < 1 && Math.abs(after.width - box.width) < 1) return;
	}
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

// --- which gestures a kind of file is offered ---------------------------------------------------

test('a clip is offered Trim and a photograph is offered Modify', async ({ page }) => {
	/*
	 * The word on the button, asserted on both kinds of file in one test so the two cannot drift
	 * apart unnoticed. One panel is behind both, and a test that only ever looked at one kind of
	 * file cannot tell a label apart from a constant. The picture's word is Modify rather than Edit
	 * because it sits one row above Rename, and "Edit" is what half the application means by
	 * renaming.
	 */
	await serve(page, 'video');
	await page.goto('/asset/e1');
	await openTheOptions(page);
	await expect(page.getByRole('menuitem', { name: 'Trim' })).toBeVisible();
	await expect(page.getByRole('menuitem', { name: 'Modify' })).toHaveCount(0);
	/* And Create GIF beside it: making an animation is the editor's third answer, so the row
	   somebody comes looking for by name has its own name on the menu rather than being a switch
	   inside Trim. */
	await expect(page.getByRole('menuitem', { name: 'Create GIF' })).toBeVisible();

	await serve(page, 'image');
	await page.goto('/asset/e1');
	await openTheOptions(page);
	await expect(page.getByRole('menuitem', { name: 'Modify' })).toBeVisible();
	await expect(page.getByRole('menuitem', { name: 'Trim' })).toHaveCount(0);
	// A photograph has no stretch of time to animate, so the row is absent rather than refusing.
	await expect(page.getByRole('menuitem', { name: 'Create GIF' })).toHaveCount(0);
});

test('a photograph is offered the gestures you do to a photograph', async ({ page }) => {
	await serve(page, 'image');
	await openTheEditor(page);

	// The shapes a rectangle can be locked to. Free is one of them: the absence of a shape is a
	// choice somebody makes, so it is a button rather than the state you get by pressing nothing.
	const shapes = page.getByRole('group', { name: 'What shape' }).getByRole('button');
	await expect(shapes).toHaveCount(5);
	await expect(shapes.nth(0)).toContainText('Free');
	await expect(shapes.nth(1)).toContainText('Square');

	const ways = page.getByRole('group', { name: 'Which way round' }).getByRole('button');
	await expect(ways).toHaveCount(4);
	await expect(page.getByRole('button', { name: 'Mirror across' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Mirror down' })).toBeVisible();
});

test('a video is offered the lengths worth asking for, and marks on the picture', async ({
	page
}) => {
	await serve(page, 'video');
	await openTheEditor(page, 'Trim');

	/* SIX buttons in a group named for length, and only five of them are lengths. Create GIF
	   belongs here: it is about the same piece, and it is a pressed state rather than a third
	   length because it does not change what is marked. Asserted by position and by text rather
	   than only by count, so a length arriving or leaving still fails here and the one that is
	   not a length is named as such. */
	const lengths = page.getByRole('group', { name: 'How long a clip' }).getByRole('button');
	await expect(lengths).toHaveCount(6);
	await expect(lengths.nth(0)).toContainText('5s');
	await expect(lengths.nth(4)).toContainText('60s');
	await expect(lengths.nth(5)).toContainText('Create GIF');
	await expect(page.getByRole('button', { name: /Where it starts/ })).toBeVisible();
	await expect(page.getByRole('button', { name: /Where it ends/ })).toBeVisible();
	// The frames are the real thing: the marks sit across the video itself.
	await expect(page.locator('figure.stage video')).toBeVisible();
});

test('an animation is not offered the editor at all', async ({ page }) => {
	/* The trim promise rather than an oversight. A cut is a copy of the packets already there; a
	 * GIF's frames each depend on the one before, so the same operation would rebuild every one,
	 * and a trim that quietly re-encodes is the one thing this is not allowed to be. */
	await serve(page, 'gif');
	await page.goto('/asset/e1');
	await openTheOptions(page);
	await expect(page.getByRole('menuitem', { name: 'Compress' })).toBeVisible();

	await expect(page.getByRole('menuitem', { name: 'Modify' })).toHaveCount(0);
});

// --- the geometry, which is the half only a browser can answer ----------------------------------

test('the frame the rectangle is drawn on carries the picture own shape', async ({ page }) => {
	/* The fault this whole file exists for. Fitted inside a box of a fixed height the picture is
	 * letterboxed, so the pointer is read as a fraction of the BOX while the numbers are about the
	 * PICTURE, and the rectangle is drawn in one place and cut in another. */
	await serve(page, 'image');
	await openTheEditor(page);

	const box = (await page.locator('figure.stage').boundingBox())!;
	const shape = box.width / box.height;

	expect(Math.abs(shape - 1600 / 1200), `the frame is ${box.width} by ${box.height}`).toBeLessThan(
		0.02
	);
});

test('a handle dragged over the picture is sent in the picture own pixels', async ({ page }) => {
	const traffic = await serve(page, 'image');
	await openTheEditor(page);

	// The bottom-right handle, dragged to the middle. On a 1600 by 1200 picture that is 800 by 600.
	await dragGrip(page, 'se', { x: 0.5, y: 0.5 });

	await expect.poll(() => lastSteps(traffic.asked).length).toBeGreaterThan(0);
	const crop = lastSteps(traffic.asked).find((step) => step.operation === 'crop')!;

	// A pointer lands on whole device pixels, so the fractions are not exact. Anything within one
	// per cent is the rectangle that was drawn; the fault this catches is out by fifty times that.
	expect(Math.abs(Number(crop.width) - 800), `width came out ${crop.width}`).toBeLessThan(16);
	expect(Math.abs(Number(crop.height) - 600), `height came out ${crop.height}`).toBeLessThan(12);
	expect(crop.left).toBe(0);
	expect(crop.top).toBe(0);
});

test('a rectangle follows the picture when it is turned', async ({ page }) => {
	/* Left where its numbers were, a rectangle down the left edge of a landscape photograph ends up
	 * across the top of a portrait one, over something else entirely. */
	const traffic = await serve(page, 'image');
	await openTheEditor(page);
	await dragGrip(page, 'se', { x: 0.5, y: 0.5 });
	await expect.poll(() => lastSteps(traffic.asked).length).toBeGreaterThan(0);

	await page.getByRole('button', { name: 'Turn right' }).click();

	await expect.poll(() => lastSteps(traffic.asked).length).toBe(2);
	const steps = lastSteps(traffic.asked);
	// The turn first, then the rectangle in the frame the turn made: a rectangle that was across
	// the top left is now down the top right, and its sides have swapped.
	expect(steps[0]).toMatchObject({ operation: 'rotate', turn: 'right' });
	expect(Math.abs(Number(steps[1].width) - 600)).toBeLessThan(12);
	expect(Math.abs(Number(steps[1].height) - 800)).toBeLessThan(16);
});

test('the sheet does not move under the pointer while a rectangle is being dragged', async ({
	page
}) => {
	/* The lines under the picture must not change while dragging: if they did, the sheet would
	 * resize and, being centred, slide, and the rectangle would end somewhere other than where
	 * the pointer was let go.
	 */
	await serve(page, 'image');
	await openTheEditor(page);

	const stage = page.locator('figure.stage');
	await page.locator('[data-grip="se"]').hover();
	const before = (await stage.boundingBox())!;
	await page.mouse.down();
	await page.mouse.move(before.x + before.width * 0.6, before.y + before.height * 0.6, {
		steps: 10
	});

	const during = (await stage.boundingBox())!;
	await page.mouse.up();

	expect(Math.abs(during.x - before.x), 'the picture moved sideways mid-drag').toBeLessThan(2);
	expect(Math.abs(during.y - before.y), 'the picture moved vertically mid-drag').toBeLessThan(2);
});

test('nothing is asked about a photograph nothing has been done to yet', async ({ page }) => {
	/* There is no edit to ask about, and inventing one to ask with would name the copy after
	 * something nobody did. */
	const traffic = await serve(page, 'image');
	await openTheEditor(page);

	await expect(page.getByRole('button', { name: 'Save a copy' })).toBeDisabled();
	expect(traffic.asked).toHaveLength(0);
});

test('a photograph a camera turned is aimed at the way it is seen', async ({ page }) => {
	/* Stored 1600 by 1200 and drawn 1200 by 1600. The browser obeys the camera's note without being
	 * asked, so a panel aiming at the stored size draws its rectangle over one picture and cuts it
	 * out of another. */
	await serve(page, 'image', { frame: { width: 1200, height: 1600 } });
	await openTheEditor(page);

	/* Read off the shape of the stage rather than off a sentence: the panel does not print the
	 * frame's numbers, and the picture being drawn in the turned shape is the claim anyway.
	 */
	const box = (await page.locator('figure.stage').boundingBox())!;
	expect(Math.abs(box.width / box.height - 1200 / 1600)).toBeLessThan(0.02);
});

// --- what the panel says about what will happen -------------------------------------------------

test('the size reported back is the one the server will really cut', async ({ page }) => {
	/* Colour is stored at half resolution in almost every photograph, so a rectangle lands on
	 * two-by-two blocks and an odd number is rounded down. A panel reporting its own numbers
	 * promises a size the file does not come out at: 605 said, 604 on disk. */
	await serve(page, 'image', {
		verdict: () =>
			aVerdict({
				left: 100,
				top: 100,
				width: 604,
				height: 404,
				result_width: 604,
				result_height: 404
			})
	});
	await openTheEditor(page);
	await dragGrip(page, 'se', { x: 0.5, y: 0.5 });

	await expect(page.getByText('The copy comes out 604 by 404')).toBeVisible();
});

test('a refusal is shown and the button cannot be pressed', async ({ page }) => {
	await serve(page, 'video', {
		verdict: () =>
			aVerdict({
				allowed: false,
				reason: 'That runs past the end of the video.',
				output_filename: null
			})
	});
	await openTheEditor(page, 'Trim');

	await expect(page.getByText('That runs past the end of the video.')).toBeVisible();
	await expect(page.getByRole('button', { name: 'Save a copy' })).toBeDisabled();
});

test('a photograph off a phone says it is coming back as a jpeg before it runs', async ({
	page
}) => {
	await serve(page, 'image', {
		asset: { original_filename: 'IMG_4021.heic' },
		verdict: () =>
			aVerdict({
				output_filename: 'IMG_4021-mirrored.jpg',
				converted_from: 'heic',
				lossy: true
			})
	});
	await openTheEditor(page);
	await page.getByRole('button', { name: 'Mirror across' }).click();

	await expect(page.getByText('saved as a JPEG')).toBeVisible();
});

test('a cut says it may begin a moment early, which is the price of it being instant', async ({
	page
}) => {
	await serve(page, 'video', {
		verdict: () =>
			aVerdict({
				output_filename: 'holiday-from-10s.mp4',
				approximate_start: true,
				left: null,
				top: null,
				width: null,
				height: null
			})
	});
	await openTheEditor(page, 'Trim');
	await page.getByRole('button', { name: '15s' }).click();

	await expect(page.getByText(/begin/i)).toBeVisible();
});

test('the two handles cannot be dragged past each other', async ({ page }) => {
	/* Crossed, they describe a piece of less than no time, which the server refuses with a
	 * sentence about a length, true and nothing to do with what the person actually did. */
	const traffic = await serve(page, 'video');
	await openTheEditor(page, 'Trim');

	await page.locator('[data-end="start"]').hover({ position: { x: 10, y: 22 } });
	const timeline = (await page.locator('.timeline').boundingBox())!;
	await page.mouse.down();
	await page.mouse.move(timeline.x + timeline.width, timeline.y + timeline.height / 2, {
		steps: 10
	});
	await page.mouse.up();

	await expect.poll(() => Number(lastSteps(traffic.asked)[0]?.duration_ms ?? 0)).toBeGreaterThan(0);
});

test('a plain press on the kept part looks there, and a drag slides the piece', async ({
	page
}) => {
	await serve(page, 'video');
	await openTheEditor(page, 'Trim');
	await page.getByRole('button', { name: '15s' }).click();
	const look = page.locator('button.head');
	const start = page.locator('[data-end="start"]');
	const opened = await start.getAttribute('aria-label');
	const kept = (await page.locator('.kept').boundingBox())!;
	await page.mouse.click(kept.x + kept.width - 4, kept.y + kept.height / 2);
	await expect(look).not.toHaveAttribute('aria-label', /0:00$/);
	await expect(start).toHaveAttribute('aria-label', opened!);
	const x = kept.x + kept.width / 2;
	await page.mouse.move(x, kept.y + 6);
	await page.mouse.down();
	await page.mouse.move(x + 80, kept.y + 6, { steps: 10 });
	await page.mouse.up();
	await expect(start).not.toHaveAttribute('aria-label', opened!);
});

test('a video that has never had a strip built still edits, and asks for none', async ({
	page
}) => {
	/* The frames come from the video itself, not from a strip built after import, so a video
	 * added a moment ago is editable immediately.
	 */
	let stripAsked = 0;
	await page.route('**/api/assets/*/sprite*', (route) => {
		stripAsked += 1;
		return route.fulfill({ status: 404 });
	});
	await serve(page, 'video');
	await openTheEditor(page, 'Trim');

	// The panel works and asks for no strip at all: the video is its own preview.
	await expect(page.getByRole('button', { name: /Where it starts/ })).toBeVisible();
	expect(stripAsked).toBe(0);
});

// --- and starting it ------------------------------------------------------------------------------

test('saving sends the edit that was asked about and says it is running', async ({ page }) => {
	const traffic = await serve(page, 'image');
	await openTheEditor(page);
	await page.getByRole('button', { name: 'Turn left' }).click();

	await expect.poll(() => lastSteps(traffic.asked)[0]?.turn).toBe('left');

	await page.getByRole('button', { name: 'Save a copy' }).click();

	await expect.poll(() => traffic.started.length).toBe(1);
	expect((traffic.started[0].steps as Step[])[0]).toMatchObject({
		operation: 'rotate',
		turn: 'left'
	});
	await expect(page.getByText(/it runs in the background/)).toBeVisible();
});

test('the name the copy will have can be typed over, and the extension cannot', async ({
	page
}) => {
	const traffic = await serve(page, 'image');
	await openTheEditor(page);
	await page.getByRole('button', { name: 'Turn left' }).click();
	await expect.poll(() => traffic.asked.length).toBeGreaterThan(0);

	const field = page.getByLabel('Rename to');
	await expect(field).toHaveValue('holiday-cropped-800x600');
	await field.fill('the good one');

	await expect.poll(() => traffic.asked.at(-1)?.filename).toBe('the good one');
	await expect(page.getByText('.jpg', { exact: true })).toBeVisible();
});

test('one file is edited from the menu and a selection is compressed', async ({ page }) => {
	/* The substitution, in the one place it happens. A rectangle or a moment is chosen for a
	 * particular photograph and means nothing applied to the next file along.
	 *
	 * Both verbs need somewhere Sift may write, because either one lands a new file beside the
	 * original. So the library has to have a folder it was handed read-write for either of them
	 * to be drawn at all. */
	await serve(page, 'image');
	await page.goto('/browse');
	await expect(page.locator('.tile').first()).toBeVisible();
	await page.locator('.tile').first().click({ button: 'right' });

	await expect(page.getByRole('menuitem', { name: 'Modify' })).toBeVisible();
	await expect(page.getByRole('menuitem', { name: 'Compress' })).toHaveCount(0);
});
