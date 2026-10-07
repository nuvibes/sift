/* The account's own interface answers, read back and written through.
 *
 * One document, one request, and every key in it defaulting the way its absence should read. That
 * last part is what is actually worth a test: each of these keys was designed around WHICH WAY the
 * default falls, and a key that read as the wrong default while the one request was still in flight
 * would be a screen that changes its mind a moment after it is drawn.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.fn(async () => ({ state: {} as Record<string, string> }));
const put = vi.fn(async () => ({}));

vi.mock('$lib/api/client', () => ({ api: { get: (..._a: unknown[]) => get(), put: () => put() } }));

import {
	askBeforeFaceRemoval,
	faceRemovalConfirmSkipped,
	popoutLeavesToMini,
	recallInterfaceState,
	rememberPopoutLeavesToMini,
	rereadInterfaceState,
	skipFaceRemovalConfirm
} from './interface-state.svelte';

beforeEach(() => {
	rereadInterfaceState();
	get.mockClear();
	put.mockClear();
	get.mockResolvedValue({ state: {} });
});

describe('whether leaving the popout keeps the clip going', () => {
	it('is on before anything has been read, which is the answer that was asked for', () => {
		expect(popoutLeavesToMini()).toBe(true);
	});

	it('is on when the account has never answered', async () => {
		await recallInterfaceState();

		expect(popoutLeavesToMini()).toBe(true);
	});

	it('is off when the account said so', async () => {
		get.mockResolvedValue({ state: { 'popout.leave_to_mini': 'off' } });

		await recallInterfaceState();

		expect(popoutLeavesToMini()).toBe(false);
	});

	it('is on again when the account said that', async () => {
		get.mockResolvedValue({ state: { 'popout.leave_to_mini': 'on' } });

		await recallInterfaceState();

		expect(popoutLeavesToMini()).toBe(true);
	});

	it('reads back immediately what was just written, without waiting for the round trip', async () => {
		await recallInterfaceState();

		rememberPopoutLeavesToMini(false);

		expect(popoutLeavesToMini()).toBe(false);
		expect(put).toHaveBeenCalledTimes(1);

		rememberPopoutLeavesToMini(true);

		expect(popoutLeavesToMini()).toBe(true);
	});
});

describe('whether taking a face off a file asks first', () => {
	it('asks before anything has been read, which is the safe direction', () => {
		// A guard that reads as OFF while the one request is in flight is a guard somebody loses by
		// opening the page quickly.
		expect(faceRemovalConfirmSkipped()).toBe(false);
	});

	it('asks when the account has never answered', async () => {
		await recallInterfaceState();

		expect(faceRemovalConfirmSkipped()).toBe(false);
	});

	it('is one answer for both verbs, so ignoring and removing stop asking together', async () => {
		get.mockResolvedValue({ state: { 'confirm.face_removal': 'skip' } });

		await recallInterfaceState();

		expect(faceRemovalConfirmSkipped()).toBe(true);
	});

	it('reads back immediately what was just written, and can be put back', async () => {
		await recallInterfaceState();

		skipFaceRemovalConfirm();

		expect(faceRemovalConfirmSkipped()).toBe(true);
		expect(put).toHaveBeenCalledTimes(1);

		askBeforeFaceRemoval();

		expect(faceRemovalConfirmSkipped()).toBe(false);
	});
});
