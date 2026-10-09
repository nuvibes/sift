/* Locking the vault empties what the client holds: the rows AND the `loaded` flag. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';

import { collections } from '$lib/library/collections.svelte';
import { people, sites } from '$lib/people/people.svelte';
import { tags } from '$lib/entity/tags.svelte';
import { mini } from '$lib/player/mini.svelte';
import { closeWhatTheVaultNowHides, forgetVaultScopedCaches } from '$lib/shell/vault-scoped';
import { showing } from '$lib/theater/wall.svelte';
import { vault } from '$lib/shell/vault.svelte';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

vi.mock('$lib/theater/wall.svelte', () => ({
	showing: { wall: { vaultChanged: vi.fn() } }
}));

const CACHES = [
	{ name: 'people', store: people },
	{ name: 'sites', store: sites },
	{ name: 'collections', store: collections },
	{ name: 'tags', store: tags }
];

beforeEach(() => {
	for (const { store } of CACHES) {
		store.items = [{ id: 'held-from-when-the-vault-was-open' }] as never;
		store.loaded = true;
	}
});

describe('forgetting what the vault scopes', () => {
	it.each(CACHES)('empties $name', ({ store }) => {
		forgetVaultScopedCaches();

		expect(store.items).toEqual([]);
		expect(store.loaded).toBe(false);
	});

	it('clears the loaded flag, which is what makes the screens fetch again', () => {
		// A cache emptied without clearing `loaded` never refills.
		forgetVaultScopedCaches();

		expect(CACHES.every(({ store }) => store.loaded === false)).toBe(true);
	});
});

/*
 * Untested here: every wall must WATCH `loaded` for this to refetch. Only a behavioural test can
 * hold that; none is written rather than one that cannot fail.
 */

/* --- the mini player */

describe('what the vault has just concealed', () => {
	const asset = { id: 'clip-1' };
	const WINDOW = { width: 1400, height: 900 };

	beforeEach(() => {
		mini.open(asset, { width: 1400, height: 900 });
		vi.mocked(api.get).mockReset();
	});

	it('closes the panel when the file it is holding has gone', async () => {
		/* A concealed clip must not go on playing in the corner. */
		vi.mocked(api.get).mockRejectedValue({ status: 404 });

		await closeWhatTheVaultNowHides();

		expect(mini.asset).toBeNull();
	});

	it('leaves a file that was never hidden playing', async () => {
		vi.mocked(api.get).mockResolvedValue({ id: asset.id });

		await closeWhatTheVaultNowHides();

		expect(mini.asset).not.toBeNull();
	});

	it('leaves it playing when the question could not be asked at all', async () => {
		/* No answer about the file: the film is not stopped. */
		vi.mocked(api.get).mockRejectedValue(new Error('the network went away'));

		await closeWhatTheVaultNowHides();

		expect(mini.asset).not.toBeNull();
	});

	it('veils a file that comes back hidden, keeping the Audio player it was in', async () => {
		mini.open({ ...asset, mediaType: 'video', poster: '/thumb', art: 'a1' }, WINDOW, { bar: true });
		vi.mocked(api.get).mockResolvedValue({ id: asset.id, media_type: '', concealed: true });

		await closeWhatTheVaultNowHides();

		expect(mini.asset).toEqual({ id: asset.id, concealed: true, from: undefined });
		expect(mini.bar).toBe(true);
	});

	it('shows it again when Hidden opens', async () => {
		mini.veil(asset.id);
		vi.mocked(api.get).mockResolvedValue({ id: asset.id, media_type: 'video', concealed: false });

		await closeWhatTheVaultNowHides();

		expect(mini.asset?.concealed).toBe(false);
		expect(mini.asset?.mediaType).toBe('video');
	});

	it('leaves a file stepped to while it asked', async () => {
		vi.mocked(api.get).mockImplementation(async () => {
			mini.open({ id: 'clip-2' }, WINDOW);
			return { id: asset.id, concealed: true };
		});

		await closeWhatTheVaultNowHides();

		expect(mini.asset).toEqual({ id: 'clip-2' });
	});

	it('asks nothing when the panel is empty', async () => {
		mini.close();

		await closeWhatTheVaultNowHides();

		expect(api.get).not.toHaveBeenCalled();
	});
});

describe('a Theater wall in the corner', () => {
	beforeEach(() => vi.mocked(showing.wall!.vaultChanged).mockClear());

	it('starts every cell again when Hidden shuts', async () => {
		mini.openWall({ width: 1400, height: 900 });
		vault.unlocked = false;

		await closeWhatTheVaultNowHides();

		expect(showing.wall!.vaultChanged).toHaveBeenCalledWith(true);
	});

	it('leaves a wall on the Theater screen to the screen', async () => {
		mini.close();

		await closeWhatTheVaultNowHides();

		expect(showing.wall!.vaultChanged).not.toHaveBeenCalled();
	});
});
