/* The leaf and the bolt: what a read of the queue draws and says, and what a press sends. */

import { afterEach, describe, expect, it, vi } from 'vitest';

const sent = vi.hoisted(() => ({
	posts: [] as { path: string; body: unknown }[],
	refuse: false,
	refreshed: 0,
	toasts: [] as string[],
	/* Holds the press's answer back while a test looks at what is drawn meanwhile. */
	gate: null as Promise<void> | null,
	/* A queue read that left before the press, landing during the refresh. */
	stale: null as unknown,
	reading: null as Promise<void> | null,
	/* What the client throws for a refusal, as the mocked module hands it out. */
	Refused: class Refused extends Error {}
}));

vi.mock('$lib/api/client', () => ({
	ApiError: sent.Refused,
	api: {
		post: vi.fn(async (path: string, options?: { body?: unknown }) => {
			if (sent.gate) await sent.gate;
			if (sent.refuse) throw new sent.Refused('refused');
			sent.posts.push({ path, body: options?.body });
			const on = (options?.body as { on: boolean }).on;
			return { stepping_back: !on, turbo_mode: on, pressed: on };
		})
	}
}));

vi.mock('$lib/library/imports.svelte', () => ({
	imports: {
		page: null,
		refresh: vi.fn(async function (this: { page: unknown }) {
			sent.refreshed += 1;
			if (sent.stale !== null) this.page = sent.stale;
			if (sent.reading) await sent.reading;
		})
	}
}));
/* The app's client mode, as the shell answers it; a browser's answer unless a test says. */
const where = vi.hoisted(() => ({ yes: false }));
vi.mock('./sift-elsewhere.svelte', () => ({ siftElsewhere: where }));
vi.mock('$lib/shell/toasts.svelte', () => ({
	toasts: { show: vi.fn((message: string) => sent.toasts.push(message)) }
}));

import {
	currentTurboMode,
	ecoWhy,
	TURBO_MODE_COPY,
	turboModeSays,
	turboModeState,
	turboModeTip,
	leafDimmed,
	pressTurboMode,
	shareWords,
	usingShare
} from './turbo-mode';
import { imports } from '$lib/library/imports.svelte';

const held = imports as { page: unknown };

afterEach(() => {
	where.yes = false;
	held.page = null;
	sent.posts = [];
	sent.refuse = false;
	sent.refreshed = 0;
	sent.toasts = [];
	sent.gate = null;
	sent.stale = null;
	sent.reading = null;
});

const page = (running: number, stepping_back: boolean, turbo_mode: boolean) => ({
	counts: { running },
	stepping_back,
	turbo_mode
});

describe('which of the two is drawn', () => {
	it('is the leaf while tasks run on fewer workers because the device is in use', () => {
		expect(turboModeState(page(3, true, false), true)).toBe('less');
	});

	it('is the bolt while every worker runs because somebody pressed for it', () => {
		expect(turboModeState(page(6, false, true), true)).toBe('full');
	});

	it('is nothing while nobody is at the device: the full count by itself', () => {
		expect(turboModeState(page(12, false, false), true)).toBeNull();
	});

	it('is nothing with no tasks running, whichever flag is set', () => {
		expect(turboModeState(page(0, true, false), true)).toBeNull();
		expect(turboModeState(page(0, false, true), true)).toBeNull();
		expect(turboModeState({ stepping_back: true }, true)).toBeNull();
	});

	it('is nothing for a guest, or before the queue has been read', () => {
		expect(turboModeState(page(3, true, false), false)).toBeNull();
		expect(turboModeState(null, true)).toBeNull();
	});

	it('is nothing from a server older than the press, which never says turbo_mode', () => {
		expect(turboModeState({ counts: { running: 3 }, stepping_back: false }, true)).toBeNull();
	});
});

describe('what the tooltip says', () => {
	it('names eco mode, why, the share, and what a press does', () => {
		expect(turboModeTip('less', 25, 'input', [])).toBe(
			"In eco mode while you're working: using a quarter of this device. Press for turbo mode."
		);
		expect(turboModeTip('full', 25, 'input', [])).toBe(
			"Turbo mode on this device although you're working. Press to go back to eco mode."
		);
	});

	it('says a chosen share in words where it has one and as a percent otherwise', () => {
		expect(usingShare(50)).toBe("In eco mode while you're working: using half of this device");
		expect(shareWords(75)).toBe('three quarters');
		expect(shareWords(40)).toBe('40%');
	});

	it('says the default quarter for a server that does not name the share', () => {
		expect(usingShare(undefined)).toBe(
			"In eco mode while you're working: using a quarter of this device"
		);
	});

	it('names what other programs keep busy, where the server says', () => {
		expect(ecoWhy('others', ['graphics'])).toBe('other programs are using the GPU');
		expect(ecoWhy('others', ['processor', 'memory'])).toBe(
			'other programs are using the CPU and most of the memory'
		);
		expect(ecoWhy('others', ['processor', 'graphics', 'memory'])).toBe(
			'other programs are using the CPU, the GPU and most of the memory'
		);
		expect(ecoWhy('others', [])).toBe('other programs are busy');
		expect(ecoWhy('others', null)).toBe('other programs are busy');
		expect(ecoWhy('input', ['graphics'])).toBe("you're working");
		expect(turboModeSays('full', 25, 'others', ['processor'])).toBe(
			'Turbo mode on this device although other programs are using the CPU'
		);
	});

	it('reads why from the queue page the shell keeps: somebody here, or other programs', () => {
		held.page = { counts: { running: 2 }, stepping_back: true, step_back_for: 'input' };
		expect(turboModeTip('less')).toBe(
			"In eco mode while you're working: using a quarter of this device. Press for turbo mode."
		);
		expect(leafDimmed('less')).toBe(false);
		held.page = { step_back_share: 50, step_back_for: 'others', step_back_over: ['graphics'] };
		expect(turboModeTip('less')).toBe(
			'In eco mode while other programs are using the GPU: using half of this device. Press for turbo mode.'
		);
		expect(leafDimmed('less')).toBe(true);
		expect(leafDimmed('full')).toBe(false);
		held.page = { step_back_for: 'others' };
		expect(turboModeTip('less')).toBe(
			'In eco mode while other programs are busy: using a quarter of this device. Press for turbo mode.'
		);
		held.page = null;
		expect(leafDimmed('less')).toBe(false);
		expect(turboModeTip('less')).toBe(
			"In eco mode while you're working: using a quarter of this device. Press for turbo mode."
		);
	});
});

describe('a video playing', () => {
	it('is its own cause, said in one wording on the leaf, the bolt, the phone and Activity', () => {
		expect(ecoWhy('playing', ['graphics'])).toBe('a video is playing');
		held.page = { counts: { running: 2 }, stepping_back: true, step_back_for: 'playing' };
		expect(turboModeTip('less')).toBe(
			'In eco mode while a video is playing: using a quarter of this device. Press for turbo mode.'
		);
		expect(turboModeSays('full', 25)).toBe('Turbo mode on this device although a video is playing');
		expect(leafDimmed('less')).toBe(false);
	});
});

describe("in the app's client mode", () => {
	it('says the state is the device running Sift, never this one', () => {
		where.yes = true;
		held.page = { counts: { running: 2 }, stepping_back: true, step_back_for: 'input' };
		expect(turboModeTip('less')).toBe(
			"In eco mode while someone's working there: using a quarter of the device running Sift. Press for turbo mode."
		);
		expect(turboModeTip('full', 50, 'playing', [])).toBe(
			'Turbo mode on the device running Sift although a video is playing. Press to go back to eco mode.'
		);
		expect(usingShare(25, 'others', ['processor'], true)).toBe(
			'In eco mode while other programs are using the CPU: using a quarter of the device running Sift'
		);
	});
});

describe('the press', () => {
	it('sends what was asked for and reads the queue again immediately', async () => {
		await pressTurboMode(true);
		await pressTurboMode(false);
		expect(sent.posts).toEqual([
			{ path: '/jobs/turbo-mode', body: { on: true } },
			{ path: '/jobs/turbo-mode', body: { on: false } }
		]);
		expect(sent.refreshed).toBe(2);
	});

	it("draws the press's own answer immediately, without waiting for the queue's next read", async () => {
		held.page = { ...page(4, true, false), step_back_for: 'input' };
		await pressTurboMode(true);
		expect(held.page).toEqual({ ...page(4, false, true), step_back_for: 'input' });
		expect(turboModeState(held.page as Parameters<typeof turboModeState>[0], true)).toBe('full');
		await pressTurboMode(false);
		expect(turboModeState(held.page as Parameters<typeof turboModeState>[0], true)).toBe('less');
	});

	it('says so when the server refuses, and leaves the state where the server has it', async () => {
		sent.refuse = true;
		await pressTurboMode(true);
		expect(sent.toasts).toEqual([TURBO_MODE_COPY.failed]);
		expect(sent.refreshed).toBe(0);
	});

	it('draws the bolt on the press, before the server answers', async () => {
		held.page = { ...page(4, true, false), step_back_for: 'input' };
		let open = () => {};
		sent.gate = new Promise((resolve) => (open = resolve));
		const pressing = pressTurboMode(true);
		expect(currentTurboMode(true)).toBe('full');
		open();
		await pressing;
		expect(currentTurboMode(true)).toBe('full');
	});

	it('puts the leaf back when the server refuses the press', async () => {
		held.page = { ...page(4, true, false), step_back_for: 'input' };
		let open = () => {};
		sent.gate = new Promise((resolve) => (open = resolve));
		sent.refuse = true;
		const pressing = pressTurboMode(true);
		expect(currentTurboMode(true)).toBe('full');
		open();
		await pressing;
		expect(currentTurboMode(true)).toBe('less');
	});

	it('keeps the bolt over a queue read that left before the press', async () => {
		held.page = { ...page(4, true, false), step_back_for: 'input' };
		let open = () => {};
		sent.gate = new Promise((resolve) => (open = resolve));
		sent.stale = { ...page(4, true, false), step_back_for: 'input' };
		let read = () => {};
		sent.reading = new Promise((resolve) => (read = resolve));
		const pressing = pressTurboMode(true);
		open();
		await pressing;
		// The stale read has landed inside the refresh; the press stands until the refresh ends.
		expect(held.page).toEqual(sent.stale);
		expect(currentTurboMode(true)).toBe('full');
		read();
	});
});
