/*
 * The leaf and the bolt: which one a read of the queue draws, in every state the server can be in,
 * and what a press sends.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

const sent = vi.hoisted(() => ({
	posts: [] as { path: string; body: unknown }[],
	refuse: false,
	refreshed: 0,
	toasts: [] as string[],
	/* What the client throws for a refusal, as the mocked module hands it out. */
	Refused: class Refused extends Error {}
}));

vi.mock('$lib/api/client', () => ({
	ApiError: sent.Refused,
	api: {
		post: vi.fn(async (path: string, options?: { body?: unknown }) => {
			if (sent.refuse) throw new sent.Refused('refused');
			sent.posts.push({ path, body: options?.body });
			return {};
		})
	}
}));

vi.mock('$lib/library/imports.svelte', () => ({
	imports: {
		page: null,
		refresh: vi.fn(async () => {
			sent.refreshed += 1;
		})
	}
}));
vi.mock('$lib/shell/toasts.svelte', () => ({
	toasts: { show: vi.fn((message: string) => sent.toasts.push(message)) }
}));

import {
	FULL_AMOUNT_COPY,
	fullAmountState,
	fullAmountTip,
	pressFullAmount,
	shareWords,
	usingShare
} from './full-amount';

afterEach(() => {
	sent.posts = [];
	sent.refuse = false;
	sent.refreshed = 0;
	sent.toasts = [];
});

const page = (running: number, stepping_back: boolean, full_amount: boolean) => ({
	counts: { running },
	stepping_back,
	full_amount
});

describe('which of the two is drawn', () => {
	it('is the leaf while tasks run on fewer workers because the device is in use', () => {
		expect(fullAmountState(page(3, true, false), true)).toBe('less');
	});

	it('is the bolt while every worker runs because somebody pressed for it', () => {
		expect(fullAmountState(page(6, false, true), true)).toBe('full');
	});

	it('is nothing while nobody is at the device: the full count by itself', () => {
		expect(fullAmountState(page(12, false, false), true)).toBeNull();
	});

	it('is nothing with no tasks running, whichever flag is set', () => {
		expect(fullAmountState(page(0, true, false), true)).toBeNull();
		expect(fullAmountState(page(0, false, true), true)).toBeNull();
		expect(fullAmountState({ stepping_back: true }, true)).toBeNull();
	});

	it('is nothing for a guest, or before the queue has been read', () => {
		expect(fullAmountState(page(3, true, false), false)).toBeNull();
		expect(fullAmountState(null, true)).toBeNull();
	});

	it('is nothing from a server older than the press, which never says full_amount', () => {
		expect(fullAmountState({ counts: { running: 3 }, stepping_back: false }, true)).toBeNull();
	});
});

describe('what the tooltip says', () => {
	it('names the share in use and what a press does, in two sentences', () => {
		expect(fullAmountTip('less', 25)).toBe(
			"Using a quarter of this device while it's in use. Press to use the full amount."
		);
		expect(fullAmountTip('full', 25)).toBe(
			"Using the full amount of this device although it's in use. Press to use less system resources again."
		);
	});

	it('says a chosen share in words where it has one and as a percent otherwise', () => {
		expect(fullAmountTip('less', 50)).toBe(
			"Using half of this device while it's in use. Press to use the full amount."
		);
		expect(shareWords(75)).toBe('three quarters');
		expect(shareWords(40)).toBe('40%');
	});

	it('says the default quarter for a server that does not name the share', () => {
		expect(usingShare(undefined)).toBe("Using a quarter of this device while it's in use");
	});
});

describe('the press', () => {
	it('sends what was asked for and reads the queue again at once', async () => {
		await pressFullAmount(true);
		await pressFullAmount(false);
		expect(sent.posts).toEqual([
			{ path: '/jobs/full-amount', body: { on: true } },
			{ path: '/jobs/full-amount', body: { on: false } }
		]);
		expect(sent.refreshed).toBe(2);
	});

	it('says so when the server refuses, and leaves the state where the server has it', async () => {
		sent.refuse = true;
		await pressFullAmount(true);
		expect(sent.toasts).toEqual([FULL_AMOUNT_COPY.failed]);
		expect(sent.refreshed).toBe(0);
	});
});
