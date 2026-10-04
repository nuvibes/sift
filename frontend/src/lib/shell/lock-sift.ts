import { goto } from '$app/navigation';
import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';
import { session } from '$lib/shell/session.svelte';
import { vault } from '$lib/shell/vault.svelte';

/**
 * Lock Sift itself: end the session and go back to the door.
 *
 * A Privacy setting picks the shape: off (the default), the session ends and the password reopens
 * it; on, the server marks the session locked (every tab, a reload, a replayed credential all
 * refused) and the PIN reopens it. It never fails: this browser lands somewhere shut.
 *
 * The server shuts Hidden with the session, so the vault store is told not to ask for that again:
 * a launch lock or a trigger still due would otherwise be refused by the locked session.
 */
export async function lockSift(): Promise<void> {
	/* Ask, then do as told: the setting and whether a PIN exists are the server's, and reading
	 * them here would race their load. */
	let outcome = 'signed_out';
	try {
		outcome = (await api.post<components['schemas']['LockResponse']>('/auth/lock')).outcome;
	} catch {
		/* A lock the server did not take still clears this browser: the stronger act is the safe one. */
		try {
			await api.post('/auth/logout');
		} catch {
			// Still cleared here. See above.
		}
		outcome = 'signed_out';
	}
	/* Straight to the door, re-reading the session, so screen and server agree from the first frame. */
	if (outcome === 'locked') {
		vault.sessionLocked();
		await session.load();
		await goto('/locked', { replaceState: true });
		return;
	}
	session.forget();
	await goto('/login', { replaceState: true });
}
