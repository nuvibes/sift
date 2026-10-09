import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/client';
import { setHidden } from '$lib/library/hiding';

/* Putting things in the vault, from wherever the menu is. */

const shown = vi.hoisted(() => vi.fn());
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: shown } }));
vi.mock('$lib/library/changes.svelte', () => ({ libraryChanges: { changed: vi.fn() } }));
/* The shared PIN prompt, answered by the test: true is a PIN that opened Hidden, false a Cancel. */
const opened = vi.hoisted(() => vi.fn());
vi.mock('$lib/shell/vault.svelte', () => ({ vaultPrompt: { ask: vi.fn(), opened } }));

function lastToast(): { message: string; tone?: string; action?: { run: () => void } } {
	const call = shown.mock.calls.at(-1) ?? [];
	return { message: String(call[0] ?? ''), ...(call[1] ?? {}) };
}

beforeEach(() => shown.mockReset());

describe('hiding something', () => {
	it('writes it, says so, and offers the way back', async () => {
		const set = vi.fn().mockResolvedValue(undefined);

		const moved = await setHidden(['c1'], true, { noun: 'collection', set });

		expect(moved).toEqual(['c1']);
		expect(set).toHaveBeenCalledWith('c1', true);
		expect(lastToast().message).toMatch(/^Hid it\. /);
		expect(
			lastToast().action,
			'nothing to undo with, and the row has left the screen'
		).toBeTruthy();
	});

	it('says what it did in the past tense, both ways', async () => {
		const set = vi.fn().mockResolvedValue(undefined);

		await setHidden(['a', 'b'], false, { noun: 'file', set });
		expect(lastToast().message).toBe('Unhid 2 files');

		await setHidden(['a'], false, { noun: 'file', set });
		expect(lastToast().message).toBe('Unhid it');

		await setHidden(['a', 'b'], true, { noun: 'file', set, stays: true });
		expect(lastToast().message).toBe('Hid 2 files. Still here because the vault is open.');
	});

	it('undoes exactly what it moved, pointed the other way', async () => {
		const set = vi.fn().mockResolvedValue(undefined);
		await setHidden(['a', 'b'], true, { noun: 'file', set });
		set.mockClear();

		lastToast().action?.run();
		await vi.waitFor(() => expect(set).toHaveBeenCalledTimes(2));

		expect(set.mock.calls).toEqual([
			['a', false],
			['b', false]
		]);
	});

	it('takes the row off the screen before the list catches up', async () => {
		const forget = vi.fn();

		await setHidden(['p1'], true, {
			noun: 'person',
			plural: 'people',
			set: async () => {},
			forget
		});

		expect(forget).toHaveBeenCalledWith('p1');
	});
});

describe('when it is refused', () => {
	/* The PIN is the only thing that opens the vault again, so hiding something without one is
	 * not hiding it. */
	it.each([401, 409])('says to set a PIN rather than that it broke (%i)', async (status) => {
		const set = vi.fn().mockRejectedValue(new ApiError(status, 'nope'));

		const moved = await setHidden(['a1'], true, { noun: 'file', set });

		expect(moved).toEqual([]);
		expect(lastToast().message).toContain('PIN');
		expect(lastToast().tone).toBe('error');
	});

	it('sends the refused straight to the PIN form', async () => {
		const set = vi.fn().mockRejectedValue(new ApiError(409, 'nope'));

		await setHidden(['a1'], true, { noun: 'file', set });

		expect(shown.mock.calls.at(-1)?.[0]).toContainEqual(
			expect.objectContaining({ text: 'Profile', href: '/settings/profile#profile.pin' })
		);
	});

	it('stops at the first failure instead of saying so six times', async () => {
		const set = vi.fn().mockRejectedValue(new Error('down'));

		await setHidden(['a', 'b', 'c'], true, { noun: 'file', set });

		expect(set).toHaveBeenCalledTimes(1);
		expect(shown).toHaveBeenCalledTimes(1);
	});

	it('still reports what got through before it stopped', async () => {
		// Half a hide is a real state on the server, and pretending otherwise leaves somebody with
		// no way back to the rows that did move.
		const set = vi.fn().mockResolvedValueOnce(undefined).mockRejectedValueOnce(new Error('down'));

		const moved = await setHidden(['a', 'b'], true, { noun: 'file', set });

		expect(moved).toEqual(['a']);
	});
});

/* A KIND WHOSE ENDPOINT TAKES A LIST. Files have one; the five named kinds do not, so the loop
 * above stays for them. */
describe('a kind that can be hidden a selection at a time', () => {
	const answered = {
		changed: 2,
		skipped: 0,
		reason: null,
		reason_many: null,
		vault_locked: false
	};

	it('sends the whole selection once instead of once per file', async () => {
		const set = vi.fn();
		const setMany = vi.fn().mockResolvedValue(answered);
		const forget = vi.fn();

		const moved = await setHidden(['a', 'b'], true, { noun: 'file', set, setMany, forget });

		expect(setMany.mock.calls).toEqual([[['a', 'b'], true]]);
		expect(set).not.toHaveBeenCalled();
		expect(moved).toEqual(['a', 'b']);
		expect(forget.mock.calls).toEqual([['a'], ['b']]);
		expect(lastToast().message).toContain('2 files');
	});

	it('undoes the selection the same way it was sent', async () => {
		const setMany = vi.fn().mockResolvedValue(answered);
		await setHidden(['a', 'b'], true, { noun: 'file', set: vi.fn(), setMany });
		setMany.mockClear();

		lastToast().action?.run();
		await vi.waitFor(() => expect(setMany).toHaveBeenCalledTimes(1));

		expect(setMany.mock.calls).toEqual([[['a', 'b'], false]]);
	});

	/* The hide was made with Hidden locked, so the Undo is refused: the server resolves a hidden
	 * thing only for a session that has proved the PIN. */
	it('asks for the PIN when Undo meets a locked Hidden, then puts it back', async () => {
		const setMany = vi.fn().mockResolvedValue(answered);
		await setHidden(['a'], true, { noun: 'file', set: vi.fn(), setMany });
		setMany.mockReset();
		setMany
			.mockResolvedValueOnce({ ...answered, changed: 0, skipped: 1, vault_locked: true })
			.mockResolvedValueOnce(answered);
		opened.mockReset().mockResolvedValue(true);
		const undo = lastToast().action;
		shown.mockClear();

		undo?.run();
		await vi.waitFor(() => expect(setMany).toHaveBeenCalledTimes(2));

		expect(opened).toHaveBeenCalledTimes(1);
		expect(opened).toHaveBeenCalledWith('Put it back');
		expect(setMany.mock.calls).toEqual([
			[['a'], false],
			[['a'], false]
		]);
		expect(shown, 'the way back worked, so there is nothing to say').not.toHaveBeenCalled();
	});

	it('leaves it hidden and says so when the PIN prompt is cancelled', async () => {
		const setMany = vi.fn().mockResolvedValue(answered);
		await setHidden(['a'], true, { noun: 'file', set: vi.fn(), setMany });
		setMany
			.mockReset()
			.mockResolvedValue({ ...answered, changed: 0, skipped: 1, vault_locked: true });
		opened.mockReset().mockResolvedValue(false);
		const undo = lastToast().action;
		shown.mockClear();

		undo?.run();
		await vi.waitFor(() => expect(shown).toHaveBeenCalled());

		expect(setMany, 'no second try without the PIN').toHaveBeenCalledTimes(1);
		expect(lastToast().message).toBe('It stayed hidden');
		expect(lastToast().action, 'they just said no to the prompt').toBeUndefined();
	});

	/* A kind with no list endpoint writes one id at a time, and a hidden id answers the 404 an
	 * unknown one gets while Hidden is locked. That is the same refusal and gets the same prompt. */
	it('reads a 404 on a single undo as the same refusal, and tries once more after the PIN', async () => {
		const set = vi.fn().mockResolvedValue(undefined);
		await setHidden(['c1'], true, { noun: 'collection', set });
		set
			.mockReset()
			.mockRejectedValueOnce(new ApiError(404, 'no such'))
			.mockResolvedValue(undefined);
		opened.mockReset().mockResolvedValue(true);
		const undo = lastToast().action;
		shown.mockClear();

		undo?.run();
		await vi.waitFor(() => expect(set).toHaveBeenCalledTimes(2));

		expect(opened).toHaveBeenCalledTimes(1);
		expect(set.mock.calls).toEqual([
			['c1', false],
			['c1', false]
		]);
	});

	it('asks only once: a second refusal after the PIN is a failure, not another prompt', async () => {
		const set = vi.fn().mockResolvedValue(undefined);
		await setHidden(['c1'], true, { noun: 'collection', set });
		set.mockReset().mockRejectedValue(new ApiError(404, 'no such'));
		opened.mockReset().mockResolvedValue(true);
		const undo = lastToast().action;
		shown.mockClear();

		undo?.run();
		await vi.waitFor(() => expect(shown).toHaveBeenCalled());

		expect(opened).toHaveBeenCalledTimes(1);
		expect(lastToast().message).toBe("That couldn't be undone");
	});

	it('takes nothing off the screen when only some of it went', async () => {
		/* The reply says how many were left out and never which (naming the rows a vault is
		 * concealing would undo the request in the act of confirming it), so a row dropped by id
		 * here would be a guess. One re-read is the only thing that can be right. */
		const forget = vi.fn();
		const setMany = vi
			.fn()
			.mockResolvedValue({ ...answered, changed: 1, skipped: 1, reason: 'It is in your vault.' });

		const moved = await setHidden(['a', 'b'], true, {
			noun: 'file',
			set: vi.fn(),
			setMany,
			forget
		});

		expect(moved).toEqual([]);
		expect(forget).not.toHaveBeenCalled();
		expect(lastToast().message).toContain("couldn't be included");
	});

	it('still says to set a PIN when the whole request is refused', async () => {
		const setMany = vi.fn().mockRejectedValue(new ApiError(409, 'nope'));

		const moved = await setHidden(['a', 'b'], true, { noun: 'file', set: vi.fn(), setMany });

		expect(moved).toEqual([]);
		expect(lastToast().message).toContain('PIN');
	});
});

/* ONE COPY OF THE SENTENCE, and a ratchet at zero other copies. */
describe('the vault refusal', () => {
	const A_SECOND_COPY = /Set a PIN in /;
	const THE_ONE_PLACE = 'lib/library/hiding.ts';

	it('is written in one place and nowhere else in the client', async () => {
		const { readFileSync, readdirSync } = await import('node:fs');
		const { join, relative, resolve } = await import('node:path');
		const { fileURLToPath } = await import('node:url');
		const root = resolve(fileURLToPath(import.meta.url), '..', '..', '..');

		const found: string[] = [];
		const walk = (at: string): void => {
			for (const entry of readdirSync(at, { withFileTypes: true })) {
				const here = join(at, entry.name);
				if (entry.isDirectory()) {
					walk(here);
				} else if (/\.(svelte|ts)$/.test(entry.name) && !entry.name.includes('.test.')) {
					if (A_SECOND_COPY.test(readFileSync(here, 'utf8'))) {
						found.push(relative(root, here).split('\\').join('/'));
					}
				}
			}
		};
		walk(root);

		expect(found, 'nothing says it at all: the rule has stopped being written').toContain(
			THE_ONE_PLACE
		);
		for (const where of found) {
			expect(where, `${where} writes the vault refusal again; call setHidden instead`).toBe(
				THE_ONE_PLACE
			);
		}
	});
});
