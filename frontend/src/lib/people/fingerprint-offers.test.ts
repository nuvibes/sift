/* The reads behind a group's fingerprints question and the Waiting for a face list. */
import { afterEach, expect, it, vi } from 'vitest';

const calls = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), del: vi.fn() }));

vi.mock('$lib/api/client', async (actual) => ({
	...(await actual<typeof import('$lib/api/client')>()),
	api: { get: calls.get, post: calls.post, del: calls.del }
}));

import { ApiError } from '$lib/api/client';
import {
	fingerprintOffers,
	fingerprintQuestion,
	makePersonFromFingerprints,
	removeWaitingFingerprints,
	waitingFingerprints
} from './fingerprint-offers';

afterEach(() => {
	calls.get.mockReset();
	calls.post.mockReset();
	calls.del.mockReset();
});

it('keys each offer by its group', async () => {
	calls.get.mockResolvedValue({ items: [{ pile_id: 'g1', name: 'Wren Halloway' }] });

	const offers = await fingerprintOffers();

	expect(calls.get).toHaveBeenCalledWith('/faces/fingerprints/offers');
	expect(offers.get('g1')?.name).toBe('Wren Halloway');
});

it('reads a refusal as no offers, so every card asks its ordinary question', async () => {
	calls.get.mockRejectedValue(new ApiError(403, 'forbidden'));

	expect((await fingerprintOffers()).size).toBe(0);
});

it('reads who is waiting for a face from the waiting list', async () => {
	calls.get.mockResolvedValue({ items: [] });

	expect(await waitingFingerprints()).toEqual({ items: [] });
	expect(calls.get).toHaveBeenCalledWith('/faces/fingerprints/waiting');
});

it('names the file a group came from and its faces, or says a fingerprints file when none is named', () => {
	expect(fingerprintQuestion({ name: 'Wren Halloway' })).toBe(
		'This group looks like Wren Halloway, from a facial fingerprints file. Create a person for them?'
	);
	expect(fingerprintQuestion({ name: 'Wren Halloway', source: 'Studio Faces', faces: 4 })).toMatch(
		/^This group looks like Wren Halloway, from Studio Faces \(.*4.*\)\. Create a person for them\?$/
	);
});

it('removes a waiting entry and makes a person from one by its id, escaped for the address', async () => {
	calls.post.mockResolvedValue({ person_id: 'p9' });

	await removeWaitingFingerprints('a/b');
	expect(calls.del).toHaveBeenCalledWith('/faces/fingerprints/waiting/a%2Fb');
	expect(await makePersonFromFingerprints('a/b')).toBe('p9');
	expect(calls.post).toHaveBeenCalledWith('/faces/fingerprints/a%2Fb/person');
});
