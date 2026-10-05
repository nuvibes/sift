import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

/* Playing something, in a real browser.
 *
 * This is the only place the HLS wiring is actually proven. Everything below the player is tested
 * server-side against real ffmpeg, and the attach logic is tested against a stand-in. But whether
 * a real browser, handed a real playlist, fetches an init segment and then media segments and turns
 * them into a picture is a question only a real browser can answer. It involves Media Source
 * Extensions, the fragmented-MP4 layout, and the browser's own buffering, none of which exist in a
 * unit test environment.
 *
 * The library is intercepted rather than imported: what is under test is the player, and building a
 * real library through the import pipeline would make this a test of the import pipeline that
 * happens to end in a video.
 */

/* Open the grid of controls that sit behind one button on the bar.
 *
 * Shuffle, the loop, the stats and the mini player are off the row: sixteen controls on one line
 * would squeeze the timeline to a stub. They open on hover, which is how somebody uses them, so that is
 * how they are opened here. */
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
};

/* A real, playable file, so the element reports a length and can be seeked.
 *
 * Every other test here 404s the stream, because what they are about is which requests go out. The
 * resume tests are the opposite: they are about what happens once the browser has the media, so
 * they need media. This is the same clip the import fixtures use. */
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
	/*
	 * The end-to-end claim, in one test: open a file the browser cannot decode, and the browser
	 * ends up fetching segments. That is the whole HLS path (capability report, plan, playlist,
	 * initialisation segment, media segments) exercised by the real thing rather than described.
	 */
	const requested: string[] = [];
	await serveLibrary(page, TRANSCODE);
	page.on('request', (request) => {
		const url = request.url();
		if (url.includes('/hls/')) requested.push(url);
	});

	await page.goto('/browse');
	await page.locator('.tile').first().click();

	// The player mounted and asked for the playlist. Whether the fake playlist yields a picture is
	// not the point: what is proven is that a real browser took the HLS route and started
	// fetching, which is the wiring no other test can reach.
	await expect
		.poll(() => requested.some((url) => url.includes('index.m3u8')), { timeout: 15_000 })
		.toBe(true);
});

test('a direct-played clip never asks for a segment', async ({ page }) => {
	/*
	 * The majority path, from the outside. A browser that can play the file must be handed the
	 * file. If this ever starts fetching a playlist, every compatible video in the library has
	 * quietly begun costing a transcode.
	 */
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

test('a file that would stall says so instead of spinning, and offers to try anyway', async ({
	page
}) => {
	/*
	 * The decision, visible in the interface. Where the machine cannot convert a file
	 * faster than it plays, the player says so in words rather than showing a loading indicator
	 * that never resolves, and it offers the override, because the projection is an estimate and
	 * the person watching is entitled to overrule it.
	 */
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

	// ONCE, and the count is the assertion: the stall is said by the sentence that says what to DO,
	// and `noticeWords` answers nothing for it, so the corner mark does not say it a second time. A
	// `.first()` would pass whether a duplicate was there or not.
	await expect(page.getByText('it would keep pausing to catch up')).toHaveCount(1);
	await expect(page.getByText('it would keep pausing to catch up')).toBeVisible();
	// No video element at all until the person chooses: nothing is being converted yet.
	await expect(page.locator('video')).toHaveCount(0);

	await page.getByRole('button', { name: 'Try anyway' }).click();

	await expect(page.locator('video')).toBeVisible();
});

test('the player says when a file is being converted', async ({ page }) => {
	/*
	 * Said plainly rather than hidden. Somebody whose 4K file arrives smaller is entitled to know
	 * why. The alternative is concluding that Sift silently downgrades their library.
	 */
	await serveLibrary(page, {
		...TRANSCODE,
		scale_height: 1080,
		reason:
			'Your browser cannot play HEVC. This machine cannot convert it at full size fast enough ' +
			'to play smoothly, so it is being reduced to 1080p.'
	});

	await page.goto('/browse');
	await page.locator('.tile').first().click();

	// Behind the mark in the picture's corner, the one every surface draws, rather than a paragraph
	// under the stage: pointing at the mark opens the words.
	await page.getByRole('button', { name: 'Why this file is being converted' }).hover();
	await expect(page.getByText('it is being reduced to 1080p')).toBeVisible();
});

test('a real browser tells a bare mp4 apart from one with a codec (the direct-play premise)', async ({
	page
}) => {
	/*
	 * Why the box is probed with a codec string and not a bare `video/mp4`. In a real engine the
	 * naked MIME is not a confident answer, so asking it leaves the mp4 box unreported and sends
	 * the commonest file there is to the transcoder. A real codec string is a confident yes. Shown
	 * with AV1, which open-source Chromium always ships (H.264 is proprietary and may be absent
	 * here, so it cannot be the codec this test asks about).
	 */
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
	/*
	 * Leaving by closing the tab or navigating away fires pagehide, not a component unmount, so
	 * without a handler the view (the thing the whole watch-time count is built on) is never
	 * posted on the commonest exit. It must also post exactly once: the server adds to a running
	 * total, so a second post would count the same sitting twice.
	 */
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

	// A second leave must not post again. Give an erroneous second post time to arrive first.
	await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
	await page.waitForTimeout(200);
	expect(views.length).toBe(1);
});

test('carrying on to the next file is what the player starts on', async ({ page }) => {
	/*
	 * The same answer a Theater cell starts on. Repeat and stop are wrong in the same way: they
	 * stop. This is a browser for a library of short clips, so stopping at the end of each one
	 * means pressing something every few seconds to keep watching, with the queue sitting right
	 * there unused. Both other answers are one press away and are remembered.
	 */
	await serveLibrary(page, { ...TRANSCODE, route: 'direct', url: '/api/assets/p1/stream' });
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await openControls(page);

	// One control with three answers, showing the one it is on.
	await expect(page.getByRole('button', { name: 'Play through' })).toBeVisible();
	await expect(page.getByRole('button', { name: 'Stop at the end' })).toHaveCount(0);
	await expect(page.getByRole('button', { name: 'Repeat this' })).toHaveCount(0);
});

test('the volume control is a slider rather than a stub', async ({ page }) => {
	/* Logical properties resolve against the element's OWN writing mode, and this one has been
	 * turned on its side, so `block-size` on it means width. Written the wrong way round it
	 * comes out 72 wide and 12 tall: a squat lozenge with a slider crushed into it. Only a
	 * browser can say which way round it ended up; jsdom computes no geometry at all.
	 */
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/p1/stream', (route) => route.fulfill({ status: 404 }));

	await page.goto('/asset/p1');
	/* Onto the picture first, then onto the control, which is the order a hand does it in, and
	 * the order the interface requires. In a window the bar waits to be hovered before it takes
	 * pointer events, so a test that teleports straight onto the volume lands on the video behind an
	 * invisible bar. Moving over the stage is what brings the bar up. */
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.locator('.volume').hover();

	const level = page.locator('.level');
	await expect(level).toBeVisible();

	const box = (await level.boundingBox())!;
	expect(box.height, `the volume slider is ${box.width} x ${box.height}`).toBeGreaterThan(80);
	expect(box.width, `the volume slider is ${box.width} x ${box.height}`).toBeLessThan(40);
});

test('the bar is out of the way until somebody looks at the picture', async ({ page }) => {
	/* In a window the bar fades with the pointer as it does in fullscreen: a strip of chrome
	 * lying across the bottom of every video the whole time it is on screen would sit in the one
	 * view whose entire job is showing the frame.
	 *
	 * Read after the fade has finished in both directions. The bar transitions its opacity, so a
	 * measurement taken straight after the pointer moves catches it mid-flight, and
	 * `expect.poll` is the wrong tool here because the value being polled for is the one that
	 * arrives first.
	 */
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

	// Nothing hovered: the pointer starts outside the stage.
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
	/*
	 * The bar is revealed on `:has(:focus-visible)`, not `:focus-within`. `:focus-within` matches a
	 * plain MOUSE click: pressing pause focuses the button, and the bar would stay drawn across the
	 * video after the pointer had left it. Keyed on `:focus-visible` it follows the pointer for a
	 * mouse and stays put for a keyboard, which is what each of them needs.
	 */
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

	// Bring it up, then press something on it: the gesture that would pin it open.
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.waitForTimeout(400);
	await bar.getByRole('button').first().click();

	// Away from the picture, without clicking anything else. The bar holds for a second after the
	// pointer leaves it (deliberately, so a hand that slips off the edge does not lose it), and
	// then the idle clock takes it.
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
	/*
	 * The same rule for the volume popover: revealed on `:has(:focus-visible)`, so clicking mute
	 * with a mouse does not pin the level open. It follows the pointer for a mouse and stays for a
	 * keyboard.
	 */
	await serveDirect(page);

	await page.goto('/asset/p1');
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.locator('.volume').hover();

	const level = page.locator('.level');
	await expect(level).toBeVisible();

	// The gesture that would pin it: a mouse click on mute, then the pointer moves off the control.
	await page.locator('.volume').getByRole('button').click();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.waitForTimeout(400);

	await expect(level, 'the volume slider stayed up after a mouse click on mute').toBeHidden();
});

test('the player bar rounds its bottom corners to the frame rather than reading sharp', async ({
	page
}) => {
	/*
	 * The bar carries a backdrop-filter, which escapes an ancestor's rounded `overflow: hidden` in
	 * Chromium, so the frame rounds the bar with a clip-path on the same curve as its own corners.
	 * Only a browser resolves a computed clip; the unit environment has none.
	 */
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
	/*
	 * Fullscreen hides its controls on an idle timer, and so does a window: otherwise a pointer
	 * resting on the picture keeps a strip of chrome lit across the bottom of it, and the cursor sits
	 * over it the whole time. Both fade on the same idle, and a move brings them back.
	 */
	await serveDirect(page);

	await page.goto('/asset/p1');
	const stage = page.locator('.stage');
	const bar = page.locator('.player-bar');
	const video = page.locator('.stage video');
	await expect(bar).toBeAttached();

	// Up, then held still on the picture past the idle.
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

	// A move wakes both.
	await stage.hover({ position: { x: 80, y: 80 } });
	await page.waitForTimeout(400);
	expect(
		await bar.evaluate((el) => getComputedStyle(el).opacity),
		'the bar did not come back when the pointer moved'
	).toBe('1');
	expect(await video.evaluate((el) => getComputedStyle(el).cursor)).not.toBe('none');
});

test('the control bar sits flush with the bottom of the frame', async ({ page }) => {
	/*
	 * A hairline of the frame showing under the bar.
	 *
	 * The bar is absolutely positioned along the bottom of a frame that clips itself with a radius,
	 * and its blur escapes that clip, which is why it rounds its own corners rather than relying
	 * on the frame to do it. The risk is a seam: a line of the frame's own ground between the
	 * bottom of the bar and the bottom of the frame, which reads as a border nobody asked for.
	 *
	 * Measured rather than eyeballed, and with a sub-pixel tolerance, because the frame's height is
	 * a viewport fraction and lands on fractional pixels.
	 */
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

/*
 * Every place the playhead is MOVED to, recorded before the app loads.
 *
 * Reading `currentTime` after the fact cannot answer this: an autoplaying clip advances on its own,
 * so a playhead past zero says nothing about whether anything seeked. Patching the setter records
 * the act rather than the result, and a repeat rewinding to zero is told apart from a resume by
 * the value.
 */
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
	/*
	 * The resume wiring, from the outside, in the only place it can be proven: the plan carries a
	 * starting point, and the player has to move the playhead there once the browser knows how long
	 * the file is.
	 *
	 * The clip is a fraction of a second long, so the exact number is not the assertion: the
	 * player pulls a position back off the end, and it should. What is asserted is that a seek
	 * happened at all, which is the whole of what this component decides.
	 */
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

	// And the other half: leaving says where it stopped, so the next open has something to use.
	await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
	await expect.poll(() => posted.length).toBe(1);
	expect(posted[0]).toHaveProperty('position_ms');
});

test('a video with nowhere to go back to starts at the beginning', async ({ page }) => {
	/*
	 * The common case, and the one a wrong default would ruin: most of a library has no stored
	 * position, and a player that seeked anyway would start every clip somewhere arbitrary.
	 *
	 * Only forward seeks count. Repeat is the default and it rewinds to zero at the end of every
	 * pass, which is a move of the playhead and is not this.
	 */
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

	// Wait until the element really knows the file. Checking before that would pass against a
	// player that seeks the moment it finds out, which is exactly the failure being excluded.
	await expect
		.poll(() => element.evaluate((v: HTMLVideoElement) => v.readyState), { timeout: 10_000 })
		.toBeGreaterThan(0);

	expect((await seeks(page)).filter((at) => at > 0)).toEqual([]);
});

/* A photograph, zoomed, and whether it can be moved far enough to see its own edges.
 *
 * The still fills the frame's height, so at zoom n there is exactly frameHeight * (n - 1) of it
 * hanging off, half above and half below, and dragging must be able to bring either edge onto
 * the frame's edge. `translate` and `scale` compose so that translate is NOT multiplied by the
 * scale: dividing the offset by the zoom would shrink every pan by the zoom factor, and the
 * closer you looked the less of the picture you could reach.
 */
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

	// Zooming is a fullscreen gesture and the handler says so, so there is nothing to test until the
	// picture is actually fullscreen.
	/*
	 * A photograph wears the SAME bar a clip wears (`player-bar`), so stepping from a clip onto
	 * a picture keeps every control where the hand left it. `StillView.svelte` carries both halves
	 * of that argument.
	 */
	/* The bar is out of reach until the pointer is on the stage: it fades in on `.stage.pointed`,
	   so aimed at cold the press lands on the picture, which is what `MediaStage` intends. */
	await page.locator('.stage').hover();
	await page.locator('.player-bar').getByRole('button', { name: 'Full screen' }).click();
	await expect(page.locator('.stage.fullscreen')).toBeVisible();

	const frame = (await page.locator('.stage').boundingBox())!;
	const middle = { x: frame.x + frame.width / 2, y: frame.y + frame.height / 2 };
	await page.mouse.move(middle.x, middle.y);
	for (let step = 0; step < 60; step++) await page.mouse.wheel(0, -200);
	/* Magnified is a `scale` on the element. What the pointer looks like over a magnified
	   picture is `Zoomable`'s answer, shared with the two other surfaces that magnify one, so
	   asserting on it is asserting on the thing all three of them read.

	   `zoom-out` and NOT an open hand: the glass stays once the picture is magnified and turns
	   its sign over, so the offer does not disappear at the moment somebody has proved they want
	   it. `grab` is only where the surface does not offer to magnify at all, and `grabbing` for
	   a drag in progress. */
	await expect(picture).toHaveCSS('cursor', 'zoom-out');

	/* Short strokes rather than one long drag: the pointer cannot leave the window, so a drag that
	   runs into the edge simply stops being delivered. A hand does the same thing. */
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

	// Grown well past the frame, or there was never anything to pan to.
	expect(reach.height).toBeGreaterThan(reach.frame * 2);
	// ...and the top of it has been brought onto the top of the frame. Not past it: the clamp is
	// what stops a picture being dragged off into empty space, and it must still hold.
	expect(Math.abs(reach.top), 'the top of the picture cannot be reached').toBeLessThanOrEqual(2);
});

test("the progress line takes the bar's place when the controls fade", async ({ page }) => {
	/*
	 * Asserted in a real browser and on the running element, because a static reading cannot see
	 * this: the line is written in the player and positioned and faded by the stage around it, so
	 * whether the two agree is only true of the thing on screen.
	 *
	 * Two states, and the fullscreen one is the one that gets forgotten. In a window the bar is
	 * hidden until somebody looks at the picture; fullscreen keeps it up until the pointer has been
	 * still for a couple of seconds. Either way something has to say how far through the clip is.
	 */
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

	// Nobody looking: the bar is out of the way and the line is what is left.
	await page.mouse.move(0, 0);
	await page.waitForTimeout(400);
	expect(await opacity(bar)).toBe('0');
	expect(await opacity(line), 'nothing says how far through the clip is').toBe('1');

	// Looking at it: the bar is back and the line gives way rather than doubling up under it.
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.waitForTimeout(400);
	expect(await opacity(bar)).toBe('1');
	expect(await opacity(line), 'two progress indicators two pixels apart').toBe('0');

	// Fullscreen, where the bar waits on the idle clock rather than on the pointer.
	/* The bar is out of reach until the pointer is on the stage: it fades in on `.stage.pointed`,
	   so aimed at cold the press lands on the picture, which is what `MediaStage` intends. */
	await page.locator('.stage').hover();
	await page.locator('.player-bar').getByRole('button', { name: 'Full screen' }).click();
	await expect(page.locator('.stage.fullscreen')).toBeVisible();
	await expect(page.locator('.stage.resting')).toBeVisible({ timeout: 5000 });
	expect(await opacity(bar)).toBe('0');
	expect(await opacity(line), 'the fullscreen case is the one that gets forgotten').toBe('1');
});

test('the mini player survives moving around the app, and stays where it is put', async ({
	page
}) => {
	/*
	 * The claim in one test, and it is only true of a real browser: the panel is drawn by the shell,
	 * so navigating from one screen to another must not take it away. A component that looked right
	 * inside the asset view would pass every unit test and disappear on the first click.
	 */
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

	// The full-size view stood down, so only one of the two is playing.
	const panel = page.locator('.mini');
	await expect(panel).toBeVisible();
	await expect(page.locator('.sheet'), 'the full-size view is still playing too').toHaveCount(0);
	await expect(panel.locator('video')).toBeVisible();

	// Somewhere else entirely. The panel is the shell's, so it comes along.
	await page.getByRole('link', { name: 'Favorites' }).click();
	await expect(page).toHaveURL(/\/favorites/);
	await expect(panel.locator('video')).toBeVisible();

	// Dragged by its edge, and remembered for the next time it opens.
	const before = (await panel.boundingBox())!;
	const grip = page.locator('.mini .grip');
	const handle = (await grip.boundingBox())!;
	// The MIDDLE of the strip. Both ends of it are buttons (the way back and the fit control at
	// one end, the way out at the other), and a press that lands on a button is deliberately not a
	// drag, or neither of them could be clicked at all.
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
	/*
	 * The wiring, in a real browser, because the wiring is what can break.
	 *
	 * Every piece of this can pass its own unit test while the run still stops dead on the first
	 * photograph: the still knows how long to wait, the modal knows where to go next, and the prop
	 * that joins the two is simply never passed. Nothing below the whole page can see that.
	 */
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
	// The two account settings this behaviour is made of: play through, and hold photographs.
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

	// A picture opened by a press waits for Play, and Play starts its rest before the run carries on.
	await page.waitForTimeout(3000);
	await expect(page).toHaveURL(/\/asset\/i1/);
	await page.locator('.stage').hover();
	await page.locator('.stage').getByRole('button', { name: 'Play', exact: true }).click();
	await expect(page).toHaveURL(/\/asset\/i2/, { timeout: 10_000 });
});

test('back to full size opens the file again, rather than closing what was playing', async ({
	page
}) => {
	/*
	 * A control that reads the id off the panel AFTER emptying it reads it off nothing: the panel
	 * shuts, the video stops, and the full-size view never opens. Only a real browser catches this:
	 * the throw is inside an event handler, so nothing above it fails and every unit test passes.
	 */
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
	/*
	 * The player in the panel is told to keep out of the keyboard, so that two of them mounted at
	 * once cannot both act on one press, and something must still listen once the panel is the
	 * only thing playing, so the key works both ways.
	 */
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
	/*
	 * The bar is fixed to the window. Centred on the WINDOW at 800 pixels, it would run under the rail on
	 * any window narrower than the rail and the bar together (about 1100 with the rail open). It
	 * centres in the page's box, and only a real browser has a rail, a page and a bar to measure.
	 */
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
		/* Centred in the page, not the window: its middle on the page's middle, once it has settled. */
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
	// The same gesture that takes a full-size player into fullscreen. On a picture this small,
	// "make this bigger" is the only thing it can mean.
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

	/* Low and to the left of the picture, which is the only part of it that is nothing else. The
	   strip lies OVER the top of the panel rather than above it, so a press twenty pixels down is
	   a drag; the middle is the play control, which stops a double-click reaching the picture on
	   purpose; and the bottom edge is the timeline. */
	await page.locator('.mini .screen').dblclick({ position: { x: 60, y: 170 } });

	await expect(page.locator('.sheet')).toBeVisible();
	await expect(page.locator('.mini')).toHaveCount(0);
});

test('the panel shows a play control under the pointer, and then puts it away', async ({
	page
}) => {
	/* The panel is too small for a bar across it, so the one control worth reaching for is drawn in
	 * the middle of the picture, and only for as long as it takes to see. A real pointer is the
	 * only thing that can answer whether it appears and then goes. */
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
	/* ONE of them. The five-second steps either side are on the arrow keys, as on every other
	   bar; what stands at the edges of the picture is the way through the LIST, drawn only where
	   there is somewhere to step. This library is one file, so there is no step to offer and the
	   play control is the whole of it. */
	await expect(page.locator('.mini .tap')).toHaveCount(1);
	await expect(page.locator('.mini .tap')).toHaveAttribute('aria-label', /^(Play|Pause)$/);

	// They stay while the pointer is on the picture: a control that goes while you are reaching
	// for it is worse than one that is always there.
	await page.waitForTimeout(1500);
	await expect(controls, 'they went while the pointer was still on them').toBeVisible();

	// A second after the pointer leaves, they fade out.
	await page.mouse.move(5, 5);
	await expect(controls).toBeHidden({ timeout: 4000 });
});

test('pressing a key does not light an accent ring around the whole dialog', async ({ page }) => {
	/*
	 * Focus is put on the dialog when it opens, so the keyboard is inside it rather than on the grid
	 * behind. The browser cannot tell that from somebody arriving at a control: pressing any key
	 * marks whatever holds the focus as keyboard-reached, so muting or seeking would light an accent ring
	 * around the entire sheet. Only a real browser has that rule at all.
	 */
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
	/*
	 * The panel listens for the key that brings a clip back, so that the key works both ways. It
	 * stands down whenever ANY clip is open at full size, not only its own: otherwise, with one
	 * clip in the corner and a different one on screen, the panel would take the press and expand
	 * itself while the clip somebody was looking at stayed where it was.
	 */
	await serveLibrary(page, {
		...TRANSCODE,
		route: 'direct',
		url: '/api/assets/p1/stream',
		reason: 'Your browser can play this file as it is.'
	});
	await page.route('**/api/assets/*/stream', (route) => route.fulfill({ status: 404 }));

	// A second clip, so there is something else to open. The shared library is one file.
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

	// A different file, at full size, while the first is still in the corner.
	await page.locator('.tile').nth(1).click();
	await expect(page.locator('.sheet')).toBeVisible();
	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await page.keyboard.press('i');

	// The corner holds the second file, and the view it came from has stood down.
	await expect(page.locator('.mini')).toBeVisible();
	await expect(page.locator('.sheet'), 'the view stayed open beside the panel').toHaveCount(0);
	await expect(page.locator('.mini video')).toHaveAttribute('src', /p2/);
});

test('the bar and the way through the list wait a second after the pointer leaves', async ({
	page
}) => {
	/*
	 * Not drawn on `:hover`, which stops matching the instant the pointer crosses the edge of the
	 * picture. They would vanish the moment somebody looked away, with no delay to tune. Only a
	 * real browser can answer this: it is the browser's own hover state that is the mechanism.
	 */
	await serveDirect(page);
	await page.goto('/asset/p1');

	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	const bar = page.locator('.player-bar');
	await expect(bar).toHaveCSS('opacity', '1');

	/* Out across the bar, which is the path a hand actually takes: the bar lies along the bottom
	 * of the picture, so leaving downward crosses it. If each end started its own wait, the bar's
	 * would finish after the stage's and wake the stage back up. Leaving sideways never touches
	 * the bar, so it would not catch that.
	 */
	const over = (await bar.boundingBox())!;
	await page.mouse.move(over.x + over.width / 2, over.y + over.height / 2);
	await page.mouse.move(2, 2);
	await page.waitForTimeout(300);
	expect(
		await bar.evaluate((el) => getComputedStyle(el).opacity),
		'the bar went the moment the pointer left the picture'
	).toBe('1');

	/* And then it goes, about a second later, not four.
	 *
	 * The upper bound is the point of this line: two stacked delays would leave it sitting there
	 * with nothing broken and nothing failing.
	 */
	await expect(bar).toHaveCSS('opacity', '0', { timeout: 2200 });
});

test('resting the pointer inside the drawer keeps it, and the bar, on screen', async ({ page }) => {
	/*
	 * The stage wakes on `pointermove` and puts the controls away when the pointer stops moving.
	 * A move over the bar or the drawer bubbles up to it, so reading the drawer could restart the very
	 * clock the drawer had just stopped, and a couple of seconds later the drawer, the bar and
	 * the way through the list would all go, with the pointer still on them.
	 */
	await serveDirect(page);
	await page.goto('/asset/p1');

	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await openControls(page);
	/* The DRAWER's panel, named by the bridge it hangs from. `.panel` on its own resolves to two
	   elements, since the file's record has a panel of its own: a strict-mode failure that reads
	   like the drawer having gone. `Panel` is the app's one box, so it is never a locator by itself. */
	const panel = page.locator('.bridge .panel');
	await expect(panel).toBeVisible();

	// Into the grid itself, and then still. Longer than the idle clock.
	await panel.hover();
	await page.waitForTimeout(3500);

	await expect(panel, 'the drawer went while the pointer was inside it').toBeVisible();
	await expect(page.locator('.player-bar')).toHaveCSS('opacity', '1');
});

test('moving off the drawer without reaching it closes it at once', async ({ page }) => {
	// Open on hover, gone on leave: the same as the volume. The grace belongs to the bar, not to
	// this: a drawer that lingers after the pointer has moved on is a menu somebody has to dismiss.
	await serveDirect(page);
	await page.goto('/asset/p1');

	await page.locator('.stage').hover({ position: { x: 40, y: 40 } });
	await openControls(page);
	// The drawer's own panel. See the test above for why `.panel` alone is not a locator here.
	await expect(page.locator('.bridge .panel')).toBeVisible();

	// Onto the time at the start of the scrub line: still on the bar, nowhere near the drawer.
	await page.locator('.player-bar .time.start').hover();
	await expect(page.locator('.bridge .panel')).toHaveCount(0);
});

/* Stepping between files with Shift and an arrow.
 *
 * The bare arrows seek, so stepping is on Shift with the same two keys. A two-file library is
 * what it takes, because stepping needs somewhere to step to.
 */
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
	// The element, not the address: what a bare arrow does depends on what KIND of file is open, and
	// that is not known until the file's own details have come back. See the test below.
	await expect(page.locator('video')).toBeVisible();

	await page.keyboard.press('Shift+ArrowRight');
	await expect(page).toHaveURL(/\/asset\/p2/);

	await page.keyboard.press('Shift+ArrowLeft');
	await expect(page).toHaveURL(/\/asset\/p1/);
});

test('a bare arrow on a video seeks rather than stepping', async ({ page }) => {
	/* The reason stepping is on Shift at all. An arrow in a video player moves five seconds,
	 * which is what it does in every player anybody has used, so on a clip the bare key must not
	 * take the file away from under somebody who meant to skip an advert. */
	await serveTwo(page);
	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await expect(page).toHaveURL(/\/asset\/p1/);
	/* Waited for deliberately, and it is a real thing rather than test hygiene: the viewer only
	 * leaves a bare arrow alone once it knows the file is a video, so an arrow pressed in the moment
	 * before the details arrive steps rather than seeking. Under four browsers at once that moment
	 * is long enough to land in. */
	await expect(page.locator('video')).toBeVisible();

	await page.keyboard.press('ArrowRight');
	await page.waitForTimeout(200);

	await expect(page, 'a bare arrow stepped off the clip it was playing').toHaveURL(/\/asset\/p1/);
});
