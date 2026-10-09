import { readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

import { type Page } from '@playwright/test';
import { expect, test } from './test';
import { signInAsAdmin } from './admin';
import { SETTINGS_SECTIONS } from '../src/lib/settings-ui/sections';

/*
 * Nothing on screen wears the browser's own look. The static gate (`check_no_native_chrome.js`)
 * cannot answer this: a snippet's control is dressed by the file that WRITES it, so the running
 * page is measured. An undressed control shows as Arial or monospace, 13.3333px, an outset or
 * inset border, or a pale grey background.
 */

/* The faces as `generated/fonts.css` declares them; "Variable" is part of the family name. */
const FACES = ['Archivo Variable', 'Instrument Sans Variable', 'Material Symbols Rounded'];

/** The size a control comes out at when nobody has said. */
const BROWSER_DEFAULT_SIZE = '13.3333px';

/** What one undressed control looked like, and where. */
interface Offence {
	where: string;
	what: string;
	why: string[];
}

/* Run inside the page, so it closes over nothing from out here. */
async function nativeChromeOn(page: Page, where: string): Promise<Offence[]> {
	return (await scan(page, where)).offences;
}

/** The scan, with a count of what the screen drew: an empty screen passes every absence check. */
async function scan(page: Page, where: string): Promise<{ offences: Offence[] }> {
	return page.evaluate(
		({ where, FACES, BROWSER_DEFAULT_SIZE }) => {
			// A range slider and a checkbox draw no glyphs, so their face is not visible.
			const TEXTUAL = new Set(['BUTTON', 'SELECT', 'TEXTAREA', 'SUMMARY']);
			const TEXT_INPUTS = new Set([
				'text',
				'password',
				'search',
				'url',
				'email',
				'number',
				'tel',
				'date',
				'submit',
				'button',
				'reset'
			]);

			const shows = (el: Element) => {
				const box = el.getBoundingClientRect();
				if (box.width === 0 || box.height === 0) return false;
				const style = getComputedStyle(el);
				return style.visibility !== 'hidden' && style.opacity !== '0';
			};

			/** A readable path to the thing, for a failure message somebody has to act on. */
			const nameOf = (el: Element) => {
				const bits = [el.tagName.toLowerCase()];
				const type = el.getAttribute('type');
				if (type) bits.push(`[type=${type}]`);
				const cls = (el.getAttribute('class') ?? '').trim().split(/\s+/).filter(Boolean);
				if (cls.length > 0) bits.push(`.${cls.join('.')}`);
				const text = (el.textContent ?? '').trim().slice(0, 30);
				if (text) bits.push(`"${text}"`);
				return bits.join('');
			};

			const found: { where: string; what: string; why: string[] }[] = [];
			const controls = document.querySelectorAll(
				'button, input, textarea, select, progress, meter, summary, dialog'
			);

			for (const el of controls) {
				if (!shows(el)) continue;
				const style = getComputedStyle(el);
				const why: string[] = [];

				/* A direct text node: an icon-only button's own family is never drawn. */
				const speaks = [...el.childNodes].some(
					(node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? '').trim() !== ''
				);

				/* And the words have to be visible: bits-ui's `PinInput` keeps one transparent
				 * monospace input over its cells, opacity 1 so it takes the keystrokes, and nothing
				 * of it is rendered. */
				const inked = !/^rgba\(.*,\s*0\)$/.test(style.color);

				const textual =
					inked &&
					((TEXTUAL.has(el.tagName) && speaks) ||
						(el.tagName === 'INPUT' && TEXT_INPUTS.has(el.getAttribute('type') ?? 'text')));

				if (textual) {
					const face = style.fontFamily.split(',')[0].replace(/["']/g, '').trim();
					if (!FACES.includes(face)) why.push(`font-family ${style.fontFamily}`);
					if (style.fontSize === BROWSER_DEFAULT_SIZE) {
						why.push(`font-size ${style.fontSize}, which is the browser's default`);
					}
				}

				// Every side: a control can be dressed on three and left on the fourth.
				for (const side of ['Top', 'Right', 'Bottom', 'Left'] as const) {
					const kind = style[`border${side}Style` as 'borderTopStyle'];
					if (kind === 'outset' || kind === 'inset') {
						why.push(`border-${side.toLowerCase()}-style: ${kind}`);
					}
				}

				/* Dark only, so a pale control is the browser's. Luminance, not two exact greys. */
				const rgb = /rgba?\(([^)]+)\)/.exec(style.backgroundColor);
				if (rgb) {
					const [r, g, b, a = '1'] = rgb[1].split(',').map((piece) => parseFloat(piece));
					const light = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
					if (Number(a) > 0.5 && light > 0.8) {
						why.push(`background ${style.backgroundColor} on a dark-only interface`);
					}
				}

				if (why.length > 0) found.push({ where, what: nameOf(el), why });
			}

			return { offences: found };
		},
		{ where, FACES, BROWSER_DEFAULT_SIZE }
	);
}

/** Wait until the pane has stopped drawing controls. See the settings loop for why. */
async function settled(page: Page): Promise<void> {
	const controls = page.locator('.pane').first().locator('button, input, select, textarea');
	let before = -1;
	for (let tries = 0; tries < 20; tries += 1) {
		const now = await controls.count();
		if (now === before) return;
		before = now;
		await page.waitForTimeout(150);
	}
}

function report(offences: Offence[]): string {
	return offences
		.map((one) => `  ${one.where}  ${one.what}\n      ${one.why.join('\n      ')}`)
		.join('\n');
}

test.beforeEach(async ({ page }) => {
	await signInAsAdmin(page);
	await page.setViewportSize({ width: 1400, height: 900 });
});

test('the detector notices a control nobody dressed', async ({ page }) => {
	/* The gate's own test: a scan that matches nothing also returns an empty list, so a planted
	 * bare button must be found. */
	await page.goto('/browse');

	await page.evaluate(() => {
		const bare = document.createElement('button');
		bare.textContent = 'Cancel';
		bare.id = 'undressed-on-purpose';
		document.body.append(bare);
	});

	const offences = await nativeChromeOn(page, '/browse');
	const caught = offences.filter((one) => one.what.includes('Cancel'));

	expect(caught.length, 'the scan did not notice a browser-default button').toBe(1);
	expect(caught[0].why.join(' ')).toContain('outset');
});

/* Every top-level route is in one of these two lists, and the test below fails if not: a new
 * route is either walked or skipped with the reason written down. */
const SCREENS = [
	'/browse',
	'/favorites',
	'/tags',
	'/collections',
	'/people',
	'/recent',
	'/organize',
	'/organize/known-people',
	/* `/organize/to-check` redirects to a tab of the faces page. */
	'/organize/faces-to-name',
	'/organize/folders',
	'/sites',
	'/downloads',
	'/hidden',
	'/theater',
	/* Photo sets and loops: a route that is not in one of these lists fails the check below. */
	'/photo-sets',
	'/loops',
	/* Its recaps open at `/insights/recaps/<id>`, inside this folder. */
	'/insights',
	/* The steps of a swap are reached only through a real session, so only its two doors here. */
	'/swap',
	'/songs',
	'/more'
	/* A username has no page: it is drawn on its person's and its Site's pages, checked above. */
];

const NOT_CHECKED_HERE: Record<string, string> = {
	opening:
		"the desktop window's own frame before sign-in, drawn by the shell and gone at the page's" +
		' first paint: no rail, no session, nothing of the app to check',
	accounts:
		"a username's old page address, kept for links written before the page went: a 308 redirect to" +
		' the Files wall narrowed to that username, with no page of its own',
	asset: 'opened over a screen rather than navigated to; covered by the item-detail checks',
	connect:
		"the desktop client's first screen, with no rail and no session, and in a browser it draws" +
		' a sentence rather than the form, because there is no shell here to save an address to',
	design: 'draws every component on purpose, including undressed samples; it IS the reference',
	library:
		"the phone's list of the rail's own destinations, links and nothing else: no control of its" +
		' own to check, and the rail it repeats is walked on every screen here',
	'library-location':
		"the shell's own second question, asked on the shell's origin before a backend exists, with" +
		' no rail, no session, and the same DoorCard, Note and Button that /connect is checked with',
	locked: 'no rail and no session, so checked by the lock-screen spec',
	login: 'no rail and no session, so checked by the sign-in spec',
	platforms:
		"the Sites wall's old address, kept for links written before the rename: a 308 redirect" +
		' with no page of its own, so opening it here would be opening /sites under a second name',
	profile: 'reached from the session menu; covered by the users spec',
	remote:
		'the phone driving what is open at the desk: with nothing open at a desk it draws no' +
		' control of its own, which is every run of this suite',
	search: 'a results view of /browse, drawn by the same components',
	settings: 'every pane is checked below, from what the app declares',
	setup: 'first run only, and there is no install to run it against here',
	start:
		"the shell's own first question, asked on the shell's origin before a backend exists, with no" +
		' rail, no session, and the same DoorCard, ChoiceCard and Button that /connect is checked with'
};

/* The fewest controls inside `main` (the rail is everywhere) for a screen's silence to mean
 * anything. One: Hidden with the vault shut is a single button, and the seeding does the rest. */
const FEWEST_CONTROLS = 1;

/* Content for the screens that are otherwise empty, mocked because the dressing is a property
 * of the markup. `thumb: true` matters: an importing tile has no controls. */
async function fillTheThinScreens(page: Page): Promise<void> {
	const json = (body: unknown) => ({
		status: 200,
		contentType: 'application/json',
		body: JSON.stringify(body)
	});

	const entity = (id: string, name: string) => ({
		id,
		name,
		favorite: false,
		rating: null,
		vault: false,
		shared: false,
		restricted: false,
		asset_count: 3,
		item_count: 3,
		account_count: 1,
		alias_count: 0,
		kind: 'site'
	});

	const asset = (id: string) => ({
		id,
		media_type: 'video',
		width: 1080,
		height: 1920,
		duration_ms: 61_000,
		favorite: false,
		rating: null,
		concealed: false,
		thumb: true,
		shared: false,
		restricted: false,
		shared_here: false,
		restricted_here: false,
		original_filename: `${id}.mp4`
	});

	const face = (trackId: string) => ({
		track_id: trackId,
		asset_id: 'a1',
		started_ms: 0,
		ended_ms: 4000,
		person_id: null,
		person_name: null,
		confidence: null,
		attribution: null
	});

	/* The board every Organize screen reads: a queue missing from it draws one sentence. */
	const queue = (name: string, title: string, rest: Record<string, unknown> = {}) => ({
		name,
		title,
		decision: `Say yes or no to a ${name} group.`,
		icon: 'inbox',
		band: 'decision',
		group: null,
		group_title: null,
		count: 2,
		pending: true,
		preview: [],
		...rest
	});

	const routes: [string, unknown][] = [
		[
			'**/api/workbench',
			{
				/* The queues this build has. `faces` names the group; `faces-to-name` and
				   `known-people` are tabs of the same page. */
				queues: [
					queue('folders', 'Folders that look like somebody'),
					queue('faces', 'Suggestions', { group: 'faces', group_title: 'Faces' }),
					queue('faces-to-name', 'To name', { group: 'faces' }),
					queue('known-people', 'People Sift can recognize', {
						band: 'record',
						group: 'faces',
						pending: false
					})
				],
				decisions: [],
				waiting: 6,
				decided: 0
			}
		],
		/* A page, `{items, total}`, not a bare list. */
		['**/api/collections*', { items: [entity('c1', 'A collection')], total: 1, offset: 0 }],
		['**/api/sites*', { items: [entity('s1', 'A site')], total: 1, offset: 0 }],
		['**/api/photo-sets*', { items: [entity('ps1', 'A shoot')], total: 1, limit: 60, offset: 0 }],
		[
			// A mark is a tile of its film, so it carries the film's id and shape.
			'**/api/loops*',
			{
				items: [
					{
						id: 'o1',
						asset_id: 'a1',
						name: 'A mark',
						start_ms: 1000,
						end_ms: 5000,
						duration_ms: 4000,
						media_type: 'video',
						width: 1080,
						height: 1920,
						favorite: false,
						rating: null,
						concealed: false,
						thumb: true
					}
				],
				total: 1,
				limit: 60,
				offset: 0
			}
		],
		[
			/* The groups the To name tab reads. */
			'**/api/faces/to-check*',
			{
				items: [
					{
						id: 'g1',
						kind: 'group',
						person_name: null,
						size: 2,
						best: null,
						status: 'open',
						faces: [face('f1'), face('f2')]
					}
				],
				total: 1,
				offset: 0,
				small_groups: 0
			}
		],
		[
			'**/api/faces/groups*',
			{
				groups: [{ id: 'g1', status: 'open', size: 2, faces: [face('f1'), face('f2')] }],
				total: 1,
				// Without it the pager reads NaN.
				offset: 0
			}
		],
		[
			// Named: one with no person is drawn without the control that acts on it.
			'**/api/faces/identified*',
			{
				items: [
					{ ...face('f1'), person_id: 'p1', person_name: 'Ada Lovelace', attribution: 'matched' },
					{ ...face('f2'), person_id: 'p1', person_name: 'Ada Lovelace', attribution: 'confirmed' }
				],
				total: 2
			}
		],
		[
			// A different address: `*` in a glob stops at a slash.
			'**/api/faces/identified/people*',
			{
				people: [
					{
						person_id: 'p1',
						person_name: 'Ada Lovelace',
						size: 2,
						waiting: 1,
						faces: [
							{
								...face('f1'),
								person_id: 'p1',
								person_name: 'Ada Lovelace',
								attribution: 'matched'
							},
							{
								...face('f2'),
								person_id: 'p1',
								person_name: 'Ada Lovelace',
								attribution: 'suggested'
							}
						]
					}
				],
				total: 1,
				offset: 0
			}
		],
		[
			'**/api/downloads*',
			{
				items: [{ id: 'd1', url: 'https://example.test/one', state: 'done', progress: 1 }],
				total: 1
			}
		],
		[
			/* One folder to name: with none the screen draws no controls. */
			'**/api/suggestions*',
			{
				proposals: [
					{
						id: 'claim-1',
						kind: 'person',
						proposed: 'bryn calloway',
						evidence: 'name_only',
						folder: 'Bryn Calloway',
						folder_id: 'folder-1',
						path: 'Models/Bryn Calloway',
						files: 3,
						art: {},
						group_id: null,
						face_id: null,
						face_art: null,
						near_miss: null,
						site: null,
						is_username: false,
						dissenting: [],
						per_file: [],
						cover: ''
					}
				],
				total: 1,
				offset: 0
			}
		],
		['**/api/assets?*', { items: [asset('a1'), asset('a2')], total: 2, limit: 60, offset: 0 }],
		// Its own address: a list of files somebody opened, not a page of the library.
		['**/api/recently-viewed*', { items: [asset('a1'), asset('a2')] }]
	];

	for (const [path, body] of routes) {
		await page.route(path, (route) =>
			route.request().method() === 'GET' ? route.fulfill(json(body)) : route.continue()
		);
	}
	await page.route('**/api/assets/*/thumb', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/faces/*/crop', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
	await page.route('**/api/faces/*/cover', (route) =>
		route.fulfill({ status: 200, contentType: 'image/png', body: PIXEL })
	);
}

/** One transparent pixel, as PNG: enough for a browser to lay a real thumbnail out. */
const PIXEL = Buffer.from(
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
	'base64'
);

for (const screen of SCREENS) {
	test(`nothing on ${screen} is wearing the browser's look`, async ({ page }) => {
		await fillTheThinScreens(page);
		await page.goto(screen);
		// The rail says the shell has drawn, not the screen inside it.
		await expect(page.locator('nav.rail')).toBeVisible();

		/* Polled: both a readiness wait and the positive control, since an empty screen passes. */
		const controls = page.locator(
			'main button:visible, main input:visible, main select:visible, main textarea:visible'
		);
		await expect
			.poll(() => controls.count(), {
				message:
					`${screen} drew no controls of its own, so finding no browser chrome on it would` +
					` prove nothing. Seed this screen with content before checking it, or move it to` +
					` NOT_CHECKED_HERE with the reason.`
			})
			.toBeGreaterThanOrEqual(FEWEST_CONTROLS);

		const { offences } = await scan(page, screen);
		expect(offences.length, `\n${report(offences)}\n`).toBe(0);
	});
}

test('every top-level screen is either checked here or excused by name', async () => {
	const dir = fileURLToPath(new URL('../src/routes', import.meta.url));
	const routes = (await readdir(dir, { withFileTypes: true }))
		.filter((entry) => entry.isDirectory() && !entry.name.startsWith('['))
		.map((entry) => entry.name);

	const checked = new Set(SCREENS.map((path) => path.split('/')[1]));
	const missing = routes.filter((name) => !checked.has(name) && !(name in NOT_CHECKED_HERE));

	expect(
		missing,
		`\nThese screens exist and this gate has never opened them:\n  ${missing.join('\n  ')}\n\n` +
			`Add each to SCREENS, or to NOT_CHECKED_HERE with the reason it does not belong.\n`
	).toEqual([]);
});

/* From what the app declares, so a new pane is gated the moment it exists. */
const SECTIONS = SETTINGS_SECTIONS.map((section) => section.id);

for (const section of SECTIONS) {
	test(`nothing in Settings > ${section} is wearing the browser's look`, async ({ page }) => {
		await page.goto(`/settings/${section}`);
		await expect(page.locator('.settings')).toBeVisible();

		/* Until what the pane drew stops changing: panes fetch their own content, and how many
		 * controls one has depends on the library behind it. */
		await page.waitForLoadState('networkidle');
		await settled(page);

		const offences = await nativeChromeOn(page, `/settings/${section}`);
		expect(offences.length, `\n${report(offences)}\n`).toBe(0);
	});
}

/* Panes whose controls exist only once a consent switch is on: switched on, measured, and left
 * as found, since the install is shared. */
const BEHIND_A_SWITCH = [
	{ section: 'semantic', switchLabel: /Search by what a picture looks like/i },
	{ section: 'faces', switchLabel: /Recognize faces in your library/i }
];

for (const { section, switchLabel } of BEHIND_A_SWITCH) {
	test(`nor anything Settings > ${section} only draws once it is switched on`, async ({ page }) => {
		await page.goto(`/settings/${section}`);
		await expect(page.locator('.settings')).toBeVisible();

		const consent = page.getByRole('switch', { name: switchLabel });
		// Not skipped when missing: a renamed switch would silently take this check off.
		const offered = await consent.count();
		if (offered === 0) {
			await expect(
				page.locator('.pane p').first(),
				`no "${switchLabel}" switch and no sentence saying why: has it been renamed?`
			).toBeVisible();
			return;
		}

		const wasOn = (await consent.getAttribute('aria-checked')) === 'true';
		if (!wasOn) await consent.click();

		try {
			// Waited for: between the click and the render the pane is empty.
			const controls = page.locator('.settings button:visible, .settings select:visible');
			await expect(async () => {
				expect(await controls.count()).toBeGreaterThan(1);
			}).toPass();

			const offences = await nativeChromeOn(page, `/settings/${section} (switched on)`);
			expect(offences.length, `\n${report(offences)}\n`).toBe(0);

			const square = await squareCorners(page, `/settings/${section} (switched on)`);
			expect(square, `\n${square.join('\n')}\n`).toEqual([]);
		} finally {
			if (!wasOn) await consent.click();
		}
	});
}

test('nor anything in a form that only appears when it is opened', async ({ page }) => {
	/* A form behind a button, where undressed controls collect. */
	const csrf = await page.request
		.get('/api/auth/me')
		.then(async (answer) => (await answer.json()).csrf_token as string);
	const made = await page.request.post('/api/sites', {
		data: { name: `e2e-chrome-${Date.now()}`, kind: null },
		headers: { 'x-csrf-token': csrf }
	});
	expect(made.ok(), await made.text()).toBeTruthy();
	const id = (await made.json()).id as string;

	await page.goto(`/sites/${id}`);
	/* Editing turns the record into one form with one Save. */
	await page.getByRole('button', { name: 'Edit', exact: true }).click();
	await expect(page.getByRole('form', { name: /^Editing / })).toBeVisible();

	const offences = await nativeChromeOn(page, `/sites/${id} (editing)`);
	expect(offences.length, `\n${report(offences)}\n`).toBe(0);
});

test('nor the list the search box drops down while somebody is typing', async ({ page }) => {
	/* The suggestions exist only once a key is pressed. */
	await page.goto('/browse');
	const box = page.getByRole('combobox', { name: 'Search' });
	await box.click();
	await box.fill('ta');
	await expect(page.getByRole('listbox')).toBeVisible();

	const offences = await nativeChromeOn(page, '/browse (search suggestions)');
	expect(offences.length, `\n${report(offences)}\n`).toBe(0);
});

test('nor the controls the sidebar only grows while it is being rearranged', async ({ page }) => {
	/* Rearranging is a mode: its buttons do not exist until it is asked for. */
	await page.goto('/browse');
	await page.getByRole('link', { name: 'Tags', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await expect(page.getByRole('button', { name: 'Done' })).toBeVisible();

	const offences = await nativeChromeOn(page, '/browse (rearranging the sidebar)');
	expect(offences.length, `\n${report(offences)}\n`).toBe(0);
});

/*
 * A button never has a square corner: a `var()` declared on a card resolves to nothing outside it,
 * and only the rendered page can tell. A deliberately bare control (no background, no border) and
 * a SCRIM covering the window are excused by shape, not by name.
 */
async function squareCorners(page: Page, where: string): Promise<string[]> {
	return page.evaluate((where) => {
		/* Measured against the viewport, so a merely large element is not excused. */
		const scrim = (element: Element, style: CSSStyleDeclaration) => {
			if (style.position !== 'fixed') return false;
			const box = element.getBoundingClientRect();
			return box.width >= window.innerWidth && box.height >= window.innerHeight;
		};

		const bare = (style: CSSStyleDeclaration) => {
			const background = style.backgroundColor;
			const invisible =
				background === 'transparent' || background === 'rgba(0, 0, 0, 0)' || background === '';
			return invisible && style.borderStyle === 'none';
		};

		/* Not `offsetParent`, null for a fixed element: overlays and toasts would be missed. */
		const drawn = (element: Element) => {
			const box = element.getBoundingClientRect();
			if (box.width === 0 || box.height === 0) return false;
			const style = getComputedStyle(element);
			return style.visibility !== 'hidden' && style.display !== 'none' && style.opacity !== '0';
		};

		return Array.from(document.querySelectorAll('button'))
			.filter(drawn)
			.filter((element) => {
				const style = getComputedStyle(element);
				if (bare(style)) return false;
				if (scrim(element, style)) return false;
				return ['borderTopLeftRadius', 'borderTopRightRadius'].every(
					(corner) => parseFloat(style[corner as never] as string) === 0
				);
			})
			.map((element) => {
				const label = (element.textContent ?? '').trim() || element.getAttribute('aria-label');
				return `${where}: "${label ?? '(no label)'}" has square corners`;
			});
	}, where);
}

for (const section of SECTIONS) {
	test(`no button in Settings > ${section} has a square corner`, async ({ page }) => {
		await page.goto(`/settings/${section}`);
		await expect(page.locator('.settings')).toBeVisible();

		const found = await squareCorners(page, `/settings/${section}`);
		expect(found, `\n${found.join('\n')}\n`).toEqual([]);
	});
}

for (const screen of SCREENS) {
	test(`no button on ${screen} has a square corner`, async ({ page }) => {
		await page.goto(screen);
		await expect(page.locator('nav.rail')).toBeVisible();

		const found = await squareCorners(page, screen);
		expect(found, `\n${found.join('\n')}\n`).toEqual([]);
	});
}

test('and the square-corner check can tell when a corner really is square', async ({ page }) => {
	/* A planted button with the radius taken off must be caught. */
	await page.goto('/browse');
	await expect(page.locator('nav.rail')).toBeVisible();

	await page.evaluate(() => {
		const planted = document.createElement('button');
		planted.textContent = 'Planted';
		planted.style.cssText =
			'position:fixed;top:0;left:0;background:#123;border:1px solid #456;border-radius:0;z-index:9999';
		document.body.append(planted);
	});

	const found = await squareCorners(page, '/browse');
	expect(found.some((one) => one.includes('Planted'))).toBe(true);
});

/* No file input is ever visible: no CSS reaches its "No file chosen" text, so it is hidden from
 * sight behind a styled label. The scans above cannot see it, so this checks the shape. */
async function visibleFilePickers(page: Page): Promise<string[]> {
	return page.evaluate(() =>
		Array.from(document.querySelectorAll('input[type="file"]'))
			.filter((element) => {
				const box = element.getBoundingClientRect();
				const style = getComputedStyle(element);
				// Bigger than the one-pixel clip the hidden ones use.
				return (
					box.width > 2 &&
					box.height > 2 &&
					style.visibility !== 'hidden' &&
					style.display !== 'none' &&
					style.opacity !== '0'
				);
			})
			.map((element) => element.id || element.className || '(unnamed file input)')
	);
}

for (const section of SECTIONS) {
	test(`no bare file picker in Settings > ${section}`, async ({ page }) => {
		await page.goto(`/settings/${section}`);
		await expect(page.locator('.settings')).toBeVisible();

		const bare = await visibleFilePickers(page);
		expect(
			bare,
			`\nA file input is on screen. The browser draws its own button and label inside one and no\n` +
				`CSS can reach them. Hide it the way the backup and Identify panes do (clipped to a\n` +
				`pixel, still in the page) and put a styled <label for> in front of it.\n  ${bare.join('\n  ')}\n`
		).toEqual([]);
	});
}

test('and that check can tell when a file picker is bare', async ({ page }) => {
	await page.goto('/settings/backup');
	await expect(page.locator('.settings')).toBeVisible();

	await page.evaluate(() => {
		const planted = document.createElement('input');
		planted.type = 'file';
		planted.id = 'planted-picker';
		document.body.append(planted);
	});

	expect(await visibleFilePickers(page)).toContain('planted-picker');
});

/* Controls that exist only once something is picked: Ctrl-click picks first, then measure. */
const PICKABLE = [
	{ screen: '/organize/faces-to-name', card: 'a.faces' },
	{ screen: '/organize/known-people', card: 'a.faces' },
	{ screen: '/browse', card: '.tile' }
];

for (const { screen, card } of PICKABLE) {
	test(`no button in the selection bar on ${screen} has a square corner`, async ({ page }) => {
		// Without them these walls have nothing to pick.
		await fillTheThinScreens(page);
		await page.goto(screen);
		await expect(page.locator('nav.rail')).toBeVisible();

		// Waited for: nothing to pick must FAIL, or an empty screen passes.
		const first = page.locator(card).first();
		await expect(
			first,
			`${screen} drew nothing to pick, so this would prove nothing`
		).toBeVisible();
		await first.click({ modifiers: ['Control'] });

		/* By role and name: `.bar` is the filter row's class too. */
		const bar = page.getByRole('region', { name: 'Selection' }).first();
		await expect(bar, 'picking something drew no selection bar').toBeVisible();

		const found = await squareCorners(page, `${screen} (selection bar)`);
		expect(found, `\n${found.join('\n')}\n`).toEqual([]);
	});
}
