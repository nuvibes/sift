/* The swap screens' own reading of a session: which step it is on, the estimate's words, the
 * pasted token as one string, the ended session's words and the tunnel's "can host" words.
 *
 * The step is where the one rule that matters most lives (the code comes before the offer) so
 * it is held here, over plain values, rather than only through a drawn page.
 */

import { describe, expect, it, vi } from 'vitest';
import { NOT_ENOUGH_TO_SAY } from '$lib/shell/when';

const posted = vi.hoisted(() => [] as { path: string; body: unknown }[]);
vi.mock('$lib/api/client', async (real) => ({
	...(await real<typeof import('$lib/api/client')>()),
	api: {
		post: async (path: string, options?: { body?: unknown }) => {
			posted.push({ path, body: options?.body });
			return { session_id: 's1' };
		}
	}
}));

import {
	canHostWords,
	cutOffWords,
	endedWords,
	joinSwap,
	inFours,
	leftOutWords,
	receivedWords,
	sendingWords,
	startBlocked,
	stageOf,
	timeLeft,
	timeLeftBothWays,
	tokenFrom,
	type SwapSession
} from './swap';

function session(over: Partial<SwapSession> = {}): SwapSession {
	return {
		two_way: false,
		answered: false,
		sending: null,
		receiving: null,
		id: 'S1',
		short_id: 'S1',
		role: 'guest',
		state: 'connected',
		code: 'K7Q2ZP',
		token: null,
		sentence: null,
		peer_device: 'ABCD-EFGH',
		offered_files: 0,
		wanted_files: 0,
		sent_files: 0,
		sent_bytes: 0,
		wanted_bytes: null,
		rate_bps: null,
		unwanted_files: 0,
		started_at: 1,
		ended_at: null,
		end_reason: null,
		offer: null,
		rejoin_until: null,
		// Received is the receiving side's own count while the session runs; none here.
		received_files: null,
		...over
	};
}

const OFFER = {
	layout: 'rows' as const,
	rows: [],
	everyone: [],
	offered_files: 3,
	offered_bytes: 30,
	files: 3,
	bytes: 30,
	shared: 0,
	unfiled_files: 0,
	unfiled_bytes: 0,
	held: []
};

describe('the step a session is on', () => {
	it('shows the host its token while it waits, and nothing else', () => {
		expect(stageOf(session({ role: 'host', state: 'waiting', token: 'AAAA' }), false)).toBe(
			'token'
		);
		expect(stageOf(session({ state: 'waiting', code: null }), false)).toBe('connecting');
	});

	it('holds the offer behind the code until the person here has compared it', () => {
		const offered = session({ state: 'offered', offer: OFFER });
		expect(stageOf(offered, false)).toBe('code');
		expect(stageOf(offered, true)).toBe('offer');
		expect(stageOf(offered, true, true)).toBe('starting');
	});

	it('waits for them once compared, on either side', () => {
		expect(stageOf(session({ role: 'host' }), true)).toBe('waiting-for-them');
		expect(stageOf(session({ role: 'host', state: 'offered' }), true)).toBe('waiting-for-them');
		expect(stageOf(session(), true)).toBe('waiting-for-them');
		expect(stageOf(session({ code: null }), true)).toBe('connecting');
	});

	it('moves and ends by the server alone', () => {
		expect(stageOf(session({ state: 'transferring' }), false)).toBe('progress');
		for (const state of ['done', 'ended', 'failed'] as const) {
			expect(stageOf(session({ state }), true)).toBe('ended');
		}
	});
});

describe("the estimate, from the session's own rate", () => {
	/* The same words every estimate says while it is measuring, from the one rule in `$lib/shell/when`,
	   never a sentence of the swap's own. */
	it('says there is not enough to say yet until a rate has been measured', () => {
		expect(timeLeft({ rate_bps: null, wanted_bytes: 1000, sent_bytes: 0 })).toBe(NOT_ENOUGH_TO_SAY);
		expect(timeLeft({ rate_bps: 0, wanted_bytes: 1000, sent_bytes: 0 })).toBe(NOT_ENOUGH_TO_SAY);
		expect(timeLeft({ rate_bps: 8000, wanted_bytes: null, sent_bytes: 0 })).toBe(NOT_ENOUGH_TO_SAY);
		expect(NOT_ENOUGH_TO_SAY).toBe('Not enough to say yet');
	});

	it('divides what is left by the rate, in bits', () => {
		// 3 GB left at 10 Mbit/s: 3e9 * 8 / 1e7 = 2400 s = 40 minutes, said as its window.
		const left = { rate_bps: 10_000_000, wanted_bytes: 4_000_000_000, sent_bytes: 1_000_000_000 };
		expect(timeLeft(left)).toBe('About 30 to 45 minutes left');
		expect(timeLeft({ ...left, sent_bytes: 3_990_000_000 })).toBe('Under a minute left');
		expect(timeLeft({ ...left, wanted_bytes: 10_000_000_000, sent_bytes: 0 })).toBe(
			'About 2 to 3 hours left'
		);
		expect(timeLeft({ ...left, wanted_bytes: 400_000_000_000, sent_bytes: 0 })).toBe(
			'About 3 to 4 days left'
		);
		expect(timeLeft({ ...left, wanted_bytes: 90_000_000, sent_bytes: 0 })).toBe(
			'A few minutes left'
		);
	});
});

describe('the words around a swap', () => {
	it('reads a pasted token as one string, whatever wrapped it', () => {
		expect(tokenFrom('  ABCD-EFGH\n-IJKL MNOP\t\n')).toBe('ABCD-EFGH-IJKLMNOP');
	});

	it('says whether a tunnel can host in three ways', () => {
		expect(canHostWords(true)).toBe('Can host');
		expect(canHostWords(false)).toBe(
			"This configuration can't host a swap because it wasn't created properly."
		);
		expect(canHostWords(null)).toBe('Not tried for a swap yet');
	});

	it("says why a session ended in the reason's own words", () => {
		expect(endedWords(session({ state: 'failed', end_reason: 'lost', sent_files: 12 }))).toBe(
			'The connection to them was lost. 12 files received.'
		);
		expect(endedWords(session({ state: 'ended', end_reason: 'refused' }))).toBe(
			"The codes didn't match, so nothing was sent."
		);
		expect(
			endedWords(session({ role: 'host', state: 'done', end_reason: 'done', sent_files: 1 }))
		).toBe('The swap is finished: 1 file sent.');
		expect(endedWords(session({ state: 'ended', end_reason: 'ended by them' }))).toBe(
			'They ended the swap. 0 files received.'
		);
	});

	it('says a run-out token and a used one as their own reasons, never as a lost connection', () => {
		const expired = endedWords(session({ role: 'host', state: 'ended', end_reason: 'expired' }));
		const used = endedWords(session({ state: 'ended', end_reason: 'used' }));
		expect(expired).toBe(
			'The token ran out before anyone joined, so nothing was sent. Start a new swap to make another.'
		);
		expect(used).toBe(
			'That token has been used already, so nothing was sent. Ask them to start a new swap.'
		);
		for (const said of [expired, used]) expect(said).not.toContain('lost');
		expect(endedWords(session({ state: 'ended', end_reason: 'wrong device' }))).toBe(
			'A different device answered the token, so nothing was sent.'
		);
		expect(endedWords(session({ state: 'failed', end_reason: 'disk full' }))).toBe(
			'The swap stopped: this device ran out of disk space. Free some space, then swap again.'
		);
	});

	it('groups a device id in fours', () => {
		expect(inFours('abcdefghij')).toBe('ABCD-EFGH-IJ');
		expect(inFours('ABCD-EFGH')).toBe('ABCD-EFGH');
	});
});

describe('a swap cut off by a tunnel', () => {
	it('is its own step, not an end', () => {
		expect(stageOf(session({ state: 'cut_off' }), true)).toBe('cut-off');
	});

	it('says the connection was lost and how long the same token joins it again', () => {
		const at = () => 'Oct 1, 2026, 3:04 AM';
		expect(cutOffWords({ role: 'host', rejoin_until: 1790000000 }, at)).toBe(
			'The connection to them was lost. They can rejoin with the same token until Oct 1, 2026, 3:04 AM.'
		);
		expect(cutOffWords({ role: 'guest', rejoin_until: 1790000000 }, at)).toBe(
			'The connection to them was lost. Sift keeps trying to reach them until Oct 1, 2026, 3:04 AM. Join again with the same token to try now.'
		);
	});
});

describe('joining a swap', () => {
	it('carries the tunnel chosen beside Join', async () => {
		await joinSwap('ABCD EFGH\n', 'f-recv', 't-harbor');
		expect(posted.at(-1)).toEqual({
			path: '/swap/join',
			body: { token: 'ABCDEFGH', dest_folder_id: 'f-recv', tunnel_id: 't-harbor' }
		});
	});
});

describe('a swap that sends and receives', () => {
	const BOTH = { two_way: true, offer: OFFER };

	it('compares the code first, then answers their offer, then watches both directions', () => {
		const offered = session({ state: 'offered', ...BOTH });
		expect(stageOf(offered, false)).toBe('code');
		expect(stageOf(offered, true)).toBe('offer');
		expect(stageOf({ ...offered, answered: true }, true)).toBe('progress');
		expect(stageOf(offered, true, true)).toBe('progress');
		// The host too: both ways, the host has an offer to answer.
		expect(stageOf(session({ role: 'host', state: 'offered', ...BOTH }), true)).toBe('offer');
		// Files moving the other way while theirs is still unanswered here: the offer comes first.
		expect(stageOf(session({ state: 'transferring', ...BOTH }), false)).toBe('offer');
		expect(stageOf(session({ state: 'transferring', ...BOTH, answered: true }), false)).toBe(
			'progress'
		);
	});

	it('says what went each way when it ends, and an older Sift in words', () => {
		const still = {
			bytes: 1,
			wanted_bytes: null,
			rate_bps: null,
			moving: false,
			received_files: null
		};
		const ended = session({
			state: 'done',
			end_reason: 'done',
			two_way: true,
			sending: { ...still, offered_files: 3, wanted_files: 3, files: 3 },
			receiving: { ...still, offered_files: 1, wanted_files: 1, files: 1 }
		});
		// Both ways is an exchange, and its end says so.
		expect(endedWords(ended)).toBe('The exchange is finished: 3 files sent and 1 received.');
		expect(endedWords({ ...ended, end_reason: 'ended by them' })).toBe(
			'They ended the exchange. 3 files sent and 1 received.'
		);
		expect(endedWords({ ...ended, end_reason: 'refused' })).toBe(
			"The codes didn't match, so nothing was sent."
		);
		expect(endedWords(session({ state: 'ended', end_reason: 'older' }))).toContain(
			"Their Sift can't send files back"
		);
	});

	it('estimates the whole by the slower direction, and only once each moving one has a pace', () => {
		const fast = {
			offered_files: 1,
			wanted_files: 1,
			files: 0,
			moving: true,
			received_files: null
		};
		const both = {
			sending: { ...fast, bytes: 0, wanted_bytes: 600_000_000, rate_bps: 80_000_000 },
			receiving: { ...fast, bytes: 0, wanted_bytes: 60_000_000, rate_bps: 8_000_000 }
		};
		expect(timeLeftBothWays(both)).toBe(
			timeLeftBothWays({ sending: both.sending, receiving: null })
		);
		expect(timeLeftBothWays({ ...both, receiving: { ...both.receiving, rate_bps: null } })).toBe(
			NOT_ENOUGH_TO_SAY
		);
		expect(timeLeftBothWays({ sending: null, receiving: null })).toBe(NOT_ENOUGH_TO_SAY);
	});
});

describe('what the receiver and the picks say', () => {
	it('says received and filed, and only filed where the count is not said', () => {
		expect(receivedWords(4293, 3100, 5000)).toBe('4,293 of 5,000 files received, 3,100 filed');
		expect(receivedWords(null, 12, 38)).toBe('12 of 38 files received');
		expect(receivedWords(undefined, 12, 38)).toBe('12 of 38 files received');
	});

	it('names a pick that one mark explains, and counts the rest', () => {
		const ava = {
			kind: 'person' as const,
			id: 'p',
			name: 'Ava Example',
			mark: 'local' as const,
			files: 1200
		};
		expect(leftOutWords([ava], 0)).toEqual([
			"Ava Example is kept local: 1,200 files aren't offered."
		]);
		const one = { ...ava, kind: 'asset' as const, name: 'holiday.mp4', mark: 'swap' as const };
		expect(leftOutWords([one], 1)).toEqual([
			"holiday.mp4 is kept out of swaps, so it isn't offered.",
			"One more file isn't offered, because it or something it's filed under is kept local or kept out of swaps."
		]);
		expect(leftOutWords([], 3)).toEqual([
			"3 files aren't offered, because they or something they're filed under is kept local or kept out of swaps."
		]);
		expect(leftOutWords([ava], 2)[1]).toBe(
			"Another 2 files aren't offered, because they or something they're filed under is kept local or kept out of swaps."
		);
		expect(leftOutWords([], 0)).toEqual([]);
	});
});

describe('whether Start can go', () => {
	const person = [{ kind: 'person' as const, id: 'p' }];
	const nothing = { files: 0, bytes: 0, left_out: [], left_out_other: 0 };

	it('is off for a swap that only sends and whose picks offer no file', () => {
		expect(startBlocked(nothing, person, false)).toBe(
			"None of what you picked can be sent, so there's nothing to start."
		);
		expect(sendingWords(nothing, person, false)).toBe(startBlocked(nothing, person, false));
	});

	it('is on for an exchange, for facial fingerprints, for a file, and before the weigh', () => {
		expect(startBlocked(nothing, person, true)).toBeNull();
		expect(sendingWords(nothing, person, true)).toBe(
			'You would send no files, and still receive what they send.'
		);
		const faces = [{ kind: 'facial_fingerprints' as const, id: 'p' }];
		expect(startBlocked(nothing, faces, false)).toBeNull();
		expect(sendingWords(nothing, faces, false)).toBe(
			'You would send facial fingerprints and no files.'
		);
		expect(startBlocked({ ...nothing, files: 1 }, person, false)).toBeNull();
		expect(startBlocked(null, person, false)).toBeNull();
	});
});
