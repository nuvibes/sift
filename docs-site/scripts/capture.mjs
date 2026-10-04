// SPDX-License-Identifier: AGPL-3.0-or-later
// One photograph per docs page, of the screen the page describes, at 1600 wide.
//
//   SIFT_CAPTURE_PASSWORD=... node docs-site/scripts/capture.mjs --user name [--url http://host:port] [--out dir] [--only name]
//
// SIFT_PLAYWRIGHT names another Playwright to import, and SIFT_CHROMIUM a Chromium to launch (else an
// installed Chrome, else Playwright's own).
//
// --url defaults to Sift's own address on this computer. Signs in through the API as --user (the
// password is read from the environment, never an argument), opens each screen, waits for it to
// settle and writes `<section>-<page>.jpg` into the site's assets, or into --out. It only looks at
// the library: a shot with a preparation below presses the screen's own controls (a layout, Full
// screen), never anything that changes a file. --only takes one photograph, named exactly.
// It prints what it found on each screen, so a photograph of an empty or refused screen is said.
import { readFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = fileURLToPath(new URL('.', import.meta.url));
const site = join(here, '..');
const argv = process.argv.slice(2);
const option = (name, fallback) => {
	const at = argv.indexOf(`--${name}`);
	return at >= 0 && argv[at + 1] ? argv[at + 1] : fallback;
};
const base = option('url', 'http://127.0.0.1:5171').replace(/\/$/, '');
const out = option('out', join(site, 'src', 'assets', 'screens'));
const user = option('user', null);
const only = option('only', null);
const password = process.env.SIFT_CAPTURE_PASSWORD;
if (!user || !password) {
	console.error(
		'Name the account with --user and set SIFT_CAPTURE_PASSWORD to its password; the password is never taken as an argument.'
	);
	process.exit(2);
}

const playwrightAt =
	process.env.SIFT_PLAYWRIGHT ??
	join(site, '..', 'frontend', 'node_modules', 'playwright', 'index.mjs');
const { chromium } = await import(pathToFileURL(playwrightAt).href);

const menu = JSON.parse(readFileSync(join(site, 'generated', 'sidebar.json'), 'utf-8'));
const routeOf = Object.fromEntries(Object.entries(menu.routes).map(([href, page]) => [page, href]));

/** Each page with a screen, and the address that draws it. */
function shots() {
	const list = [
		['get-started-add-your-folders', '/settings/library'],
		['get-started-your-first-download', '/downloads'],
		// The phone's bar, at a phone's width: the one screen the page is about.
		['get-started-sift-on-your-phone', '/browse', 390]
	];
	for (const page of menu.library) list.push([page.replace('/', '-'), routeOf[`/${page}/`]]);
	for (const group of menu.settings) {
		for (const page of group.items) list.push([page.replace('/', '-'), `/${page}`]);
	}
	return only ? list.filter(([name]) => name === only) : list;
}

/** How long the wall may take to put something in every cell before the photograph is taken anyway. */
const CELLS_WAIT_MS = 20000;

/*
 * The Theater page shows the wall as somebody watches it: three cells in one row (1x3), the screen
 * filled by the page's own Full screen (the shell's box, so both bars come with it), and the pointer
 * resting on the bottom bar, which is what holds the chrome up past its idle clock.
 */
async function fillTheWall(page) {
	await page.getByRole('button', { name: 'Layouts' }).first().click();
	await page.getByRole('option', { name: '1x3' }).first().click();
	await page.keyboard.press('Escape');
	// The wall opens held when Autoplay is off; the bar's own play control lets every cell go.
	const play = page.getByRole('button', { name: 'Play everything' }).first();
	if (await play.isVisible().catch(() => false)) await play.click();
	await page
		.waitForFunction(
			() => {
				const cells = [...document.querySelectorAll('.wall video, .wall img')].filter(
					(one) => one.getBoundingClientRect().width > 0
				);
				const shown = cells.filter((one) =>
					one instanceof HTMLVideoElement
						? one.readyState >= 2 && one.currentTime > 0.5
						: one.complete && one.naturalWidth > 0
				);
				return shown.length >= 3;
			},
			null,
			{ timeout: CELLS_WAIT_MS }
		)
		.catch(() => console.log('library-theater: not every cell drew in time'));
	// F, the screen's own key for Full screen: the button sits in a cell's hover controls.
	await page.keyboard.press('f');
	await page.waitForFunction(() => document.fullscreenElement !== null, null, { timeout: 5000 });
	await page.waitForTimeout(1500);
	// The bottom band raises the chrome; resting on the bar itself then holds it past the idle clock.
	const view = page.viewportSize();
	await page.mouse.move(view.width / 2, view.height - 12, { steps: 4 });
	await page.waitForTimeout(700);
	// A spot on the bottom bar with no control under it, so no tooltip is drawn over the photograph.
	const rest = await page.evaluate(() => {
		const bar = document.querySelector('.stage-bar');
		const box = bar?.getBoundingClientRect();
		if (!box) return null;
		const y = box.top + box.height / 2;
		for (let x = box.right - 4; x > box.left; x -= 8) {
			const under = document.elementFromPoint(x, y);
			if (under && bar.contains(under) && !under.closest('button, [role="slider"], input')) {
				return { x, y };
			}
		}
		return null;
	});
	if (rest) await page.mouse.move(rest.x, rest.y, { steps: 4 });
	await page.waitForTimeout(800);
}

/* A folder's row says where the folder is on the computer being photographed. The page is about
 * the row, so the photograph shows an example path in its place. */
async function exampleFolderPath(page) {
	await page.evaluate(() => {
		const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
		for (let node = walker.nextNode(); node; node = walker.nextNode()) {
			const inSettings = node.parentElement?.closest('[role="dialog"]');
			if (inSettings && /^\s*(?:[A-Za-z]:\\|\/[^/\s]+\/)/.test(node.textContent ?? '')) {
				node.textContent = 'D:\\Media';
			}
		}
	});
}

/* Performance opens on a readout and a benchmark of the computer being photographed. The page
 * is about the settings under them, so the photograph starts at the first setting's row. */
async function atTheFirstSetting(page) {
	const reached = await page.evaluate(() => {
		const pane = document.querySelector('[role="dialog"] section.performance');
		const row = pane?.querySelector('[id="performance.concurrency"]') ?? null;
		row?.scrollIntoView({ block: 'start' });
		return row !== null;
	});
	if (!reached) console.log('settings-performance: the first setting was not found');
	await page.waitForTimeout(500);
}

/** Each photograph that needs the screen set up first, by the name it is written under. */
const preparations = {
	'library-theater': fillTheWall,
	'get-started-add-your-folders': exampleFolderPath,
	'settings-library': exampleFolderPath,
	'settings-performance': atTheFirstSetting
};

/*
 * An installed Chrome before Playwright's own Chromium, which has no H.264: with it every clip on
 * the Theater wall stays on its first frame at 0:00.
 */
async function launch() {
	if (process.env.SIFT_CHROMIUM)
		return chromium.launch({ executablePath: process.env.SIFT_CHROMIUM });
	try {
		return await chromium.launch({ channel: 'chrome' });
	} catch {
		console.log("No installed Chrome; Playwright's Chromium cannot play the wall's clips.");
		return chromium.launch();
	}
}
const browser = await launch();
const context = await browser.newContext({
	viewport: { width: 1600, height: 1000 }
});
const login = await context.request.post(`${base}/api/auth/login`, {
	data: { username: user, password }
});
if (!login.ok()) {
	console.error(`Sign-in refused at ${base}: ${login.status()}`);
	await browser.close();
	process.exit(1);
}
mkdirSync(out, { recursive: true });
let failed = 0;
for (const [name, address, width] of shots()) {
	// A page of its own for each shot, so a screen left filled or with a menu open cannot reach the next.
	const page = await context.newPage();
	try {
		await page.setViewportSize(width ? { width, height: 844 } : { width: 1600, height: 1000 });
		const answer = await page.goto(`${base}${address}`, {
			waitUntil: 'load',
			timeout: 30000
		});
		await page.waitForTimeout(1500);
		await preparations[name]?.(page);
		const seen = await page.evaluate(() => {
			/* Settings is drawn as a dialog over the screen; everything else in the main column. */
			const area = document.querySelector('[role="dialog"]') ?? document.querySelector('main');
			const heading = area?.querySelector('h1, h2')?.textContent ?? '';
			return {
				heading: heading.replace(/[^\x20-\x7E]/g, '').trim(),
				pictures: area?.querySelectorAll('img').length ?? 0,
				words: (area?.innerText ?? '').split(/\s+/).filter(Boolean).length
			};
		});
		// JPEG: a wall of photographs at this width is well over a megabyte as a PNG, and the site
		// turns every picture into WebP at build, so nothing is lost twice.
		await page.screenshot({ path: join(out, `${name}.jpg`), type: 'jpeg', quality: 85 });
		console.log(
			`${name}: ${address} ${answer?.status() ?? '-'} heading="${seen.heading}" pictures=${seen.pictures} words=${seen.words}`
		);
	} catch (error) {
		failed += 1;
		console.log(`${name}: ${address} FAILED ${String(error).split('\n')[0]}`);
	} finally {
		await page.close();
	}
}
await browser.close();
console.log(`${shots().length - failed} photographs in ${out}`);
process.exit(failed ? 1 : 0);
