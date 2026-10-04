/*
 * The Remote, the phone's screen for driving what plays at the desk.
 *
 * What would be silent if it broke: an empty list drawn as a blank page, the chooser offering a
 * screen that is not there or driving another than the one picked, a Hidden file named, a quiet screen still pressable, a transport control
 * drawn for a verb its screen never offered (which the server refuses), a drawer that rearranges
 * instead of dimming, a toggle sent as a flip rather than the state wanted, a choice sent as
 * anything but its place in the screen's own list, a wall's bar, cells or drawer missing, a
 * scrubber under Every cell that moves one cell, and a drawer that is not folded until opened.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { api } from '$lib/api/client';
import type { ScreenOut } from './wire';

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

/* The chooser's primitive captures the pointer, which jsdom does not implement. */
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
	if (!(name in Element.prototype)) {
		Object.defineProperty(Element.prototype, name, { value: () => false, writable: true });
	}
}

const Screen = (await import('./RemoteScreen.svelte')).default;
const { COPY, LOOP_STEPS } = await import('./copy');

const PLAYER = [
	'player.playPause',
	'player.back',
	'player.forward',
	'player.seekTo',
	'player.previous',
	'player.next',
	'player.volumeTo',
	'player.mute'
] as const;

/** What a screen says about its drawer and lists when it has none. */
const NOTHING_MORE = {
	repeat: null,
	shuffle: null,
	loop_marks: 0,
	qualities: [],
	quality: null,
	favorite: null,
	count: null,
	timer: null,
	every_cell: false,
	cell_held: null,
	cell_muted: null,
	layouts: [],
	layout: null,
	presets: [],
	cell_files: []
} satisfies Partial<ScreenOut>;

function aScreen(extra: Partial<ScreenOut> = {}): ScreenOut {
	return {
		screen: 'screen-desk',
		label: 'The Sift app on Windows',
		surface: 'player',
		app: true,
		playing: true,
		position: 83,
		length: 1312,
		file: 'f1',
		hidden: false,
		volume: 70,
		muted: false,
		supports: [...PLAYER],
		acted_on: null,
		cells: 0,
		focused: null,
		...NOTHING_MORE,
		heard_seconds_ago: 0,
		controlled_by: [],
		...extra
	};
}

const aWall = (extra: Partial<ScreenOut> = {}) =>
	aScreen({
		screen: 'screen-wall',
		label: 'Chrome on Windows',
		surface: 'theater',
		app: false,
		file: null,
		supports: [
			'theater.pauseAll',
			'theater.muteAll',
			'theater.cell',
			'theater.everyCell',
			'theater.previous',
			'theater.next',
			'theater.pause',
			'theater.mute',
			'theater.repeat',
			'theater.shuffle',
			'theater.loop',
			'theater.layout',
			'theater.preset',
			'theater.solo'
		],
		cells: 4,
		focused: 1,
		cell_files: [
			{ file: 'f1', hidden: false },
			{ file: null, hidden: true },
			{ file: null, hidden: false },
			{ file: null, hidden: false }
		],
		layouts: ['single', 'side_by_side', 'side_by_side_by_side', 'grid'],
		layout: 3,
		presets: ['Evening', 'Rainy day'],
		...extra
	});

let listed: ScreenOut[] = [];
let host: HTMLElement | undefined;
let drawn: ReturnType<typeof mount> | undefined;

beforeEach(() => {
	vi.clearAllMocks();
	localStorage.clear();
	listed = [];
	mocked.get.mockImplementation(async (path: string) =>
		path === '/remote/screens'
			? { screens: listed, listed_for_seconds: 30 }
			: { filename: 'beach.mp4' }
	);
	mocked.post.mockResolvedValue({ id: 'c-1' });
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	document.body.innerHTML = '';
});

async function draw(screens: ScreenOut[]): Promise<HTMLElement> {
	listed = screens;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Screen, { target: host });
	flushSync();
	await vi.waitFor(() => expect(host!.querySelector('.stack, .skeleton')).toBeNull());
	await Promise.resolve();
	flushSync();
	return host;
}

/** The fold the drawer sits under, by the words on its press. */
function fold(root: HTMLElement): HTMLButtonElement | undefined {
	return [...root.querySelectorAll<HTMLButtonElement>('button[aria-expanded]')].find((button) =>
		button.textContent?.includes(COPY.drawer)
	);
}

/** The card for these screens, with the drawer's fold opened, as a thumb opens it. */
async function open(...screens: ScreenOut[]): Promise<HTMLElement> {
	const root = await draw(screens);
	const press = fold(root);
	if (press?.getAttribute('aria-expanded') === 'false') {
		press.click();
		flushSync();
	}
	return root;
}

/** The glyph-only controls, by the names they say out loud. */
function labels(root: HTMLElement): string[] {
	return [...root.querySelectorAll('.row button')]
		.map((button) => button.getAttribute('aria-label') ?? '')
		.filter(Boolean);
}

function press(root: HTMLElement, label: string): void {
	const button = root.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);
	if (!button) throw new Error(`no button ${label}`);
	button.click();
	flushSync();
}

/** A press in a bar or a drawer, found by the word under its glyph. */
function worded(root: ParentNode, word: string): HTMLButtonElement {
	const found = [...root.querySelectorAll<HTMLButtonElement>('button')].find(
		(button) => button.querySelector('.label')?.textContent?.trim() === word
	);
	if (!found) throw new Error(`no press ${word}`);
	return found;
}

/** A drawer's words, in its order, each with whether it can be pressed. */
function drawer(root: HTMLElement): [string, boolean][] {
	const group = root.querySelector(`[role="group"][aria-label="${COPY.drawer}"]`);
	return [...(group?.querySelectorAll<HTMLButtonElement>('button') ?? [])].map((button) => [
		button.querySelector('.label')?.textContent?.trim() ?? '',
		!button.disabled
	]);
}

/** Open a menu: the library opens on pointerdown, not on click. */
function openMenu(button: HTMLElement): void {
	button.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	flushSync();
}

function menuRow(label: string): HTMLElement {
	const row = [
		...document.querySelectorAll<HTMLElement>(
			'[role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]'
		)
	].find((one) => one.textContent?.includes(label));
	if (!row) throw new Error(`no row ${label}`);
	return row;
}

/** Every command sent, as the screen it went to and what it asked. */
function sent(): [string, unknown][] {
	return mocked.post.mock.calls.map(([path, options]) => [
		path.replace('/remote/screens/', '').replace('/commands', ''),
		(options as { body: unknown }).body
	]);
}

describe('the Remote screen', () => {
	it('says how a screen gets here when there is none', async () => {
		const root = await open();

		expect(root.textContent).toContain(COPY.emptyTitle);
		expect(root.querySelector('section[aria-label]')).toBeNull();
	});

	it("drives the one screen there is, with no chooser, and names its file through the phone's door", async () => {
		const root = await open(aScreen());

		expect(root.querySelector('.head [aria-haspopup="menu"]')).toBeNull();
		expect(root.querySelector('.head')?.textContent?.trim()).toBe('The Sift app on Windows');
		await vi.waitFor(() => {
			flushSync();
			expect(root.querySelector('section h2, section h3')?.textContent?.trim()).toBe('beach.mp4');
		});
		expect(labels(root)).toEqual([
			COPY.previous,
			COPY.back,
			COPY.pause,
			COPY.forward,
			COPY.next,
			COPY.mute,
			COPY.fill
		]);
		expect(
			[...root.querySelectorAll('input[type="range"]')].map((one) => one.getAttribute('aria-label'))
		).toEqual([COPY.position, COPY.volume]);
	});

	it('draws no transport control for a verb the screen did not offer, and Full screen always dimmed', async () => {
		const root = await open(aScreen({ supports: ['player.playPause', 'player.fill'] }));

		expect(labels(root)).toEqual([COPY.pause, COPY.fill]);
		expect(
			root.querySelector<HTMLButtonElement>(`button[aria-label="${COPY.fill}"]`)?.disabled
		).toBe(true);
		expect(root.querySelector('input[type="range"]')).toBeNull();
	});

	it("draws the popout drawer's nine presses in its own order, dimming what the screen cannot do", async () => {
		const root = await open(aScreen({ supports: [...PLAYER, 'player.shuffle', 'player.loop'] }));

		expect(drawer(root)).toEqual([
			[COPY.clip, false],
			[COPY.screenshot, false],
			[COPY.quality, false],
			['Play through', false],
			[COPY.shuffle, true],
			[COPY.randomize, false],
			[LOOP_STEPS[0], true],
			[COPY.saveLoop, false],
			[COPY.stats, false]
		]);
	});

	it('sends the state it wants, never a flip', async () => {
		const root = await open(aScreen({ playing: false, muted: true }));

		press(root, COPY.play);
		press(root, COPY.unmute);
		press(root, COPY.back);

		expect(sent()).toEqual([
			['screen-desk', { action: 'player.playPause', value: 1 }],
			['screen-desk', { action: 'player.mute', value: 0 }],
			['screen-desk', { action: 'player.back', value: 5 }]
		]);
	});

	it('sends the state it wants to a playing screen too: paused, not toggled', async () => {
		const root = await open(aScreen({ playing: true, muted: false }));

		press(root, COPY.pause);
		press(root, COPY.mute);

		expect(sent()).toEqual([
			['screen-desk', { action: 'player.playPause', value: 0 }],
			['screen-desk', { action: 'player.mute', value: 1 }]
		]);
	});

	it("sends the drawer's presses as the desk reads them: the next repeat's place, the shuffle wanted, the loop", async () => {
		const root = await open(
			aScreen({
				supports: [
					...PLAYER,
					'player.repeat',
					'player.shuffle',
					'player.loop',
					'player.saveLoop',
					'player.random',
					'player.favorite',
					'player.count'
				],
				repeat: 'loop_all',
				shuffle: true,
				loop_marks: 2,
				favorite: false,
				count: 2
			})
		);

		expect(worded(root, COPY.shuffle).getAttribute('aria-pressed')).toBe('true');
		worded(root, 'Play through').click();
		worded(root, COPY.shuffle).click();
		worded(root, LOOP_STEPS[2]).click();
		worded(root, COPY.saveLoop).click();
		worded(root, COPY.randomize).click();
		press(root, 'O counter: 2');
		press(root, 'Add to favorites');

		expect(sent()).toEqual([
			['screen-desk', { action: 'player.repeat', value: 2 }],
			['screen-desk', { action: 'player.shuffle', value: 0 }],
			['screen-desk', { action: 'player.loop', value: null }],
			['screen-desk', { action: 'player.saveLoop', value: null }],
			['screen-desk', { action: 'player.random', value: null }],
			['screen-desk', { action: 'player.count', value: null }],
			['screen-desk', { action: 'player.favorite', value: 1 }]
		]);
	});

	it('sends a size as its place in the list the screen reported', async () => {
		const root = await open(
			aScreen({
				supports: [...PLAYER, 'player.quality'],
				qualities: ['1080p', '720p', '480p'],
				quality: 0
			})
		);

		openMenu(worded(root, COPY.quality));
		menuRow('720p').click();
		flushSync();

		expect(sent()).toEqual([['screen-desk', { action: 'player.quality', value: 1 }]]);
	});

	it('chooses among exactly the screens there are, and drives the one picked', async () => {
		const root = await draw([
			aScreen(),
			aWall(),
			aScreen({ screen: 'screen-lap', label: 'Firefox on a Mac' })
		]);

		const trigger = root.querySelector<HTMLElement>('.head .ui-select');
		expect(trigger, 'no chooser with three screens').not.toBeNull();
		trigger!.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		trigger!.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
		trigger!.click();
		flushSync();
		const rows = [...document.querySelectorAll<HTMLElement>('.ui-select-item')];
		const words = (one: HTMLElement) => (one.textContent ?? '').replace(/[^\x20-\x7e]/g, '').trim();
		expect(rows.map(words)).toEqual([
			'The Sift app on Windows',
			`Chrome on Windows, ${COPY.wall}`,
			'Firefox on a Mac'
		]);
		const lap = rows[2];
		lap.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
		lap.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
		lap.click();
		flushSync();

		expect(root.querySelector('section[aria-label]')?.getAttribute('aria-label')).toBe(
			'Firefox on a Mac'
		);
		expect(root.textContent).not.toContain('All screens');
	});

	it('leads with what plays, and puts which screen and the wall under the sound, over More controls', async () => {
		const wall = aWall();
		const root = await draw([
			{ ...wall, supports: [...wall.supports, 'theater.seekTo', 'theater.volumeTo'] }
		]);
		const card = root.querySelector('section')!;
		const order = (selector: string): number => {
			const found = card.querySelector(selector);
			expect(found, selector).not.toBeNull();
			return [...card.querySelectorAll('*')].indexOf(found!);
		};
		const named = order('h2, h3');
		const scrub = order('input[type="range"]');
		const sound = order(`input[aria-label="${COPY.volume}"]`);
		const head = order('.head');
		const bar = order(`[role="group"][aria-label="${COPY.wall}"]`);
		const cells = order(`[role="group"][aria-label="${COPY.cells}"]`);
		const more = [...card.querySelectorAll('*')].indexOf(fold(root)!);
		expect(named).toBeLessThan(scrub);
		expect(scrub).toBeLessThan(sound);
		expect(sound, 'which screen sits above the sound').toBeLessThan(head);
		expect(head).toBeLessThan(bar);
		expect(bar).toBeLessThan(cells);
		expect(cells, 'the wall sits under More controls').toBeLessThan(more);
	});

	it('says where the switch is while a browser holds a player back, with screens or none', async () => {
		mocked.get.mockImplementation(async (path: string) =>
			path === '/remote/screens'
				? { screens: listed, listed_for_seconds: 30, not_offering: 1 }
				: { filename: 'beach.mp4' }
		);
		const none = await draw([]);
		expect(none.querySelector('.holding-back')?.textContent).toBe(COPY.notOffering);
		// The empty state's own sentence, in its column, and not a second line under it.
		expect(none.querySelector('.holding-back')?.closest('.empty')).not.toBeNull();
		expect(none.textContent).not.toContain(COPY.empty);
		expect(COPY.notOffering).toBe(
			"A browser signed in on another computer isn't offering its screens. Turn on Settings > Playback > Remote > Let your phone control this browser there."
		);
		unmount(drawn!);
		drawn = undefined;
		host?.remove();

		const one = await draw([aScreen()]);
		expect(one.querySelector('.holding-back')?.textContent).toBe(COPY.notOffering);
	});

	it('says nothing about a switch while no browser holds anything back', async () => {
		const root = await draw([aScreen()]);
		expect(root.querySelector('.holding-back')).toBeNull();
	});

	it('folds the drawer under its name until it is opened', async () => {
		const root = await draw([aScreen({ supports: [...PLAYER, 'player.shuffle'] })]);

		const press = fold(root);
		expect(press?.getAttribute('aria-expanded')).toBe('false');
		expect(drawer(root)).toEqual([]);
		press!.click();
		flushSync();
		expect(drawer(root).map(([word]) => word)).toContain(COPY.shuffle);
	});

	it("dims a wall's scrubber under Every cell, saying why, and sends no position", async () => {
		const root = await open(
			aWall({ every_cell: true, length: 90, supports: [...aWall().supports, 'theater.seekTo'] })
		);

		const slider = root.querySelector<HTMLInputElement>('input[type="range"]');
		expect(slider?.getAttribute('aria-label')).toBe(COPY.position);
		expect(slider?.disabled).toBe(true);
		expect(root.querySelector('.times')?.textContent?.trim()).toBe(COPY.positionOfEvery);

		// One cell chosen: the same scrubber, live, over that cell's own times.
		unmount(drawn!);
		host!.remove();
		const one = await open(
			aWall({ every_cell: false, length: 90, supports: [...aWall().supports, 'theater.seekTo'] })
		);
		expect(one.querySelector<HTMLInputElement>('input[type="range"]')?.disabled).toBe(false);
		expect(one.querySelector('.times')?.textContent).toContain('1:30');
	});

	it("drives a wall: its bar, its cells as a strip, and the chosen cell's own presses", async () => {
		const root = await open(
			aWall({ playing: true, muted: false, cell_held: false, cell_muted: true })
		);

		const strip = root.querySelector(`[role="group"][aria-label="${COPY.cells}"]`)!;
		const cells = [...strip.querySelectorAll('button')];
		expect(cells.map((one) => one.querySelector('.name')?.textContent?.trim())).toEqual([
			'Cell 1',
			'Cell 2',
			'Cell 3',
			'Cell 4',
			COPY.everyCell
		]);
		expect(cells[1].getAttribute('aria-pressed')).toBe('true');

		worded(root, COPY.pauseAll).click();
		cells[3].click();
		cells[4].click();
		press(root, COPY.pause);
		press(root, COPY.unmute);
		worded(root, 'Play through').click();
		worded(root, COPY.solo).click();
		flushSync();

		expect(sent()).toEqual([
			['screen-wall', { action: 'theater.pauseAll', value: 1 }],
			['screen-wall', { action: 'theater.cell', value: 3 }],
			['screen-wall', { action: 'theater.everyCell', value: null }],
			['screen-wall', { action: 'theater.pause', value: 1 }],
			['screen-wall', { action: 'theater.mute', value: 0 }],
			['screen-wall', { action: 'theater.repeat', value: 2 }],
			['screen-wall', { action: 'theater.solo', value: null }]
		]);
	});

	it("sends a wall's preset as its place in the wall's own list", async () => {
		const root = await open(aWall());

		openMenu(worded(root, COPY.presets));
		menuRow('Rainy day').click();
		flushSync();

		expect(sent()).toEqual([['screen-wall', { action: 'theater.preset', value: 1 }]]);
	});

	it('says Something Hidden is playing, and asks for no name, for a file the phone keeps in Hidden', async () => {
		const root = await open(aScreen({ file: null, hidden: true }));

		expect(root.querySelector('section h2, section h3')?.textContent?.trim()).toBe(COPY.hidden);
		expect(mocked.get.mock.calls.map(([path]) => path)).toEqual(['/remote/screens']);
	});

	it('says only whether it plays when the list names no file', async () => {
		/* No file is both "nothing loaded" and "a file this phone cannot reach": the page guesses
		   neither. */
		const root = await open(aScreen({ file: null, playing: false }));

		expect(root.querySelector('section h2, section h3')?.textContent?.trim()).toBe(COPY.paused);
		expect(mocked.get.mock.calls.map(([path]) => path)).toEqual(['/remote/screens']);
	});

	it('drops a screen that went quiet', async () => {
		const root = await open(aScreen({ heard_seconds_ago: 31 }), aWall());

		expect(root.querySelector('.head [aria-haspopup="menu"]')).toBeNull();
		expect(root.querySelector('section[aria-label]')?.getAttribute('aria-label')).toBe(
			'Chrome on Windows'
		);
	});

	it('says in one line why a press was refused', async () => {
		const { ApiError } = await import('$lib/api/client');
		mocked.post.mockRejectedValue(
			new ApiError(409, "That's already done.", "That screen can't do that.")
		);
		const root = await open(aScreen());

		press(root, COPY.next);

		await vi.waitFor(() => {
			flushSync();
			expect(root.querySelector('.problem')?.textContent).toBe("That screen can't do that.");
		});
	});
});
