import { goto } from '$app/navigation';
import { stopAccountScopedReaders } from '$lib/shell/account-scoped';
import { api, ApiError, requestsInFlight } from '$lib/api/client';
import { mini } from '$lib/player/mini.svelte';
import { session } from '$lib/shell/session.svelte';
import { theme } from '$lib/theme/theme.svelte';
import { toasts } from '$lib/shell/toasts.svelte';

/** The longest sign-out waits for the requests already on their way before it ends the session. */
export const LANDING_WAIT_MS = 2000;
const LANDING_STEP_MS = 50;

/* A request sent while the session is open and answered after it ends is refused, and the browser
   writes every refusal into its console as an error. The readers are stopped first, so nothing new
   is sent, and what is already on its way is let land, for a moment at most. */
async function letRequestsLand(): Promise<void> {
	for (let waited = 0; requestsInFlight() && waited < LANDING_WAIT_MS; waited += LANDING_STEP_MS) {
		await new Promise((done) => setTimeout(done, LANDING_STEP_MS));
	}
}

/**
 * Sign this browser out, from wherever the press is: Settings > Profile, or More on a phone.
 *
 * One function for every door, so what signing out clears (the session, the theme this account
 * chose) cannot differ by which door was used.
 */
export async function signOut(): Promise<void> {
	stopAccountScopedReaders();
	await letRequestsLand();
	try {
		await api.post('/auth/logout');
	} catch (error) {
		// A sign-out the server never heard still clears this browser: leaving somebody on a screen
		// that says they are signed in, right after they pressed sign out, is the worse outcome.
		if (!(error instanceof ApiError)) {
			toasts.show("Signed out here, but Sift couldn't confirm it", { tone: 'error' });
		}
	}
	session.forget();
	// The corner panel's file would be drawn again at the next sign-in in this window.
	mini.close();
	theme.forget();
	await goto('/');
}
