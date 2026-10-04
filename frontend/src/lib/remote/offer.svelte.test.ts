/*
 * A tab offering its open player or wall to the phone, and answering the phone's commands from the
 * same table its keys are answered from.
 *
 * What is worth proving: the application offers and a browser tab does not until it is switched
 * on; a heartbeat is quiet and a change is not; a command acts only on the screen it names and is
 * said back; and closing the surface takes the screen away.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { commanded, offerable, type Actions, type PlayerAction } from '$lib/shell/shortcuts';

import { screenChanges } from '$lib/library/changes.svelte';

import {
	controlledWords,
	labelFor,
	REPORT_EVERY_MS,
	ScreenOffer,
	type ScreenState,
	type Surface
} from './offer.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

const WINDOWS_CHROME =
	'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36';

let clock = 0;
let state: ScreenState;
let played: (number | null)[];

function aPlayer(): { kind: 'player'; actions: Actions<PlayerAction>; state: () => ScreenState } {
	return {
		kind: 'player',
		actions: {
			'player.playPause': ({ value }) => {
				played.push(value);
				return true;
			},
			'player.fill': () => true,
			'player.corner': () => true
		},
		state: () => state
	};
}

function anOffer(app = true): ScreenOffer {
	return new ScreenOffer(
		() => app,
		() => clock,
		() => WINDOWS_CHROME
	);
}

function reports(): Record<string, unknown>[] {
	return mocked.post.mock.calls.map(
		([, options]) => (options as { body: Record<string, unknown> }).body
	);
}

beforeEach(() => {
	vi.clearAllMocks();
	mocked.post.mockResolvedValue(undefined);
	mocked.del.mockResolvedValue(undefined);
	localStorage.clear();
	clock = 0;
	played = [];
	state = { playing: true, position: 10, length: 100, file: 'f1', volume: 80, muted: false };
});

describe('who offers', () => {
	it('offers from the desktop application by default', async () => {
		const offer = anOffer(true);
		offer.offer(aPlayer());
		await Promise.resolve();

		expect(mocked.post).toHaveBeenCalledTimes(1);
		expect(mocked.post.mock.calls[0][0]).toBe(`/remote/screens/${offer.screen}`);
		expect(reports()[0]).toMatchObject({
			label: 'The Sift app on Windows',
			surface: 'player',
			app: true,
			file: 'f1',
			// Full screen is never offered: a browser fills the screen only for a press at the desk.
			supports: ['player.playPause', 'player.corner']
		});
	});

	it('waits to be switched on in a browser tab, and remembers it', async () => {
		const offer = anOffer(false);
		offer.offer(aPlayer());
		expect(mocked.post).not.toHaveBeenCalled();

		offer.setThisBrowser(true);
		expect(mocked.post).toHaveBeenCalledTimes(1);
		expect(reports()[0]).toMatchObject({ label: 'Chrome on Windows', app: false });
		expect(anOffer(false).thisBrowser).toBe(true);
	});

	it('takes the screen away when the surface closes', async () => {
		const offer = anOffer(true);
		const close = offer.offer(aPlayer());
		close();

		expect(mocked.del).toHaveBeenCalledWith(`/remote/screens/${offer.screen}`);
	});

	it('mints a screen name the server takes, without a secure connection', () => {
		expect(anOffer().screen).toMatch(/^[A-Za-z0-9_-]{8,64}$/);
		expect(anOffer().screen).not.toBe(anOffer().screen);
	});
});

describe('what is said, and when', () => {
	it('keeps quiet over a heartbeat and speaks when the report is due', async () => {
		const offer = anOffer();
		offer.offer(aPlayer());
		clock = 1000;
		state = { ...state, position: 11 };
		await offer.look(false);
		expect(mocked.post).toHaveBeenCalledTimes(1);

		clock = REPORT_EVERY_MS;
		await offer.look(false);
		expect(mocked.post).toHaveBeenCalledTimes(2);
	});

	it('speaks at once for a pause and for a seek', async () => {
		const offer = anOffer();
		offer.offer(aPlayer());
		state = { ...state, playing: false };
		await offer.look(false);
		expect(mocked.post).toHaveBeenCalledTimes(2);

		state = { ...state, position: 60 };
		await offer.look(false);
		expect(mocked.post).toHaveBeenCalledTimes(3);
	});
});

describe('a command from the phone', () => {
	it('is answered from the table, with the state wanted, and said back', async () => {
		const offer = anOffer();
		offer.offer(aPlayer());
		offer.receive([{ id: 'c1', screen: offer.screen, action: 'player.playPause', value: 0 }]);
		await Promise.resolve();

		expect(played).toEqual([0]);
		expect(reports().at(-1)).toMatchObject({ acted_on: 'c1' });
	});

	it('is left alone when it names another screen, or a verb this surface lacks', () => {
		const offer = anOffer();
		offer.offer(aPlayer());
		mocked.post.mockClear();
		offer.receive([
			{ id: 'c1', screen: 'screen-somebody-else', action: 'player.playPause', value: 0 },
			{ id: 'c2', screen: offer.screen, action: 'player.next', value: null }
		]);

		expect(played).toEqual([]);
		expect(mocked.post).not.toHaveBeenCalled();
	});

	it('reaches nothing while the tab is not offering', () => {
		const offer = anOffer(false);
		offer.offer(aPlayer());
		offer.receive([{ id: 'c1', screen: offer.screen, action: 'player.playPause', value: 1 }]);
		expect(played).toEqual([]);
	});
});

describe('the label', () => {
	it('names the browser and the system, or the application', () => {
		expect(labelFor(true, WINDOWS_CHROME)).toBe('The Sift app on Windows');
		expect(labelFor(false, 'Mozilla/5.0 (Macintosh; Intel Mac OS X) Firefox/140.0')).toBe(
			'Firefox on a Mac'
		);
		expect(labelFor(false, 'Something else entirely')).toBe('A browser');
	});

	it('names an iPhone as one, although its own words say it is like a Mac', () => {
		const iphone =
			'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';
		expect(labelFor(false, iphone)).toBe('Safari on an iPhone or iPad');
	});

	it('names the computer the app is on when the shell says, so two desks read apart', () => {
		expect(labelFor(true, WINDOWS_CHROME, 'DESK-UPSTAIRS')).toBe('The Sift app on DESK-UPSTAIRS');
		expect(labelFor(true, WINDOWS_CHROME, 'DESK-DOWNSTAIRS')).toBe(
			'The Sift app on DESK-DOWNSTAIRS'
		);
		/* A browser tab has no shell; a name handed to it is not its own to say. */
		expect(labelFor(false, WINDOWS_CHROME, 'DESK-UPSTAIRS')).toBe('Chrome on Windows');
		expect(labelFor(true, WINDOWS_CHROME, null)).toBe('The Sift app on Windows');
	});

	it('asks the shell once, and reports the named label as soon as it is known', async () => {
		const name = vi.fn(async () => 'DESK-UPSTAIRS');
		const offer = new ScreenOffer(
			() => true,
			() => clock,
			() => WINDOWS_CHROME,
			name
		);
		offer.offer(aPlayer());
		offer.offer(aPlayer());
		await vi.waitFor(() => expect(reports().at(-1)?.label).toBe('The Sift app on DESK-UPSTAIRS'));
		expect(name).toHaveBeenCalledOnce();
	});

	it('never asks from a browser tab, which has no shell', () => {
		const name = vi.fn(async () => 'DESK-UPSTAIRS');
		const offer = new ScreenOffer(
			() => false,
			() => clock,
			() => WINDOWS_CHROME,
			name
		);
		offer.setThisBrowser(true);
		offer.offer(aPlayer());
		expect(name).not.toHaveBeenCalled();
		offer.setThisBrowser(false);
	});
});

describe('the drawer and the lists', () => {
	it('are said as the server takes them: cut to its longest list, a place kept only inside it', async () => {
		const offer = anOffer(true);
		state = {
			...state,
			repeat: 'loop_one',
			qualities: Array.from({ length: 40 }, (_, at) => `size ${at}`),
			quality: 35,
			layouts: ['single', 'grid'],
			layout: 1,
			cells: 2,
			cell_files: ['f1', 'f2', 'f3']
		};
		offer.offer(aPlayer());
		await Promise.resolve();

		expect(reports()[0]).toMatchObject({
			repeat: 'loop_one',
			shuffle: null,
			loop_marks: 0,
			quality: null,
			layout: 1,
			presets: [],
			cell_files: ['f1', 'f2']
		});
		expect((reports()[0].qualities as string[]).length).toBe(32);
	});

	it("lays the viewer's own marks over what it shows", async () => {
		const { offerViewer, screenOffer } = await import('./offer.svelte');
		let surface: Surface | null = null;
		const offer = vi.spyOn(screenOffer, 'offer').mockImplementation((given) => {
			surface = given;
			return () => {};
		});
		offerViewer(
			() => ({ actions: {}, state: () => state }),
			() => ({}),
			() => ({ favorite: true, count: 4 })
		);

		expect((surface as unknown as Surface).state()).toMatchObject({
			file: 'f1',
			favorite: true,
			count: 4
		});
		offer.mockRestore();
	});
});

describe('a viewer offering whatever it shows', () => {
	/* Offered by the video player alone, a popout showing a photograph or a GIF would offer
	   nothing and the phone would see no screen. The viewer offers once; every read and press goes to
	   whatever it shows now. */
	it('answers from the clip, then from the picture, with its own presses laid over', async () => {
		const { offerViewer, screenOffer } = await import('./offer.svelte');
		let surface: Surface | null = null;
		const offer = vi.spyOn(screenOffer, 'offer').mockImplementation((given) => {
			surface = given;
			return () => {};
		});
		const pressed: string[] = [];
		const clip = {
			actions: { 'player.playPause': () => (pressed.push('clip'), true) },
			state: () => ({
				playing: true,
				position: 3,
				length: 9,
				file: 'clip-1',
				volume: 50,
				muted: false
			})
		};
		const picture = {
			actions: { 'player.next': () => (pressed.push('picture'), true) },
			state: () => ({
				playing: false,
				position: 0,
				length: null,
				file: 'photo-1',
				volume: 50,
				muted: true
			})
		};
		let showing: typeof clip | typeof picture | null = clip;
		offerViewer(
			() => showing,
			() => ({ 'player.favorite': () => (pressed.push('favourite'), true) })
		);

		const offered = surface as unknown as Surface;
		expect(offered.state().file).toBe('clip-1');
		expect(offerable(offered.actions)).toEqual(
			expect.arrayContaining(['player.playPause', 'player.favorite'])
		);
		showing = picture;
		expect(offered.state().file).toBe('photo-1');
		expect(offerable(offered.actions)).toEqual(['player.next', 'player.favorite']);
		expect(commanded(offered.actions, 'player.next', null)).toBe(true);
		expect(commanded(offered.actions, 'player.playPause', 1)).toBe(false);
		showing = null;
		expect(offered.state().file).toBeNull();
		expect(pressed).toEqual(['picture']);
		offer.mockRestore();
	});
});

describe('being driven from a phone', () => {
	it('says which phones, as a sentence says them', () => {
		expect(controlledWords([])).toBe('');
		expect(controlledWords(['Chrome on Android'])).toBe('Remote controlled from Chrome on Android');
		expect(controlledWords(['Safari on an iPhone or iPad', 'Chrome on Android', 'Firefox'])).toBe(
			'Remote controlled from Safari on an iPhone or iPad, Chrome on Android and Firefox'
		);
	});

	it('reads its own entry on a bell, and forgets it when nothing is offered', async () => {
		const offer = anOffer(true);
		mocked.get.mockResolvedValue({
			screens: [
				{ screen: 'someone-else', controlled_by: ['Firefox'] },
				{ screen: offer.screen, controlled_by: ['Chrome on Android'] }
			],
			listed_for_seconds: 30
		} as never);
		const take = offer.offer({ ...aPlayer(), kind: 'theater', actions: {} });
		screenChanges.changed();
		await vi.waitFor(() => expect(offer.controlledBy).toEqual(['Chrome on Android']));
		expect(offer.wallControlled).toBe(true);

		take();
		expect(offer.controlledBy).toEqual([]);
		expect(offer.wallControlled).toBe(false);
	});
});

describe('a browser that holds something back', () => {
	it('says only that it exists while its switch is off and a player is open', async () => {
		const offer = anOffer(false);
		const close = offer.offer(aPlayer());

		expect(offer.holdingBack).toBe(true);
		expect(mocked.put).toHaveBeenCalledWith(`/remote/quiet/${offer.screen}`);
		expect(mocked.put.mock.calls[0]).toHaveLength(1);
		expect(mocked.post).not.toHaveBeenCalled();

		close();
		expect(offer.holdingBack).toBe(false);
		expect(mocked.del).toHaveBeenCalledWith(`/remote/quiet/${offer.screen}`);
	});

	it('stops holding back once it is switched on, and offers instead', () => {
		const offer = anOffer(false);
		offer.offer(aPlayer());
		offer.setThisBrowser(true);

		expect(mocked.del).toHaveBeenCalledWith(`/remote/quiet/${offer.screen}`);
		expect(mocked.post.mock.calls[0][0]).toBe(`/remote/screens/${offer.screen}`);
		offer.setThisBrowser(false);
	});

	it('says nothing from the application, which always offers, or from a tab with nothing open', () => {
		anOffer(true).offer(aPlayer());
		const idle = anOffer(false);
		idle.setThisBrowser(false);

		expect(mocked.put).not.toHaveBeenCalled();
		expect(idle.holdingBack).toBe(false);
	});
});
