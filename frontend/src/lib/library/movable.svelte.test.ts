/* Where a file can be moved to: only folders the disk lets Sift write in. */
import { expect, it, vi } from 'vitest';
import { ApiError } from '$lib/api/client';

const get = vi.hoisted(() => vi.fn());

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get }
}));

import { movable } from './movable.svelte';

it('leaves out a folder the server says Sift may not write in', async () => {
	get.mockImplementation(async (path: string) =>
		path === '/library/roots'
			? { roots: [{ id: 'r1', name: 'Media' }] }
			: {
					folders: [
						{
							id: 'a',
							root_id: 'r1',
							parent_id: null,
							name: 'Clips',
							rel_path: 'Clips',
							writable: true
						},
						{
							id: 'b',
							root_id: 'r1',
							parent_id: null,
							name: 'Kept',
							rel_path: 'Kept',
							writable: false
						}
					]
				}
	);
	movable.forget();
	await movable.ensure();

	expect(get).toHaveBeenCalledWith('/library/folders', { query: { writable: true } });
	expect(movable.folders).toEqual([{ id: 'a', name: 'Clips', path: 'Media/Clips' }]);
});

it('says a failed read, and takes a refusal as a list read with nothing in it', async () => {
	get.mockRejectedValue(new Error('500'));
	movable.forget();
	await movable.ensure();
	expect(movable.foldersRead).toBe('failed');

	get.mockRejectedValue(new ApiError(403, 'Forbidden'));
	movable.forget();
	await movable.ensure();
	expect(movable.foldersRead).toBe('read');
	expect(movable.folders).toEqual([]);
});

it('hands a second ask the read already under way', async () => {
	let answer: (value: unknown) => void = () => {};
	get.mockImplementation((path: string) =>
		path === '/library/roots'
			? Promise.resolve({ roots: [] })
			: new Promise((resolve) => (answer = resolve))
	);
	movable.forget();
	const first = movable.ensure();
	let second = false;
	void movable.ensure().then(() => (second = true));
	await Promise.resolve();
	expect(second).toBe(false);
	answer({ folders: [] });
	await first;
	await Promise.resolve();
	expect(second).toBe(true);
});
