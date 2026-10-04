/* The walls somebody kept, and the one place this deliberately differs from a saved search.
 *
 * Saving under a name already used is REFUSED here rather than replacing. A search is one line of
 * text and overwriting it costs a retype; a wall is four sources, four sets of behaviour and a
 * shape, and overwriting one because the name happened to match is a loss somebody only notices
 * later. So a 409 becomes its own error carrying the server's own sentence, which names the
 * wall, where the flat one-liner every other screen gets does not.
 */

import { beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), patch: vi.fn(), del: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: mocks.get, post: mocks.post, patch: mocks.patch, del: mocks.del }
}));

import { ApiError } from '$lib/api/client';
import { presets, NameTaken } from './presets.svelte';

const WALL = { layout: 'four', shape: { rows: 2, columns: 2 } as never, cells: [] };
const KEPT = { id: 'w-1', name: 'Evening', ...WALL };
const OTHER = { id: 'w-2', name: 'Morning', ...WALL };

beforeEach(() => {
	vi.clearAllMocks();
	mocks.get.mockResolvedValue({ items: [] });
	mocks.post.mockResolvedValue(KEPT);
	mocks.patch.mockResolvedValue(KEPT);
	mocks.del.mockResolvedValue({});
	presets.items = [];
	presets.loaded = false;
	presets.busy = false;
});

it('asks the server once however many times the menu is opened', async () => {
	mocks.get.mockResolvedValue({ items: [KEPT] });

	await presets.ensure();
	await presets.ensure();

	expect(mocks.get).toHaveBeenCalledTimes(1);
	expect(presets.items).toEqual([KEPT]);
});

it('does not ask a second time while the first answer is still coming', async () => {
	let settle!: (value: { items: (typeof KEPT)[] }) => void;
	mocks.get.mockReturnValueOnce(new Promise((resolve) => (settle = resolve)));

	const first = presets.ensure();
	const second = presets.ensure();
	settle({ items: [KEPT] });
	await Promise.all([first, second]);

	expect(mocks.get).toHaveBeenCalledTimes(1);
});

it('stops saying it is busy even when the read fails', async () => {
	mocks.get.mockRejectedValue(new Error('no'));

	await expect(presets.reload()).rejects.toThrow();

	expect(presets.busy).toBe(false);
	expect(presets.loaded).toBe(false);
});

it('saves the wall whole and reads the list back', async () => {
	mocks.get.mockResolvedValue({ items: [KEPT] });

	const made = await presets.save('Evening', WALL);

	expect(mocks.post).toHaveBeenCalledWith('/theater/arrangements', {
		body: { name: 'Evening', ...WALL }
	});
	expect(made).toEqual(KEPT);
	expect(presets.items).toEqual([KEPT]);
});

it('refuses a name already used rather than overwriting the wall under it', async () => {
	// The whole difference from a saved search. A wall is four sources, four sets of behaviour and
	// a shape, and losing one because the name matched is noticed much later than it happened.
	mocks.post.mockRejectedValue(
		new ApiError(409, 'That request was not valid.', 'You already have a preset called Evening.')
	);

	await expect(presets.save('Evening', WALL)).rejects.toBeInstanceOf(NameTaken);
});

it('carries the sentence the server wrote, which names the preset', async () => {
	mocks.post.mockRejectedValue(
		new ApiError(409, 'That request was not valid.', 'You already have a preset called Evening.')
	);

	await expect(presets.save('Evening', WALL)).rejects.toThrow(
		'You already have a preset called Evening.'
	);
});

it('falls back to the flat one-liner when the refusal carries no sentence', async () => {
	mocks.post.mockRejectedValue(new ApiError(409, 'That request was not valid.'));

	await expect(presets.save('Evening', WALL)).rejects.toThrow('That request was not valid.');
});

it('leaves any other failure as it is, because only a conflict is an ordinary answer', async () => {
	mocks.post.mockRejectedValue(new ApiError(500, 'Something went wrong.'));

	await expect(presets.save('Evening', WALL)).rejects.not.toBeInstanceOf(NameTaken);
});

it('updates a wall that is already saved, and reads the list back', async () => {
	mocks.get.mockResolvedValue({ items: [KEPT] });

	const changed = await presets.update('w-1', 'Evening', WALL);

	expect(mocks.patch).toHaveBeenCalledWith('/theater/arrangements/w-1', {
		body: { name: 'Evening', ...WALL }
	});
	expect(changed).toEqual(KEPT);
});

it('refuses a rename onto a name already used, through the same door', async () => {
	mocks.patch.mockRejectedValue(
		new ApiError(409, 'That request was not valid.', 'You already have a wall called Morning.')
	);

	await expect(presets.update('w-1', 'Morning', WALL)).rejects.toBeInstanceOf(NameTaken);
});

it('takes a wall out at once and tells the server after', async () => {
	mocks.get.mockResolvedValue({ items: [KEPT, OTHER] });
	await presets.ensure();

	await presets.remove('w-1');

	expect(presets.items).toEqual([OTHER]);
	expect(mocks.del).toHaveBeenCalledWith('/theater/arrangements/w-1');
});

it('finds one saved wall by the id an address names, and none for an id it does not hold', async () => {
	mocks.get.mockResolvedValue({ items: [KEPT, OTHER] });

	expect(await presets.byId('w-2')).toEqual(OTHER);
	expect(await presets.byId('w-gone')).toBeNull();
	expect(mocks.get).toHaveBeenCalledTimes(1);
});
