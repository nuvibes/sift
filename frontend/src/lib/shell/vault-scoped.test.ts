/* Locking the vault has to empty what the client is holding.
 *
 * The failure this guards is quiet: the lock reaches the server, the server stops serving
 * concealed rows, and the screens go on drawing the ones they were already given. Nothing errors.
 * The vault reports itself locked, the button says locked, and a concealed person is still on the
 * page.
 *
 * A store keeps a `loaded` flag so it does not re-fetch, and that flag is exactly the thing that
 * has to stop being true. So this asserts the flag and the rows are gone: both, because a
 * screen reading `items` directly would keep drawing the old ones while a re-fetch was in flight.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';

import { collections } from '$lib/library/collections.svelte';
import { people, sites } from '$lib/people/people.svelte';
import { tags } from '$lib/entity/tags.svelte';
import { mini } from '$lib/player/mini.svelte';
import { closeWhatTheVaultNowHides, forgetVaultScopedCaches } from '$lib/shell/vault-scoped';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() },
	ApiError: class extends Error {}
}));

/** Every cache whose contents the vault decides. Named here so a new one has to be added twice. */
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
		// The rows going is not enough on its own. Every screen guards its fetch with "if it is not
		// loaded", so a cache emptied without clearing that flag stays empty forever instead of
		// refilling: concealed rows gone, but everything else gone with them.
		forgetVaultScopedCaches();

		expect(CACHES.every(({ store }) => store.loaded === false)).toBe(true);
	});
});

/*
 * THE OTHER HALF OF THIS CONTRACT HAS NO TEST HERE, AND A SOURCE-TEXT CHECK CANNOT BE ONE.
 *
 * Clearing `loaded` only works because every wall WATCHES it: the screens' own
 * load-it-if-it-is-not-loaded effects do the fetching. That is an assumption about four other
 * files: a guard wrapped in `untrack` (to stop a request storm) makes a screen deaf to the cache
 * being emptied, and hiding a tag or moving the vault then leaves the wall blank until it is
 * navigated away from and back. Every test in this file would pass, because they all test the
 * store.
 *
 * A static gate cannot catch it: these screens also read `loaded` in their markup, which is a real
 * reactive read in a different context, and source text cannot tell "read inside the loader" from
 * "read in the template".
 *
 * What is actually needed is behavioural: mount the wall, call `forgetVaultScopedCaches()`, and
 * assert a fetch follows. Left undone rather than left as a green test that cannot fail, which is
 * the more dangerous of the two.
 */

/* --- the mini player ------------------------------------------------------------------------- */

describe('what the vault has just concealed', () => {
	const asset = { id: 'clip-1' };

	beforeEach(() => {
		mini.open(asset, { width: 1400, height: 900 });
		vi.mocked(api.get).mockReset();
	});

	it('closes the panel when the file it is holding has gone', async () => {
		/* The unsafe direction, and the one this file exists for: a concealed clip going on playing
		   in the corner after everything else about it has been taken off the screen. */
		vi.mocked(api.get).mockRejectedValue({ status: 404 });

		await closeWhatTheVaultNowHides();

		expect(mini.asset).toBeNull();
	});

	it('leaves a file that was never hidden playing', async () => {
		/* Somebody watching a clip that is not hidden must keep it when Hidden shuts on its
		   timer: a lock doing that would be doing something that is none of its business. */
		vi.mocked(api.get).mockResolvedValue({ id: asset.id });

		await closeWhatTheVaultNowHides();

		expect(mini.asset).not.toBeNull();
	});

	it('leaves it playing when the question could not be asked at all', async () => {
		/* A dropped network, a restarted server, a reply that is not JSON. None of those is an
		   answer about whether the file is hidden, and stopping somebody's film on one is the wrong
		   answer to every one of them. */
		vi.mocked(api.get).mockRejectedValue(new Error('the network went away'));

		await closeWhatTheVaultNowHides();

		expect(mini.asset).not.toBeNull();
	});

	it('asks nothing when the panel is empty', async () => {
		mini.close();

		await closeWhatTheVaultNowHides();

		expect(api.get).not.toHaveBeenCalled();
	});
});
