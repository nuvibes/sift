import { readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

import { expect, test, type Page } from '@playwright/test';
import { signInAsAdmin } from './admin';
import { SETTINGS_SECTIONS } from '../src/lib/settings-ui/sections';

/*
 * Nothing on screen wears the browser's own look.
 *
 * There is a static gate for this as well (`scripts/check_no_native_chrome.js`), and it cannot
 * answer this question. A control's styling does not have to live in the file that writes the
 * control: Svelte compiles a `{#snippet}` in the scope of the file that WRITES it, so a button
 * handed to a component is dressed by a `:global` rule over there, in a file the reader of either
 * one never opens. Source that looks identical is styled in one place and bare in another. That is
 * exactly how a browser-default Cancel button lands beside a styled Save: `EntityHeader` dresses
 * the buttons in its `actions` snippet and says nothing about the ones in `fields`.
 *
 * So this measures the running page instead. What the browser draws for an undressed control is
 * unmistakable and is nothing the design system produces:
 *
 *   font-family    Arial, or monospace for a textarea: never one of the two faces the app ships
 *   font-size      13.3333px, the form-control default, not a step on any type scale
 *   border-style   outset on a button, inset on a field: the 3D borders of a 1996 dialog box
 *   background     rgb(239,239,239) or white, and this app is dark only
 *
 * Any one of them is the operating system drawing part of this interface.
 */

/* The faces the app ships, named exactly as `generated/fonts.css` declares them. The packages are
   the variable cuts, so "Variable" is part of the family name and not a description of it. */
const FACES = ['Archivo Variable', 'Instrument Sans Variable', 'Material Symbols Rounded'];

/** The size a control comes out at when nobody has said. */
const BROWSER_DEFAULT_SIZE = '13.3333px';

/** What one undressed control looked like, and where. */
interface Offence {
	where: string;
	what: string;
	why: string[];
}

/*
 * The scan, run inside the page.
 *
 * Written as one string of source rather than as a helper this file imports, because it is
 * evaluated in the browser and closes over nothing from out here.
 */
async function nativeChromeOn(page: Page, where: string): Promise<Offence[]> {
	return (await scan(page, where)).offences;
}

/**
 * The scan, and the reason the screen tests below also count what a screen drew.
 *
 * Every screen check here asserts a list is empty, and a screen that drew NOTHING produces an
 * empty list too. A screen whose data failed to load renders its "nothing is waiting" sentence and
 * no controls at all, and a check that only looked for absences would walk over it and pass.
 *
 * So a screen has to have had something on it for its silence to mean anything.
 */
async function scan(page: Page, where: string): Promise<{ offences: Offence[] }> {
	return page.evaluate(
		({ where, FACES, BROWSER_DEFAULT_SIZE }) => {
			// Controls that show text. The font checks only mean something for these: a range slider
			// and a checkbox draw no glyphs, so the face they inherit is not visible to anybody.
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

				/* Words of its own, rather than words somewhere inside it.
				 *
				 * An icon-only button holds a span that sets the symbol face itself, so the button's
				 * own family is never drawn and saying it is Arial says nothing. Asking for a direct
				 * text node is what tells "a button with a label on it" from "a button with a picture
				 * in it", and only the first can show the system's typeface to anybody.
				 */
				const speaks = [...el.childNodes].some(
					(node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? '').trim() !== ''
				);

				/*
				 * ...AND ITS WORDS HAVE TO BE VISIBLE, which is the same argument one line up
				 * carried to its end: a range slider and a checkbox are skipped because they draw
				 * no glyphs, and a control whose ink is fully transparent draws none either.
				 *
				 * `PinBox` is bits-ui's `PinInput`, which draws the cells you can see and keeps ONE
				 * real input over them holding the caret and the selection, styled, inline and by
				 * the library, as `color: transparent`, `background: transparent`, `caret-color:
				 * transparent` and `font-family: monospace`. Nothing of it is ever rendered.
				 * `shows()` cannot catch it, deliberately: it is opacity 1 with a real box, because
				 * it has to take the keystrokes.
				 *
				 * Without this, whichever pane holds the PIN control fails this check for a
				 * monospace face that is invisible to everybody: a deterministic fault that looks
				 * like a rotating flake as the control moves between panes.
				 */
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

				// Every side, because a control can be dressed on three of them and left on the fourth.
				for (const side of ['Top', 'Right', 'Bottom', 'Left'] as const) {
					const kind = style[`border${side}Style` as 'borderTopStyle'];
					if (kind === 'outset' || kind === 'inset') {
						why.push(`border-${side.toLowerCase()}-style: ${kind}`);
					}
				}

				/* Dark only, so a pale control is the browser's. Read as luminance rather than as the
				   two exact greys, or the check is defeated by a browser shipping a slightly different
				   default. Transparent parses to zero and is not pale. */
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
	/*
	 * The gate's own test, and it is not ceremony.
	 *
	 * Every other test here asserts that a list is empty, which is what a scan that silently matches
	 * nothing also does. A selector typo, a renamed face, a `document.querySelectorAll` that returns
	 * nothing on a page that failed to render: each one turns this whole file green and takes the
	 * gate off without a word. So one bare button is put on the page and the scan has to find it.
	 */
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

/* Screens with a rail on them, and the ones deliberately not checked here.
 *
 * These two lists together must account for every top-level route, and the test below fails if
 * they do not. A hand-kept list of screens looks complete and cannot be: adding a route does not
 * add it here, nothing fails, and the gate goes on reporting green over a screen it has never
 * opened. A list that is short is indistinguishable from a list that is finished.
 *
 * So a new route has to be put in one of these two, and putting it in the second means writing
 * down why. Skipping is allowed; skipping silently is not.
 */
const SCREENS = [
	'/browse',
	'/favorites',
	'/tags',
	'/collections',
	'/people',
	'/recent',
	'/organize',
	'/organize/known-people',
	/* The faces waiting for a name, as groups. `/organize/to-check` is a redirect to a tab of
	   the faces page, so it is not walked here. */
	'/organize/faces-to-name',
	'/organize/folders',
	'/sites',
	'/downloads',
	'/hidden',
	/* The wall's controls on the shared bar; a route missing from these lists is a route never
	   opened here, which is what this list exists to make impossible. */
	'/theater',
	/* Photo sets and loops: a route that is not in one of these lists fails the check below. */
	'/photo-sets',
	'/loops',
	/* The figures of what you viewed and organized, with the period tabs, their arrows and a chart.
	   Its recaps open at `/insights/recaps/<id>`, which is inside this folder. */
	'/insights',
	/* A swap with another Sift: its two doors, each a Button, drawn without asking the server
	   anything. The steps of a session are reached only through a real session, so they are NOT
	   walked here; the Swaps block on Updates and Info is walked with that pane below. */
	'/swap',
	/* The songs, on the Music page the rail names. */
	'/songs',
	/* The phone's list of every section of Settings, with Sign out at its foot: a real screen at a
	   real address on every width. */
	'/more'
	/* A username has no page of its own: it is stored and is not a place, and is drawn under its
	   Site on a person's page and under its person on a Site's page, both of which are checked
	   above. */
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

/* The fewest visible controls a screen has to have drawn for its silence to mean anything.
 *
 * The rail alone clears this, which is why the rail is not what is counted: the scan is scoped
 * to the whole document, so a screen with an empty middle still shows the rail's own buttons and
 * would pass a naive threshold. What is counted is controls inside `main`, where the screen's own
 * content is.
 *
 * **One, and the seeding above does the real work.** Some screens cannot reach more: Hidden with
 * the vault shut IS a single button. A higher floor would report a fault where there is none, and
 * a check that is red for reasons nobody can fix is a check people learn to ignore.
 *
 * One is also enough for the failure that matters: a screen whose data failed to load draws
 * NOTHING (zero controls) and passes every absence check. What stops that is refusing a
 * screen that drew nothing at all, and populating the walls so the screens with content are
 * genuinely walked.
 */
const FEWEST_CONTROLS = 1;

/* Content for the screens that are otherwise empty here.
 *
 * The check below is nothing but absence checks, and an empty screen passes every one of them,
 * which is why it also counts what the screen drew and refuses to bless a screen that drew almost
 * nothing. Against a library with no files, a wall draws its empty sentence and a control or two,
 * and that is not enough for "no browser chrome here" to mean anything.
 *
 * So they are given something to draw, the same way the concentric-corner spec does it. Mocked
 * rather than imported, because what is being measured is how the app DRESSES a control, which
 * is a property of the markup and the stylesheet, not of where the row came from.
 *
 * `thumb: true` on every asset is load-bearing: without it a tile is drawn as still importing,
 * and an importing tile has no controls on it at all.
 */
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

	/* The board every Organize screen reads before it draws anything.
	 *
	 * Without it the queue screens look their queue up, do not find it (the face queues are absent
	 * from a real board unless recognition is switched on), and draw one sentence saying so. That
	 * is a screen with no controls on it, which passes every absence check in this file and proves
	 * nothing, which is exactly what the floor below is for.
	 */
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
				/* THE QUEUES THIS BUILD HAS, and keeping them in step is not optional here. A
				   screen that looks itself up on this board and does not find itself draws one
				   sentence and no controls at all, and every check in this file then measures an
				   empty screen: the exact state the floor above exists to refuse.
				   The faces are one group whose first queue is named for it, `faces`, which names
				   the board's card; `faces-to-name` and `known-people` are tabs of the same page,
				   and `known-people` is the RECORD of its group. */
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
		/* A page, not a bare list: both of these answer `{items, total}`. A fixture describing a
		   bare list leaves the wall reading no rows and drawing its empty sentence, which passes
		   every absence check here. */
		['**/api/collections*', { items: [entity('c1', 'A collection')], total: 1, offset: 0 }],
		['**/api/sites*', { items: [entity('s1', 'A site')], total: 1, offset: 0 }],
		['**/api/photo-sets*', { items: [entity('ps1', 'A shoot')], total: 1, limit: 60, offset: 0 }],
		[
			// A mark is drawn as a tile of the film it was cut from, so it carries that film's id
			// and shape as well as its own ends.
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
			/* What the To name tab reads: the groups, from the faces list's own address, asked for by
			   kind. Without it the wall draws its empty sentence, which passes every absence check
			   in this file and proves nothing. */
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
				// The server always sends this; a page that lands on the four-hundredth row and reads
				// zero is a pager that lies, and a mock without it draws the readout as NaN.
				offset: 0
			}
		],
		[
			// Named, because the Identified screen is about faces that have been attributed: one
			// with no person on it is drawn without the name and without the control that acts on it.
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
			// The wall in front of that list, gathered by person. A different address, so a route for
			// the one above does not answer it: `*` in a glob stops at a slash. Missed, the screen
			// draws nothing, and a screen with nothing on it passes every absence check in this file.
			'**/api/faces/identified/people*',
			{
				people: [
					{
						person_id: 'p1',
						person_name: 'Ada Lovelace',
						size: 2,
						// One still waiting, so the card carries the control that acts on it.
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
			/* One folder to name, the shape `ProposalView` sends. With none the Folders screen draws
			   its empty sentence and no pager (an empty page has nothing to step through), which is a
			   screen with no controls on it. */
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
		// What the Recently viewed screen draws. Its own address rather than the wall's: the history
		// is a list of files somebody opened, not a page of the library.
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
		// The rail is on every one of these, so its presence says the shell has drawn. But it says
		// nothing about the screen inside it, which fetches its own content afterwards.
		await expect(page.locator('nav.rail')).toBeVisible();

		/* There has to be something to find, and waiting for it is the SAME check. See `scan`:
		   an empty screen passes any absence check and this file is nothing but absence checks.
		   Counting once, right after the rail appears, would race panels that have not come back
		   yet under load. Polled, it is a readiness wait and a positive control at the same time, and the
		   absence scan below cannot run against a screen that has not arrived. */
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

/* Taken from what the app declares rather than copied out of it. A pane added to the settings list
   is gated the moment it exists, which a second list here could never promise. */
const SECTIONS = SETTINGS_SECTIONS.map((section) => section.id);

for (const section of SECTIONS) {
	test(`nothing in Settings > ${section} is wearing the browser's look`, async ({ page }) => {
		await page.goto(`/settings/${section}`);
		await expect(page.locator('.settings')).toBeVisible();

		/*
		 * Wait for the pane to have finished drawing itself.
		 *
		 * The shell is visible long before the pane is: every pane fetches its own settings, and
		 * several fetch more than that: Performance asks what the machine is, and the table of
		 * facts and the control beside it do not exist until that answer lands. Scanning on the
		 * shell alone would measure an empty pane and bless it.
		 *
		 * Waiting for what the pane drew to stop changing, rather than for a number or a word. How
		 * many controls a pane has is a fact about the library behind it (Folders against an
		 * install with no folders draws none, and About has none to draw on any install), and a
		 * pane whose models are not installed sits on its own "Looking..." for as long as it likes.
		 * Settling is the one thing true of all of them.
		 */
		await page.waitForLoadState('networkidle');
		await settled(page);

		const offences = await nativeChromeOn(page, `/settings/${section}`);
		expect(offences.length, `\n${report(offences)}\n`).toBe(0);
	});
}

/*
 * The panes whose controls do not exist until a switch above them is on.
 *
 * The loop above opens every declared pane and measures what it finds, which sounds complete and is
 * not: a pane gated behind a consent switch draws a heading, a sentence and the switch, and NOTHING
 * else until somebody turns it on. Its buttons could be in the operating system's grey and the loop
 * would pass. It would not be wrong about what it saw: it never saw them.
 *
 * This is the same blind spot as the form behind a button below, and the same fix: put the screen
 * into the state its controls exist in, THEN measure. Written as a list because there is more than
 * one consent gate in the app and there will be more.
 *
 * The switch is left as it was found. These run against a shared install, and a gate that leaves a
 * feature switched on has changed the machine it was measuring.
 */
const BEHIND_A_SWITCH = [
	{ section: 'semantic', switchLabel: /Search by what a picture looks like/i },
	{ section: 'faces', switchLabel: /Recognize faces in your library/i }
];

for (const { section, switchLabel } of BEHIND_A_SWITCH) {
	test(`nor anything Settings > ${section} only draws once it is switched on`, async ({ page }) => {
		await page.goto(`/settings/${section}`);
		await expect(page.locator('.settings')).toBeVisible();

		const consent = page.getByRole('switch', { name: switchLabel });
		// Not skipped when it is missing. A machine that cannot run the feature says so instead of
		// offering the switch, and that is a fair reason for there to be nothing here. But a
		// RENAMED switch looks identical from out here, and would silently take this check off.
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
			// The controls the switch reveals, waited for rather than assumed: measuring between the
			// click and the render is measuring an empty pane, which passes every check in this file.
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
	/*
	 * A form behind a button.
	 *
	 * It is invisible to a scan of the screens above, and it is where undressed controls collect:
	 * the buttons somebody writes last, in a branch that is closed by default. A gate that only
	 * looked at what a page shows on arrival would pass with a wrong Cancel button.
	 */
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
	/* One press. The record is on the page rather than in a dialog, and editing it takes the
	   page: the whole record becomes one form with one Save, which is the shape this scan wants:
	   every control in it is written last, in a branch that is closed by default. */
	await page.getByRole('button', { name: 'Edit', exact: true }).click();
	await expect(page.getByRole('form', { name: /^Editing / })).toBeVisible();

	const offences = await nativeChromeOn(page, `/sites/${id} (editing)`);
	expect(offences.length, `\n${report(offences)}\n`).toBe(0);
});

test('nor the list the search box drops down while somebody is typing', async ({ page }) => {
	/*
	 * The same blind spot again, on the control that is on every screen.
	 *
	 * The suggestions do not exist until a key is pressed, so every scan in this file would walk
	 * past them. Rename a class on the list and every rule keyed on the old name as an ancestor
	 * stops matching immediately, while the markup and the stylesheet each still read correctly.
	 */
	await page.goto('/browse');
	const box = page.getByRole('combobox', { name: 'Search' });
	await box.click();
	await box.fill('ta');
	// The list is what is being measured, so it has to be there before anything is measured.
	await expect(page.getByRole('listbox')).toBeVisible();

	const offences = await nativeChromeOn(page, '/browse (search suggestions)');
	expect(offences.length, `\n${report(offences)}\n`).toBe(0);
});

test('nor the controls the sidebar only grows while it is being rearranged', async ({ page }) => {
	/*
	 * The same blind spot as the form above, on the other side of the shell.
	 *
	 * Rearranging is a mode: until somebody asks for it, the buttons that put a row away and the one
	 * that ends the mode do not exist, so a scan of the screens above walks straight past them. They
	 * are also exactly the kind of control that collects browser defaults: written last, in a
	 * branch that is closed by default, and never seen by anybody not looking for them.
	 */
	await page.goto('/browse');
	await page.getByRole('link', { name: 'Tags', exact: true }).click({ button: 'right' });
	await page.getByRole('menuitem', { name: 'Rearrange' }).click();
	await expect(page.getByRole('button', { name: 'Done' })).toBeVisible();

	const offences = await nativeChromeOn(page, '/browse (rearranging the sidebar)');
	expect(offences.length, `\n${report(offences)}\n`).toBe(0);
});

/*
 * A button never has a square corner.
 *
 * The other half of the resolvable-property gate, and the half a static reader cannot do.
 *
 * `border-radius: var(--inner)` where `--inner` is declared on the CARD, in a rule that also dresses
 * a button on the form OUTSIDE the card, resolves to nothing out there. The declaration is
 * dropped, the corner comes out square, and the CSS is valid and reads as though it set a radius.
 * The property IS declared in that file, so nothing reading the text a file at a time can tell.
 * The rendered page can: the corner is either round or it is not.
 *
 * Every button in the design system is rounded. A control that is deliberately bare (a thumbnail
 * that is a button so it can be pressed, an icon with no box) has no background and no border
 * either, and those are excused here by that shape rather than by name, so a new one does not have
 * to be added to a list somebody will forget.
 *
 * A SCRIM is the second such shape, since every settings address is a panel:
 * the dimmed sheet behind a sheet is a `<button>` on purpose: clicking beside a panel is how
 * everybody closes one, and being a button is what announces it as dismissible rather than as
 * decoration. It is also the whole window, and a rounded corner on the whole window would show the
 * page behind it in four little curves. So it is excused by covering the viewport, which is a
 * shape, and not by its label, which is a name.
 */
async function squareCorners(page: Page, where: string): Promise<string[]> {
	return page.evaluate((where) => {
		/* The whole window, pinned to it. Measured against the viewport rather than trusted from
		   `inset: 0`, so an element that merely happens to be large is not excused. */
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

		/* Drawn, asked of the box and the computed style rather than of `offsetParent`.
		 *
		 * `offsetParent` is null for a `position: fixed` element, which is not hidden, it is
		 * pinned to the window, and it is what an overlay, a toolbar and a toast all are.
		 * Filtering on it would make this gate blind to exactly the controls that float above
		 * everything; the self-test below plants a fixed button to prove it is seen.
		 */
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
	/* The gate above is worth exactly as much as its ability to fail. A planted button, dressed the
	   way a real one is and with the radius taken off, must be caught. */
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

/*
 * No file input is ever visible.
 *
 * This is the one control a page genuinely cannot restyle. The browser draws its own button and its
 * own "No file chosen" beside it, in the operating system's look and typeface, and no CSS reaches
 * either: `::file-selector-button` gets you the button's box and nothing gets you the text. So
 * the app's answer is to hide the input from SIGHT and not from the page, and put a styled label in
 * front of it: the label opens it, and the keyboard and a screen reader still reach the input.
 *
 * The scan above cannot see a visible one: it measures the input's OWN font and border, which are
 * fine, while the part that is wrong belongs to a pseudo-element; and the square-corner rule misses
 * it because the browser's button is not a `<button>`.
 *
 * So this checks the shape rather than the styling: a file input that takes up space on screen is
 * the fault, whatever it looks like.
 */
async function visibleFilePickers(page: Page): Promise<string[]> {
	return page.evaluate(() =>
		Array.from(document.querySelectorAll('input[type="file"]'))
			.filter((element) => {
				const box = element.getBoundingClientRect();
				const style = getComputedStyle(element);
				// Bigger than the one-pixel clip the hidden ones use, and not hidden outright.
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

/*
 * The controls that only exist once you have picked something.
 *
 * The sweep above walks every screen and measures the buttons it finds, which is every button that
 * is DRAWN, and a selection bar is not drawn until a selection exists. A check that can only fail
 * on what is already on screen quietly stops covering anything put behind a gesture.
 *
 * These make the selection first, then measure. Ctrl-click is the picking gesture on a mouse and
 * needs no long press, so it is what a test can drive reliably.
 */
const PICKABLE = [
	{ screen: '/organize/faces-to-name', card: 'a.faces' },
	{ screen: '/organize/known-people', card: 'a.faces' },
	{ screen: '/browse', card: '.tile' }
];

for (const { screen, card } of PICKABLE) {
	test(`no button in the selection bar on ${screen} has a square corner`, async ({ page }) => {
		// The same fixtures the sweep above uses. Without them these walls are empty, and an empty
		// wall has nothing to pick, which is the state this whole block exists to refuse.
		await fillTheThinScreens(page);
		await page.goto(screen);
		await expect(page.locator('nav.rail')).toBeVisible();

		// Waited for rather than counted. Counting the moment the rail appears asks before the
		// fixture has been drawn, gets zero, and skips, which reads as a pass and covers nothing.
		// Nothing to pick has to be a FAILURE here: an empty screen satisfies every absence check
		// in this file, and that is the exact hole this block exists to close.
		const first = page.locator(card).first();
		await expect(
			first,
			`${screen} drew nothing to pick, so this would prove nothing`
		).toBeVisible();
		await first.click({ modifiers: ['Control'] });

		/* The bar has to actually be there, or the measurement below is over an empty set again.
		 *
		 * Named by its ROLE and its accessible name rather than by `.bar`: that class is not the
		 * selection bar's alone: the screen's own filter row wears it too, earlier in the
		 * document, and is `display: none` when the screen has no filters. The region and its
		 * label are what the component promises to a screen reader, so they are the thing to hold
		 * it to.
		 */
		const bar = page.getByRole('region', { name: 'Selection' }).first();
		await expect(bar, 'picking something drew no selection bar').toBeVisible();

		const found = await squareCorners(page, `${screen} (selection bar)`);
		expect(found, `\n${found.join('\n')}\n`).toEqual([]);
	});
}
