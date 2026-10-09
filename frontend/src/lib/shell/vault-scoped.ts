/*
 * Every cache whose contents the vault decides, emptied in one place when it opens or shuts.
 * Leaving a store out fails silently and unsafely, so anything caching a scoped read belongs here.
 */

import { api } from '$lib/api/client';
import { collections } from '$lib/library/collections.svelte';
import { people, sites } from '$lib/people/people.svelte';
import { heldOf, mini } from '$lib/player/mini.svelte';
import { showing } from '$lib/theater/wall.svelte';
import { vault } from '$lib/shell/vault.svelte';
import type { components } from '$lib/api/schema';
import { tags } from '$lib/entity/tags.svelte';

export function forgetVaultScopedCaches(): void {
	people.forget();
	sites.forget();
	collections.forget();
	tags.forget();
}

/** A wall there starts again; the panel's file is veiled or shown, closed only when gone. */
export async function closeWhatTheVaultNowHides(): Promise<void> {
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

/** A 404 is what concealment looks like from here. */
function isGone(error: unknown): boolean {
	return (
		typeof error === 'object' &&
		error !== null &&
		'status' in error &&
		(error as { status: unknown }).status === 404
	);
}
