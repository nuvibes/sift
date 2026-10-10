import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/* Playing something in a real browser: the only place the HLS wiring (Media Source Extensions,
 * fragmented MP4, buffering) is proven. The library is intercepted, not imported. */

/* Open the controls behind one button on the bar, by hover as a person does. */
async function openControls(page: Page): Promise<void> {
	await page.getByRole('button', { name: 'More controls' }).hover();
}

const CLIP = {
	id: 'p1',
	media_type: 'video',
	width: 320,
	height: 240,
	duration_ms: 6000,
	favorite: false,
	rating: null,
	concealed: false,
	original_filename: 'clip.mp4'
};

type Plan = {
	route: string;
	reason: string;
	url: string;
	scale_height: number | null;
	projected_realtime: number | null;
	streamable: boolean;
	duration_ms: number | null;
	resume_ms?: number | null;
	unreadable?: 'gone' | 'away' | null;
	scan_queued?: boolean;
};

/* A real playable file for the resume tests; the others 404 the stream on purpose. */
const REAL_MP4 = readFileSync(
	fileURLToPath(
		new URL('../../src/sift/kernel/tests/fixtures/ingress/accepted.mp4', import.meta.url)
	)
);

const TRANSCODE: Plan = {
	route: 'transcode',
	reason: 'Your browser cannot play HEVC, so it is being converted as you watch.',
	url: '/api/assets/p1/hls/index.m3u8',
	scale_height: null,
	projected_realtime: 3.2,
	streamable: true,
	duration_ms: 6000,
	resume_ms: null
};

async function serveLibrary(page: Page, plan: Plan) {
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [CLIP], total: 1, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/p1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CLIP) })
	);
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/p1/playback', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(plan) })
	);
	await page.route('**/api/assets/p1/view', (route) => route.fulfill({ status: 204, body: '' }));
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
});

test('an incompatible clip plays through the segment pipeline', async ({ page }) => {
	// A file the browser cannot decode ends in segment fetches: the whole HLS path.
	const requested: string[] = [];
	await serveLibrary(page, TRANSCODE);
	page.on('request', (request) => {
		const url = request.url();
		if (url.includes('/hls/')) requested.push(url);
	});

	await page.goto('/browse');
	await page.locator('.tile').first().click();

	// Whether the fake playlist yields a picture is not the point; the fetches are.
	await expect
		.poll(() => requested.some((url) => url.includes('index.m3u8')), { timeout: 15_000 })
		.toBe(true);
});

test('a direct-played clip never asks for a segment', async ({ page }) => {
	// A playable file is handed over directly; a playlist here would cost a transcode.
	const segments: string[] = [];
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));
	page.on('request', (request) => {
		if (request.url().includes('/hls/')) segments.push(request.url());
	});

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await expect(page.locator('video')).toBeVisible();

	expect(segments).toEqual([]);
});

test('a file recorded where it no longer is says so, and names the scan that will find it', async ({
	page
}) => {
	const segments: string[] = [];
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		unreadable: 'gone',
		scan_queued: true
	});
	page.on('request', (request) => {
		if (request.url().includes('/hls/') || request.url().includes('/stream'))
			segments.push(request.url());
	});

	await page.goto('/browse');
	await page.locator('.tile').first().click();

	await expect(page.getByText("Sift can't reach this file")).toBeVisible();
	await expect(page.getByText('A scan of its library folder is queued')).toBeVisible();
	expect(segments, 'a file nobody can read was fetched or converted').toEqual([]);
});

test('a stream that answers 404 is never blamed on the codec or converted', async ({ page }) => {
	// Moved since the plan: the element fails as it fails for a codec, and the second plan says why.
	const segments: string[] = [];
	await serveLibrary(page, TRANSCODE);
	let asked = 0;
	await page.route('**/api/assets/p1/playback', (route) => {
		asked += 1;
		const plan =
			asked === 1
				? { ...TRANSCODE, route: 'direct', url: '/api/assets/p1/stream', unreadable: null }
				: { ...TRANSCODE, unreadable: 'gone', scan_queued: false };
		return route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(plan)
		});
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));
	page.on('request', (request) => {
		if (request.url().includes('/hls/')) segments.push(request.url());
	});

	await page.goto('/browse');
	await page.locator('.tile').first().click();

	await expect(page.getByText("Sift can't reach this file")).toBeVisible();
	expect(asked).toBe(2);
	await expect(page.getByText('being converted')).toHaveCount(0);
	await expect(page.getByText('could play this file')).toHaveCount(0);
	expect(segments).toEqual([]);
});

test('a file that would stall says so instead of spinning, and offers to try anyway', async ({
	page
}) => {
	// Too slow to convert live: the player says so in words and offers the override.
	await serveLibrary(page, {
		...TRANSCODE,
		streamable: false,
		projected_realtime: 0.6,
		reason:
			'Your browser cannot play AV1, and this machine cannot convert it fast enough to play ' +
			'smoothly \u2014 it would keep pausing to catch up. You can try anyway.'
	});

	await page.goto('/browse');
	await page.locator('.tile').first().click();

	// Once: the corner mark does not repeat the stall sentence.
	await expect(page.getByText('it would keep pausing to catch up')).toHaveCount(1);
	await expect(page.getByText('it would keep pausing to catch up')).toBeVisible();
	await expect(page.locator('video')).toHaveCount(0);

	await page.getByRole('button', { name: 'Try anyway' }).click();

	await expect(page.locator('video')).toBeVisible();
});

test('the player says when a file is being converted', async ({ page }) => {
	// Said plainly: a 4K file played smaller tells the viewer why.
	await serveLibrary(page, {
		...TRANSCODE,
		scale_height: 1080,
		reason:
			'Your browser cannot play HEVC. This machine cannot convert it at full size fast enough ' +
			'to play smoothly, so it is being reduced to 1080p.'
	});

	await page.goto('/browse');
	await page.locator('.tile').first().click();

	await page.getByRole('button', { name: 'Why this file is being converted' }).hover();
	await expect(page.getByText('it is being reduced to 1080p')).toBeVisible();
});

test('a real browser tells a bare mp4 apart from one with a codec (the direct-play premise)', async ({
	page
}) => {
	// A codec string: a bare `video/mp4` is not a confident answer. AV1 is always shipped.
	await page.goto('/browse');
	const answers = await page.evaluate(() => {
		const v = document.createElement('video');
		return {
			bare: v.canPlayType('video/mp4'),
			withCodec: v.canPlayType('video/mp4; codecs="av01.0.05M.08"')
		};
	});

	expect(answers.bare).not.toBe('probably');
	expect(answers.withCodec).toBe('probably');
});

test('a view is still recorded when the tab closes, and only once', async ({ page }) => {
	// pagehide posts the view exactly once: the server adds to a running total.
	const views: string[] = [];
	await serveLibrary(page, { ...TRANSCODE, route: 'direct', url: '/api/assets/p1/stream' });
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));
	page.on('request', (request) => {
		if (request.method() === 'POST' && request.url().includes('/api/assets/p1/view')) {
			views.push(request.url());
		}
	});

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await expect(page.locator('video')).toBeVisible();

	await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
	await expect.poll(() => views.length).toBe(1);

	// A second leave must not post again.
	await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
	await page.waitForTimeout(200);
	expect(views.length).toBe(1);
});

test('carrying on to the next file is what the player starts on', async ({ page }) => {
	// Play through by default, as a Theater cell starts: stopping after each short clip is a chore.
	await serveLibrary(page, { ...TRANSCODE, route: 'direct', url: '/api/assets/p1/stream' });
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await openControls(page);

	await expect(page.getByRole('button', { name: 'Play through' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Stop at the end' })).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Repeat this' })).toHaveCount(0);
});

test('the volume control is a slider rather than a stub', async ({ page }) => {
	// The slider is turned on its side, so its logical `block-size` is its width.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/asset/p1');
	// Over the stage first: the bar takes no pointer events until it is up.
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.locator('.volume').hover();

	const level = page.locator('.level');
	await expect(level).toBeVisible();

	const box = (await level.boundingBox())!;
	expect(box.height, `the volume slider is ${box.width} x ${box.height}`).toBeGreaterThan(80);
	expect(box.width, `the volume slider is ${box.width} x ${box.height}`).toBeLessThan(40);
});

test('the bar is out of the way until somebody looks at the picture', async ({ page }) => {
	// In a window the bar fades with the pointer; read after the fade settles each way.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/asset/p1');
	const bar = page.locator('.player-bar');
	await expect(bar).toBeAttached();

	await page.mouse.move(0, 0);
	await page.waitForTimeout(400);
	expect(
		await bar.evaluate((el) => getComputedStyle(el).opacity),
		'the bar is drawn over the video with nobody looking at it'
	).toBe('0');

	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.waitForTimeout(400);
	expect(
		await bar.evaluate((el) => getComputedStyle(el).opacity),
		'the bar did not come back when the pointer entered the stage'
	).toBe('1');
});

test('and it goes away after a control is clicked, not only after something else is', async ({
	page
}) => {
	// Revealed on `:has(:focus-visible)`: a mouse click must not pin the bar open.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/asset/p1');
	const bar = page.locator('.player-bar');
	await expect(bar).toBeAttached();

	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.waitForTimeout(400);
	await bar.getByRole('button', { name: 'Play', exact: true }).click();

	// The bar holds a second after the pointer leaves, then the idle clock takes it.
	await page.mouse.move(5, 5);
	await expect
		.poll(async () => bar.evaluate((el) => getComputedStyle(el).opacity), { timeout: 6000 })
		.toBe('0');
});

async function serveDirect(page: Page) {
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));
}

test('the volume slider does not pin open after a mouse click on mute', async ({ page }) => {
	// The same rule for the volume popover.
	await serveDirect(page);

	await page.goto('/asset/p1');
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.locator('.volume').hover();

	const level = page.locator('.level');
	await expect(level).toBeVisible();

	await page.locator('.volume').getByRole('button').click();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.waitForTimeout(400);

	await expect(level, 'the volume slider stayed up after a mouse click on mute').toBeHidden();
});

test('the player bar rounds its bottom corners to the frame rather than reading sharp', async ({
	page
}) => {
	// The bar's blur escapes a rounded `overflow: hidden`, so the frame clips it with a path.
	await serveDirect(page);

	await page.goto('/asset/p1');
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });

	const frame = await page.locator('.player-bar').evaluate((el) => {
		const style = getComputedStyle(el.closest('.stage') as HTMLElement);
		return {
			clip: style.clipPath,
			left: style.borderBottomLeftRadius,
			right: style.borderBottomRightRadius
		};
	});

	expect(frame.left, 'the frame has no rounded corner for the bar to follow').not.toBe('0px');
	expect(frame.right, 'the frame rounds its two bottom corners differently').toBe(frame.left);
	expect(frame.clip, 'the frame does not clip the bar to its curve').toBe(
		`inset(0px round ${frame.left})`
	);
});

test('in a window the bar and cursor fade when the pointer goes idle, and return on a move', async ({
	page
}) => {
	// A window hides the bar and cursor on the same idle as fullscreen; a move wakes both.
	await serveDirect(page);

	await page.goto('/asset/p1');
	const stage = page.locator('.stage');
	const bar = page.locator('.player-bar');
	const video = page.locator('.stage video');
	await expect(bar).toBeAttached();

	await stage.hover({ position: { x: 40, y: 40 } });
	await page.waitForTimeout(400);
	expect(await bar.evaluate((el) => getComputedStyle(el).opacity)).toBe('1');

	await page.waitForTimeout(3000); // longer than the 2.5s idle plus the fade
	expect(
		await bar.evaluate((el) => getComputedStyle(el).opacity),
		'the bar stayed lit over a picture nobody was touching'
	).toBe('0');
	expect(
		await video.evaluate((el) => getComputedStyle(el).cursor),
		'the cursor stayed drawn over an idle picture'
	).toBe('none');

	await stage.hover({ position: { x: 80, y: 80 } });
	await page.waitForTimeout(400);
	expect(
		await bar.evaluate((el) => getComputedStyle(el).opacity),
		'the bar did not come back when the pointer moved'
	).toBe('1');
	expect(await video.evaluate((el) => getComputedStyle(el).cursor)).not.toBe('none');
});

test('the control bar sits flush with the bottom of the frame', async ({ page }) => {
	// No seam of the frame under the bar; sub-pixel tolerance for a fractional frame height.
	await serveLibrary(page, TRANSCODE);
	await page.goto('/browse');
	await page.locator('.tile-frame').first().click();

	const bar = page.locator('.player-bar');
	await expect(bar).toBeVisible();

	const gap = await bar.evaluate((element) => {
		const frame = element.closest('.stage') as HTMLElement;
		return frame.getBoundingClientRect().bottom - element.getBoundingClientRect().bottom;
	});

	expect(gap, 'the frame shows under the control bar').toBeLessThan(1);
});

/* Records every playhead move: an autoplaying clip advances anyway, so the act is what counts. */
async function recordSeeks(page: Page): Promise<void> {
	await page.addInitScript(() => {
		const w = window as unknown as { __seeks: number[] };
		w.__seeks = [];
		const proto = HTMLMediaElement.prototype;
		const original = Object.getOwnPropertyDescriptor(proto, 'currentTime');
		if (!original?.set || !original.get) return;
		const set = original.set;
		Object.defineProperty(proto, 'currentTime', {
			configurable: true,
			get: original.get,
			set(value: number) {
				w.__seeks.push(value);
				set.call(this, value);
			}
		});
	});
}

function seeks(page: Page): Promise<number[]> {
	return page.evaluate(() => (window as unknown as { __seeks: number[] }).__seeks);
}

test('a video opens where it was left, and reports where it stopped', async ({ page }) => {
	// The plan's starting point is seeked to once the length is known; that a seek happened is all.
	const posted: Array<Record<string, unknown>> = [];
	await recordSeeks(page);
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		duration_ms: 400,
		resume_ms: 200
	});
	await page.route('**/api/assets/p1/stream', (route) =>
		route.fulfill({ status: 200, contentType: 'video/mp4', body: REAL_MP4 })
	);
	await page.route('**/api/assets/p1/view', (route) => {
		posted.push(JSON.parse(route.request().postData() ?? '{}'));
		return route.fulfill({ status: 204, body: '' });
	});

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await expect(page.locator('video')).toBeVisible();

	await expect
		.poll(() => seeks(page).then((all) => all.filter((at) => at > 0)), { timeout: 10_000 })
		.not.toEqual([]);

	// Leaving says where it stopped.
	await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
	await expect.poll(() => posted.length).toBe(1);
	expect(posted[0]).toHaveProperty('position_ms');
});

test('a video with nowhere to go back to starts at the beginning', async ({ page }) => {
	// No stored position, no forward seek; Repeat's rewind to zero is not counted.
	await recordSeeks(page);
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		duration_ms: 400,
		resume_ms: null
	});
	await page.route('**/api/assets/p1/stream', (route) =>
		route.fulfill({ status: 200, contentType: 'video/mp4', body: REAL_MP4 })
	);

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	const element = page.locator('video');
	await expect(element).toBeVisible();

	// Wait until the element knows the file, or a late seek would be missed.
	await expect
		.poll(() => element.evaluate((v: HTMLVideoElement) => v.readyState), { timeout: 10_000 })
		.toBeGreaterThan(0);

	expect((await seeks(page)).filter((at) => at > 0)).toEqual([]);
});

/* A zoomed photograph pans to its own edges: translate is not divided by the scale. */
const STILL = readFileSync(
	fileURLToPath(
		new URL('../../src/sift/kernel/tests/fixtures/ingress/accepted.png', import.meta.url)
	)
);

test('a zoomed picture can be panned to its own top edge', async ({ page }) => {
	await signInAsAdmin(page);

	const still = {
		id: 's1',
		media_type: 'image',
		width: 16,
		height: 16,
		favorite: false,
		rating: null,
		concealed: false,
		original_filename: 'still.png',
		filename: 'still.png',
		thumb: true
	};
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [still], total: 1, limit: 60, offset: 0 })
		})
	);
	await page.route('**/api/assets/s1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(still) })
	);
	for (const kind of ['thumb', 'stream', 'preview']) {
		await page.route(`**/api/assets/*/${kind}`, (route) =>
			route.fulfill({ status: 200, contentType: 'image/png', body: STILL })
		);
	}

	await page.goto('/asset/s1');
	const picture = page.locator('.stage img');
	await expect(picture).toBeVisible();

	// Zooming is a fullscreen gesture.
	/* The bar is out of reach until `.stage.pointed`; a cold press lands on the picture. */
	await page.locator('.stage').hover();
	await page.locator('.player-bar').getByRole('button', { name: 'Full screen' }).click();
	await expect(page.locator('.stage.fullscreen')).toBeVisible();

	const frame = (await page.locator('.stage').boundingBox())!;
	const middle = { x: frame.x + frame.width / 2, y: frame.y + frame.height / 2 };
	await page.mouse.move(middle.x, middle.y);
	for (let step = 0; step < 60; step++) await page.mouse.wheel(0, -200);
	/* `zoom-out` over a magnified picture, from `Zoomable`, shared by all three surfaces. */
	await expect(picture).toHaveCSS('cursor', 'zoom-out');

	// Short strokes: a drag into the window's edge stops being delivered.
	for (let stroke = 0; stroke < 15; stroke++) {
		await page.mouse.move(middle.x, middle.y - 200);
		await page.mouse.down();
		await page.mouse.move(middle.x, middle.y + 200, { steps: 5 });
		await page.mouse.up();
	}

	const reach = await page.evaluate(() => {
		const el = document.querySelector('.stage img') as HTMLImageElement;
		const box = el.getBoundingClientRect();
		const frameBox = document.querySelector('.stage')!.getBoundingClientRect();
		return { top: box.top - frameBox.top, height: box.height, frame: frameBox.height };
	});

	expect(reach.height).toBeGreaterThan(reach.frame * 2);
	// The clamp still stops it going past the top.
	expect(Math.abs(reach.top), 'the top of the picture cannot be reached').toBeLessThanOrEqual(2);
});

test("the progress line takes the bar's place when the controls fade", async ({ page }) => {
	// The progress line shows when the bar is away, in a window and in fullscreen.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/asset/p1');
	const bar = page.locator('.player-bar');
	const line = page.locator('.player-progress');
	await expect(line).toBeAttached();

	const opacity = (thing: typeof bar) => thing.evaluate((el) => getComputedStyle(el).opacity);

	await page.mouse.move(0, 0);
	await expect.poll(() => opacity(bar)).toBe('0');
	await expect.poll(() => opacity(line), 'nothing says how far through the clip is').toBe('1');

	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await expect.poll(() => opacity(bar)).toBe('1');
	await expect.poll(() => opacity(line), 'two progress indicators two pixels apart').toBe('0');

	// Fullscreen waits on the idle clock; the bar needs the pointer on the stage first.
	await page.locator('.stage').hover();
	await page.locator('.player-bar').getByRole('button', { name: 'Full screen' }).click();
	await expect(page.locator('.stage.fullscreen')).toBeVisible();
	await expect(page.locator('.stage.resting')).toBeVisible({ timeout: 5000 });
	await expect.poll(() => opacity(bar)).toBe('0');
	await expect
		.poll(() => opacity(line), 'the fullscreen case is the one that gets forgotten')
		.toBe('1');
});

test('the mini player survives moving around the app, and stays where it is put', async ({
	page
}) => {
	// The panel is the shell's, so navigating keeps it.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await expect(page.locator('.sheet')).toBeVisible(); // the dialog the tile opened into

	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await openControls(page);
	await page.getByRole('button', { name: 'Open mini player' }).click();

	const panel = page.locator('.mini');
	await expect(panel).toBeVisible();
	await expect(page.locator('.sheet'), 'the full-size view is still playing too').toHaveCount(0);
	await expect(panel.locator('video')).toBeVisible();

	await page.getByRole('link', { name: 'Favorites' }).click();
	await expect(page).toHaveURL(/\/favorites/);
	await expect(panel.locator('video')).toBeVisible();

	const before = (await panel.boundingBox())!;
	const grip = page.locator('.mini .grip');
	const handle = (await grip.boundingBox())!;
	// The middle of the strip: both ends are buttons, and a press on one is not a drag.
	await page.mouse.move(handle.x + handle.width / 2, handle.y + 14);
	await page.mouse.down();
	await page.mouse.move(handle.x + handle.width / 2 - 200, handle.y - 90, { steps: 8 });
	await page.mouse.up();

	const after = (await panel.boundingBox())!;
	expect(after.x, 'the panel did not move').toBeLessThan(before.x - 50);
	expect(
		await page.evaluate(() => localStorage.getItem('sift.mini.place')),
		'where it was put was not remembered'
	).toContain('"x"');
});

test('a run holds a photograph and then carries on, when the account asked for that', async ({
	page
}) => {
	// The still and the modal can each pass alone while the prop joining them is missing.
	const pictures = [
		{ ...CLIP, id: 'i1', media_type: 'image', duration_ms: null, original_filename: 'one.jpg' },
		{ ...CLIP, id: 'i2', media_type: 'image', duration_ms: null, original_filename: 'two.jpg' }
	];
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: pictures, total: 2, limit: 50, offset: 0 })
		})
	);
	for (const picture of pictures) {
		await page.route(`**/api/assets/${picture.id}`, (route) =>
			route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify(picture)
			})
		);
	}
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/stream', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: STILL })
	);
	await page.route('**/api/settings', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				sections: [
					{
						name: 'Playback',
						settings: [
							{ key: 'playback.loop_mode', value: 'loop_all' },
							{ key: 'playback.dwell_pictures', value: true }
						]
					}
				]
			})
		})
	);

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await expect(page).toHaveURL(/\/asset\/i1/);

	await page.waitForTimeout(3000);
	await expect(page).toHaveURL(/\/asset\/i1/);
	await page.locator('.stage').hover();
	await page.locator('.stage').getByRole('button', { name: 'Play', exact: true }).click();
	await expect(page).toHaveURL(/\/asset\/i2/, { timeout: 10_000 });
});

test('back to full size opens the file again, rather than closing what was playing', async ({
	page
}) => {
	// Reading the id after emptying the panel would throw inside a handler, silently.
	const broke: string[] = [];
	page.on('pageerror', (error) => broke.push(error.message));

	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await openControls(page);
	await page.getByRole('button', { name: 'Open mini player' }).click();
	await expect(page.locator('.mini')).toBeVisible();

	await page.getByRole('button', { name: 'Back to full size' }).click();

	await expect(page.locator('.sheet'), 'the full-size view never came back').toBeVisible();
	await expect(page.locator('.mini')).toHaveCount(0);
	expect(broke, 'something threw on the way back to full size').toEqual([]);
});

test('the key that sends a clip to the corner brings it back', async ({ page }) => {
	// The panel's player ignores the keyboard, so something else must listen for the key.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.keyboard.press('i');
	await expect(page.locator('.mini')).toBeVisible();

	await page.keyboard.press('i');
	await expect(
		page.locator('.sheet'),
		'it went to the corner and would not come back'
	).toBeVisible();
	await expect(page.locator('.mini')).toHaveCount(0);
});

test('the Audio player stands inside the page, never over the rail, at every rail width', async ({
	page
}) => {
	// The panel centres in the page, not the window, or it runs under the rail.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	for (const [width, collapsed] of [
		[1100, false],
		[1100, true],
		[1600, false],
		[960, false]
	] as const) {
		await page.setViewportSize({ width, height: 900 });
		await page.goto('/browse');
		await page.evaluate((shut) => {
			if (shut) localStorage.setItem('sift.rail.collapsed', '1');
			else localStorage.removeItem('sift.rail.collapsed');
		}, collapsed);
		await page.reload();
		await page.locator('.tile').first().click();
		await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
		await page.keyboard.press('a');
		const bar = page.locator('section.mini.bar');
		await expect(bar).toBeVisible();

		const at = `at ${width}${collapsed ? ' with the rail collapsed' : ''}`;
		const rail = (await page.locator('.shell > :first-child').boundingBox())!;
		const content = (await page.locator('.shell > .content').boundingBox())!;
		await expect
			.poll(async () => (await bar.boundingBox())!.x, `the bar ran under the rail ${at}`)
			.toBeGreaterThanOrEqual(rail.x + rail.width);
		const box = (await bar.boundingBox())!;
		expect(box.x, `the bar left the page ${at}`).toBeGreaterThanOrEqual(content.x);
		expect(box.x + box.width, `the bar left the page ${at}`).toBeLessThanOrEqual(
			content.x + content.width
		);
		await expect
			.poll(async () => {
				const now = (await bar.boundingBox())!;
				return Math.abs(now.x + now.width / 2 - (content.x + content.width / 2));
			}, `the bar is not centred in the page ${at}`)
			.toBeLessThan(2);
		await page.locator('section.mini.bar').getByRole('button', { name: 'Close' }).click();
	}
});

test('a double-click on the small panel takes it back to full size', async ({ page }) => {
	// Double-click enlarges, as it takes a full-size player fullscreen.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await openControls(page);
	await page.getByRole('button', { name: 'Open mini player' }).click();
	await expect(page.locator('.mini')).toBeVisible();

	// Low left: the strip covers the top, the middle is play, the bottom is the timeline.
	await page.locator('.mini .screen').dblclick({ position: { x: 60, y: 170 } });

	await expect(page.locator('.sheet')).toBeVisible();
	await expect(page.locator('.mini')).toHaveCount(0);
});

test('the panel shows a play control under the pointer, and then puts it away', async ({
	page
}) => {
	// The control in the middle shows briefly; only a real pointer answers that.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await openControls(page);
	await page.getByRole('button', { name: 'Open mini player' }).click();
	await expect(page.locator('.mini')).toBeVisible();

	await page.locator('.mini .screen').hover({ position: { x: 60, y: 170 } });
	const controls = page.locator('.mini .tapped');
	await expect(controls, 'nothing appeared under the pointer').toBeVisible();
	// One file, so no step either side: the play control is all.
	await expect(page.locator('.mini .tap')).toHaveCount(1);
	await expect(page.locator('.mini .tap')).toHaveAttribute('aria-label', /^(Play|Pause)$/);

	await page.waitForTimeout(1500);
	await expect(controls, 'they went while the pointer was still on them').toBeVisible();

	await page.mouse.move(5, 5);
	await expect(controls).toBeHidden({ timeout: 4000 });
});

test('pressing a key does not light an accent ring around the whole dialog', async ({ page }) => {
	// Focus moved into the dialog must not light a keyboard ring on the whole sheet.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	const sheet = page.locator('.sheet');
	await expect(sheet).toBeVisible();

	const shadowOf = () => sheet.evaluate((node) => getComputedStyle(node).boxShadow);
	const resting = await shadowOf();

	await page.keyboard.press('m');
	expect(await shadowOf(), 'a keypress changed how the whole dialog is drawn').toBe(resting);
});

test('opening a second file replaces what is in the corner rather than being pushed aside', async ({
	page
}) => {
	// The panel stands down while any clip is open full size, not only its own.
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/*/stream', (route) => route.fulfill({ status: 404 }));

	const second = { ...CLIP, id: 'p2', original_filename: 'other.mp4' };
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [CLIP, second], total: 2, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/p2', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(second) })
	);
	await page.route('**/api/assets/p2/playback', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				...TRANSCODE,
				route: 'direct',
				url: '/api/assets/p2/stream',
				reason: 'Your browser can play this file as it is.'
			})
		})
	);
	await page.route('**/api/assets/p2/view', (route) => route.fulfill({ status: 204, body: '' }));

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.keyboard.press('i');
	await expect(page.locator('.mini')).toBeVisible();

	await page.locator('.tile').nth(1).click();
	await expect(page.locator('.sheet')).toBeVisible();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.keyboard.press('i');

	await expect(page.locator('.mini')).toBeVisible();
	await expect(page.locator('.sheet'), 'the view stayed open beside the panel').toHaveCount(0);
	await expect(page.locator('.mini video')).toHaveAttribute('src', /p2/);
});

test('the bar and the way through the list wait a second after the pointer leaves', async ({
	page
}) => {
	// Not on `:hover`, which ends at the picture's edge with no delay.
	await serveDirect(page);
	await page.goto('/asset/p1');

	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	const bar = page.locator('.player-bar');
	await expect(bar).toHaveCSS('opacity', '1');

	// Out across the bar: separate waits would let the bar's wake the stage.
	const over = (await bar.boundingBox())!;
	await page.mouse.move(over.x + over.width / 2, over.y + over.height / 2);
	await page.mouse.move(2, 2);
	await page.waitForTimeout(300);
	expect(
		await bar.evaluate((el) => getComputedStyle(el).opacity),
		'the bar went the moment the pointer left the picture'
	).toBe('1');

	// About a second, not four: stacked delays would fail nothing.
	await expect(bar).toHaveCSS('opacity', '0', { timeout: 2200 });
});

test('resting the pointer inside the drawer keeps it, and the bar, on screen', async ({ page }) => {
	// Reading the drawer must not restart the idle clock it stopped.
	await serveDirect(page);
	await page.goto('/asset/p1');

	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await openControls(page);
	// The drawer's panel: `.panel` alone matches the record's panel too.
	const panel = page.locator('.bridge .panel');
	await expect(panel).toBeVisible();

	await panel.hover();
	await page.waitForTimeout(3500);

	await expect(panel, 'the drawer went while the pointer was inside it').toBeVisible();
	await expect(page.locator('.player-bar')).toHaveCSS('opacity', '1');
});

test('moving off the drawer without reaching it closes it immediately', async ({ page }) => {
	// Gone on leave, with no grace: a lingering drawer is a menu to dismiss.
	await serveDirect(page);
	await page.goto('/asset/p1');

	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await openControls(page);
	await expect(page.locator('.bridge .panel')).toBeVisible();

	await page.locator('.player-bar .time.start').hover();
	await expect(page.locator('.bridge .panel')).toHaveCount(0);
});

// Shift with an arrow steps between files; the bare arrows seek.
const SECOND = {
	id: 'p2',
	media_type: 'video',
	width: 320,
	height: 240,
	duration_ms: 6000,
	favorite: false,
	rating: null,
	concealed: false,
	original_filename: 'second.mp4'
};

async function serveTwo(page: Page): Promise<void> {
	const plan = (id: string) => ({
		route: 'direct',
		reason: 'Your browser can play this file as it is.',
		url: `/api/assets/${id}/stream`,
		scale_height: null,
		projected_realtime: 3.2,
		streamable: true,
		duration_ms: 6000,
		resume_ms: null
	});
	await page.route('**/api/assets?*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [CLIP, SECOND], total: 2, limit: 50, offset: 0 })
		})
	);
	await page.route('**/api/assets/p1', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CLIP) })
	);
	await page.route('**/api/assets/p2', (route) =>
		route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(SECOND) })
	);
	await page.route('**/api/assets/*/thumb', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/preview', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/stream', (route) => route.fulfill({ status: 404 }));
	await page.route('**/api/assets/*/view', (route) => route.fulfill({ status: 204, body: '' }));
	await page.route('**/api/assets/p1/playback', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(plan('p1'))
		})
	);
	await page.route('**/api/assets/p2/playback', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(plan('p2'))
		})
	);
}

test('Shift and an arrow step between files', async ({ page }) => {
	await serveTwo(page);
	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await expect(page).toHaveURL(/\/asset\/p1/);
	// The element: what an arrow does depends on the file's kind, known once details return.
	await expect(page.locator('video')).toBeVisible();

	await page.keyboard.press('Shift+ArrowRight');
	await expect(page).toHaveURL(/\/asset\/p2/);

	await page.keyboard.press('Shift+ArrowLeft');
	await expect(page).toHaveURL(/\/asset\/p1/);
});

test('a bare arrow on a video seeks rather than stepping', async ({ page }) => {
	await serveTwo(page);
	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await expect(page).toHaveURL(/\/asset\/p1/);
	// Waited for: before the details arrive, an arrow steps rather than seeks.
	await expect(page.locator('video')).toBeVisible();

	await page.keyboard.press('ArrowRight');
	await page.waitForTimeout(200);

	await expect(page, 'a bare arrow stepped off the clip it was playing').toHaveURL(/\/asset\/p1/);
});
