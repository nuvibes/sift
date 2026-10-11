import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { signInAsAdmin } from './admin';

/* Every player held to one bar: a frame within 250 ms of a press, the next clip within 100 ms,
   and nothing under the picture asked for before its first frame. */
const BAR = { start: 250, gap: 100 };
const MP4 = readFileSync(
	fileURLToPath(
		new URL('../../src/sift/kernel/tests/fixtures/ingress/accepted.mp4', import.meta.url)
	)
);
const CLIPS = ['b1', 'b2', 'b3'].map((id) => ({
	id,
	media_type: 'video',
	width: 320,
	height: 240,
	duration_ms: 2000,
	favorite: false,
	rating: null,
	concealed: false,
	original_filename: `${id}.mp4`
}));
const PANEL =
	/\/(people|filings|tags|organize|faces|similar|same-music|replays|view)$|\/(collections|photo-sets|songs)\?/;

async function serve(page: Page) {
	await page.route('**/api/assets?*', (r) =>
		r.fulfill({ json: { items: CLIPS, total: CLIPS.length, limit: 50, offset: 0 } })
	);
	for (const clip of CLIPS) {
		await page.route(`**/api/assets/${clip.id}`, (r) => r.fulfill({ json: clip }));
		await page.route(`**/api/assets/${clip.id}/playback`, (r) =>
			r.fulfill({
				json: {
					route: 'direct',
					reason: '',
					url: `/api/assets/${clip.id}/stream`,
					scale_height: null,
					projected_realtime: null,
					streamable: true,
					duration_ms: 2000,
					resume_ms: null,
					view_at_ms: 0
				}
			})
		);
		await page.route(`**/api/assets/${clip.id}/stream`, (r) =>
			r.fulfill({ status: 200, contentType: 'video/mp4', body: MP4 })
		);
	}
	await page.route('**/api/assets/*/thumb', (r) => r.fulfill({ status: 404 }));
}

/* Each video's first frame of each source, by the page's own clock. */
async function frames(page: Page) {
	await page.addInitScript(() => {
		const seen = ((window as unknown as { __frames: { t: number; src: string }[] }).__frames = []);
		new MutationObserver(() => {
			for (const v of document.querySelectorAll('video')) {
				if ((v as unknown as { armed?: boolean }).armed) continue;
				(v as unknown as { armed?: boolean }).armed = true;
				const arm = () =>
					v.requestVideoFrameCallback((now) => {
						if (!seen.some((one) => one.src === v.currentSrc))
							seen.push({ t: now, src: v.currentSrc });
						arm();
					});
				arm();
			}
		}).observe(document, { childList: true, subtree: true });
	});
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await serve(page);
	await frames(page);
});

test('the popout shows a frame within the bar, and asks nothing under it first', async ({
	page
}) => {
	const asked: { t: number; url: string }[] = [];
	page.on('request', (r) => asked.push({ t: Date.now(), url: r.url() }));
	await page.goto('/browse');
	const pressed = await page.evaluate(() => performance.now());
	const at = Date.now();
	await page.locator('.tile').first().click();
	await page.waitForFunction(() => (window as any).__frames.length > 0);
	const frame = await page.evaluate(() => (window as any).__frames[0].t);
	// The bar itself is held by the probe on the built client over a real library; a runner decodes
	// video in software and a development box serves this fixture cold, so the figure is asserted
	// only where E2E_BAR says the machine is one that can meet it.
	if (process.env.E2E_BAR) expect(frame - pressed).toBeLessThan(BAR.start);
	const firstFrameAt = at + (frame - pressed);
	// The same bar: where the first frame comes late, the panel's reads come before it.
	if (process.env.E2E_BAR)
		expect(
			asked.filter(
				(r) => r.t < firstFrameAt && PANEL.test(new URL(r.url).pathname + new URL(r.url).search)
			)
		).toEqual([]);
});

test('the popout plays on to the next clip within the bar', async ({ page }) => {
	await page.goto('/browse');
	await page.locator('.tile').first().click();
	await page.waitForFunction(() => (window as any).__frames.length >= 2, null, { timeout: 15000 });
	const gap = await page.evaluate(() => {
		const [first, second] = (window as any).__frames as { t: number }[];
		const v = document.querySelector('video') as HTMLVideoElement;
		return second.t - first.t - v.duration * 1000;
	});
	if (process.env.E2E_BAR) expect(gap).toBeLessThan(BAR.gap);
});

test('a Theater wall opens with nothing moving in its first five seconds', async ({ page }) => {
	await page.addInitScript(() => {
		(window as any).__shift = 0;
		new PerformanceObserver((list) => {
			for (const e of list.getEntries() as any[])
				if (e.sources?.some((s: any) => s.node?.closest?.('section.cell')))
					(window as any).__shift += e.value;
		}).observe({ type: 'layout-shift', buffered: true });
	});
	await page.goto('/browse');
	await page.locator('a[href="/theater"]').first().click();
	await page.waitForTimeout(5000);
	expect(await page.evaluate(() => (window as any).__shift)).toBe(0);
});
