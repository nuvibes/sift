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
 * ## The mini player, and why it asks
 *
 * The panel in the corner holds one asset (a scoped read by any reading of the paragraph above),
 * and it can fail in BOTH directions:
 *
 *   - **A hidden file keeps playing.** Lock the vault and a concealed clip goes on playing in the
 *     corner, with its picture on screen, after everything else about it has gone. That is the
 *     unsafe direction and it is the one this file exists to prevent.
 *   - **A file that is NOT hidden is closed** when the vault locks. The rule below never closes the
 *     panel on a file the server is still willing to send.
 *
 * The rule is the doctrine at the top of this file applied literally, and it needs no new idea:
 * ASK THE SERVER. A concealed thing is absent from what the server sends, so the panel closes
 * when, and only when, the asset it is holding has gone. There is no list of what is hidden in
 * the browser, and there must not be one.
 */

import { api } from '$lib/api/client';
import { collections } from '$lib/library/collections.svelte';
import { people, sites } from '$lib/people/people.svelte';
import { mini } from '$lib/player/mini.svelte';
import { tags } from '$lib/entity/tags.svelte';

/** Empty every vault-scoped cache. Called when the vault opens or shuts, and at no other time. */
export function forgetVaultScopedCaches(): void {
	people.forget();
	sites.forget();
	collections.forget();
	tags.forget();
}

/**
 * Close the mini player if what it is holding has just been concealed. Otherwise leave it alone.
 *
 * Asynchronous, unlike everything above, because it is a question rather than a sweep, and it has
 * to be a question. The browser does not know which files are hidden; that is the point of hiding
 * being real. What it can do is ask for the one file in the panel and see whether the server still
 * admits it exists.
 *
 * **Anything other than a plain "it is gone" leaves the panel alone.** A request that fails because
 * the network dropped, or the server restarted, or the reply was not JSON, says nothing about
 * whether the file is hidden, and stopping somebody's film on a failed request is the wrong
 * answer to every one of those.
 */
export async function closeWhatTheVaultNowHides(): Promise<void> {
	const showing = mini.asset?.id;
	if (showing === undefined) return;
	try {
		await api.get(`/assets/${encodeURIComponent(showing)}`);
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
