/* Everything the client is holding that the vault decides the contents of.
 *
 * A concealed thing is absent from what the server sends. That is what makes hiding it real, and
 * it is also why opening or shutting the vault cannot be drawn from what the browser already has:
 * there is nothing in the page to reveal, and (the direction that actually matters) what was
 * revealed while the vault was open is still sitting in these caches after it shuts.
 *
 * A store that keeps a `loaded` flag will not fetch again on its own; that flag is exactly the
 * thing that has to stop being true. So every cache whose contents the vault scopes is emptied
 * here, in one place, and the screens' existing "load it if it is not loaded" effects do the
 * rest.
 *
 * Getting this list wrong is silent and it fails in the unsafe direction: a store left out keeps
 * drawing concealed rows after the vault is locked, and nothing errors. Anything that caches a
 * scoped read belongs here on the same day it is written.
 *
 * The corner panel holds a scoped read too, and the browser has no list of what is hidden, so it
 * asks the server about the one file it holds.
 */

import { api } from '$lib/api/client';
import { collections } from '$lib/library/collections.svelte';
import { people, sites } from '$lib/people/people.svelte';
import { heldOf, mini } from '$lib/player/mini.svelte';
import { showing } from '$lib/theater/wall.svelte';
import { vault } from '$lib/shell/vault.svelte';
import type { components } from '$lib/api/schema';
import { tags } from '$lib/entity/tags.svelte';

/** Empty every vault-scoped cache. Called when the vault opens or shuts, and at no other time. */
export function forgetVaultScopedCaches(): void {
	people.forget();
	sites.forget();
	collections.forget();
	tags.forget();
}

/**
 * Bring the corner up to date with Hidden: a wall there starts again, and the panel's file is
 * veiled if it comes back hidden, shown again if not, and closed only on a plain "it is gone".
 */
export async function closeWhatTheVaultNowHides(): Promise<void> {
	// The Theater screen answers for its own wall; one in the corner has nobody else to.
	if (mini.wall) showing.wall?.vaultChanged(!vault.unlocked);
	const held = mini.asset?.id;
	if (held === undefined) return;
	try {
		const file = await api.get<components['schemas']['AssetDetail']>(
			`/assets/${encodeURIComponent(held)}`
		);
		if (file.concealed) mini.veil(held);
		else mini.unveil(heldOf(file));
	} catch (error) {
		if (isGone(error)) mini.close();
	}
}

/** Whether the server answered "there is no such thing", which is what concealment looks like from
 *  out here, rather than "something went wrong", which is not an answer about the file at all. */
function isGone(error: unknown): boolean {
	return (
		typeof error === 'object' &&
		error !== null &&
		'status' in error &&
		(error as { status: unknown }).status === 404
	);
}
