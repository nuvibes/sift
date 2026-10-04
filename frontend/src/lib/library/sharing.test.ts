/* The one piece of judgement on the client side of sharing.
 *
 * Everything else in that module is a request shaped and sent. `standingOf` is the exception: it
 * turns a set of rows into one of the three words the interface uses, and it has to reach the same
 * answer the server would. A row reading "Shared" on a screen where the server says no is the only
 * mistake in that file that would matter, so this is where it is pinned.
 */

import { describe, expect, it, vi } from 'vitest';
import * as client from '../api/client';
import { holds, put, readingOf, standingOf, type Grant, type ShareTarget } from './sharing';

function grant(subject: string, effect: 'share' | 'restrict'): Grant {
	return { subject_user_id: subject, username: subject, effect, created_at: 0 };
}

describe('what one account currently holds', () => {
	it('is private when nothing names them', () => {
		// The default for everything in a library, and it is not the same as restricted: it means
		// nobody has said anything, so a share made elsewhere could still reach this.
		expect(standingOf([], 'sam')).toBe('private');
	});

	it('is private when the only rows belong to somebody else', () => {
		expect(standingOf([grant('alex', 'share')], 'sam')).toBe('private');
	});

	it('is shared when a share names them', () => {
		expect(standingOf([grant('sam', 'share')], 'sam')).toBe('shared');
	});

	it('is restricted when a restrict names them', () => {
		expect(standingOf([grant('sam', 'restrict')], 'sam')).toBe('restricted');
	});

	it('is restricted when they hold BOTH, whichever order the rows arrive in', () => {
		/* The whole model in one line. A share and a restrict on the same thing are separate rows
		 * and both can exist; the server resolves them to restrict, every time, and this mirrors that
		 * rather than holding an opinion. Getting it backwards would draw "Shared" beside an account
		 * the server refuses, which is the one wrong answer here that would be believed.
		 */
		expect(standingOf([grant('sam', 'share'), grant('sam', 'restrict')], 'sam')).toBe('restricted');
		expect(standingOf([grant('sam', 'restrict'), grant('sam', 'share')], 'sam')).toBe('restricted');
	});
});

describe('whether one specific row exists', () => {
	it('is not the same question as the resolved standing', () => {
		/* The distinction the controls are lit from. Both rows exist, the resolved answer is
		 * Restricted, and the share is still really stored. So a Share button reading the
		 * resolution would sit dark over a row that is there, and pressing it would write what was
		 * already written and appear to do nothing.
		 */
		const both = [grant('sam', 'share'), grant('sam', 'restrict')];

		expect(standingOf(both, 'sam')).toBe('restricted');
		expect(holds(both, 'sam', 'share')).toBe(true);
		expect(holds(both, 'sam', 'restrict')).toBe(true);
	});

	it('does not answer for somebody else', () => {
		expect(holds([grant('alex', 'share')], 'sam', 'share')).toBe(false);
	});

	it('is false for a row that was never made', () => {
		expect(holds([grant('sam', 'restrict')], 'sam', 'share')).toBe(false);
	});
});

describe('one answer across several things at once', () => {
	it('is the word they all agree on', () => {
		expect(readingOf([[grant('sam', 'share')], [grant('sam', 'share')]], 'sam')).toBe('shared');
	});

	it('is mixed when they do not', () => {
		// A real answer rather than a failure to compute one. Forty files do not have one standing,
		// and picking the first would tell somebody the other thirty-nine say something they do not.
		expect(readingOf([[grant('sam', 'share')], []], 'sam')).toBe('mixed');
	});

	it('is private when the panel is open on nothing', () => {
		expect(readingOf([], 'sam')).toBe('private');
	});
});

describe('putting one account on one thing into a chosen state', () => {
	/* `put` is "get to this state", not "toggle". The panel stages a decision and applies it later,
	 * so what it holds at that point is the word somebody chose rather than the presses that got
	 * them there. */
	const target: ShareTarget = { type: 'item', id: 'a1', label: 'clip.mp4' };

	it('writes the share when the answer is shared', async () => {
		const seen: unknown[] = [];
		vi.spyOn(client.api, 'put').mockImplementation(async (path, options) => {
			seen.push([path, options]);
			return [];
		});
		await put(target, 'sam', 'shared', []);
		expect(seen).toHaveLength(1);
		vi.restoreAllMocks();
	});

	it('takes BOTH rows off when the answer is private, and only the ones that exist', async () => {
		/* The one case that needs two calls. A share and a restrict can both be recorded on one
		 * thing, and leaving either behind would make the row read Private over a grant that is
		 * still there. Only the rows that exist, so Private on an untouched thing writes nothing. */
		const posted: string[] = [];
		vi.spyOn(client.api, 'post').mockImplementation(async (path, options) => {
			posted.push(String((options?.body as { effect?: string })?.effect));
			return [];
		});

		await put(target, 'sam', 'private', [grant('sam', 'share'), grant('sam', 'restrict')]);
		expect(posted).toEqual(['share', 'restrict']);

		posted.length = 0;
		await put(target, 'sam', 'private', [grant('sam', 'restrict')]);
		expect(posted).toEqual(['restrict']);

		posted.length = 0;
		await put(target, 'sam', 'private', []);
		expect(posted).toEqual([]);
		vi.restoreAllMocks();
	});
});
