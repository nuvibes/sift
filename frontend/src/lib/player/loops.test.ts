// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Forgetting marks over a selection, from the Loops wall.
 *
 * The behaviour worth pinning is not the wording on its own: it is that the rows come off the
 * screen on the server's word, that the exception goes through the one place that knows how to say
 * it, and that ONE request covers the whole selection rather than one per mark.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import { announceSkipped } from '$lib/library/bulk';
import { toasts } from '$lib/shell/toasts.svelte';
import { forgetLabel, forgetLoops, forgottenWords } from '$lib/player/loops';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));
vi.mock('$lib/library/bulk', () => ({ announceSkipped: vi.fn() }));

const sent = vi.mocked(api);
const shown = vi.mocked(toasts.show);
const skipped = vi.mocked(announceSkipped);

const answer = (over: Record<string, unknown> = {}) => ({
	changed: 0,
	skipped: 0,
	reason: null,
	reason_many: null,
	vault_locked: false,
	...over
});

beforeEach(() => {
	vi.clearAllMocks();
});

describe('what the verb says', () => {
	it('says Delete and names the LOOP, never the file, however many there are', () => {
		// The whole wording problem on this wall: the shared file verb is hidden here because it
		// removes the video, so this one names what it deletes. Never "Forget".
		expect(forgetLabel(1)).toBe('Delete this loop');
		expect(forgetLabel(12)).toBe('Delete 12 loops');
		expect(forgetLabel(1)).not.toMatch(/forget|file|video/i);
		expect(forgetLabel(12)).not.toMatch(/forget|file|video/i);
	});

	it('says afterwards that no file went, because a loop IS a file', () => {
		expect(forgottenWords(1)).toBe('Loop deleted. No file was touched.');
		expect(forgottenWords(3)).toBe('3 loops deleted. No file was touched.');
	});
});

describe('forgetting a selection', () => {
	it('is ONE request for the whole selection, not one per mark', async () => {
		sent.post.mockResolvedValue(answer({ changed: 3 }));

		await forgetLoops(['a', 'b', 'c'], vi.fn());

		expect(sent.post).toHaveBeenCalledTimes(1);
		expect(sent.post).toHaveBeenCalledWith('/loops/forget', {
			body: { loop_ids: ['a', 'b', 'c'] }
		});
		expect(sent.del).not.toHaveBeenCalled();
	});

	it('takes the rows off and says how many went', async () => {
		const gone = vi.fn();
		sent.post.mockResolvedValue(answer({ changed: 2 }));

		await forgetLoops(['a', 'b'], gone);

		expect(gone.mock.calls.map((call) => call[0])).toEqual(['a', 'b']);
		expect(shown).toHaveBeenCalledWith('2 loops deleted. No file was touched.', {
			tone: 'success'
		});
	});

	it('hands the exception to the ONE place that knows how to say it', async () => {
		// Not a sentence written here. A dozen screens answer this shape and only one of them would
		// remember the Unlock button; `announceSkipped` is where that is settled.
		sent.post.mockResolvedValue(answer({ changed: 1, skipped: 1, reason: 'not yours' }));

		await forgetLoops(['a', 'b'], vi.fn());

		expect(skipped).toHaveBeenCalledWith(expect.objectContaining({ skipped: 1 }), 'loop');
	});

	it('says nothing cheerful when nothing went', async () => {
		sent.post.mockResolvedValue(answer({ changed: 0, skipped: 2, reason: 'not yours' }));

		await forgetLoops(['a', 'b'], vi.fn());

		expect(shown).not.toHaveBeenCalled();
		expect(skipped).toHaveBeenCalled();
	});

	it('leaves the wall alone when the request fails outright', async () => {
		const gone = vi.fn();
		sent.post.mockRejectedValue(new Error('offline'));

		await forgetLoops(['a'], gone);

		expect(gone).not.toHaveBeenCalled();
		expect(shown).toHaveBeenCalledWith("Those loops couldn't be deleted", { tone: 'error' });
	});
});
