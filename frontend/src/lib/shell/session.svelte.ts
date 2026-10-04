import { api, setCsrfToken, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/* LIVE: followed by routes/+layout.svelte (whether the saved keys are locked, read again when the live connection comes up and on the jobs bell while they are; who is signed in moves nothing here: the live connection is keyed on it, and a session ended elsewhere closes the connection) */

/* Who is signed in, according to the server.
 *
 * Read once when the app starts and kept for the shell to draw from. Nothing here is a permission:
 * the role decides which nav items are worth rendering, and that is a courtesy to the person using
 * the app, not a control. Every endpoint behind those items checks for itself, and would refuse a
 * request made with curl and a stolen guess just the same. Anyone reading this file can see the
 * whole list of admin routes; that costs nothing, because knowing where they are was never what
 * kept anyone out.
 */

export type Viewer = components['schemas']['ViewerResponse'];

class Session {
	/** null means nobody is signed in. undefined means we have not asked yet. */
	viewer = $state<Viewer | null | undefined>(undefined);

	readonly isAdmin = $derived(this.viewer?.role === 'admin');
	readonly isSignedIn = $derived(this.viewer != null);
	readonly adminUnlocked = $derived(this.isAdmin && this.viewer?.locked !== true);
	/** Whether to offer the "save a copy" action. A courtesy; the save endpoint is the control. */
	readonly canSave = $derived(this.viewer?.can_save_to_device ?? false);
	/** Whether saved logins and tunnels are unreadable until the password is given again. */
	readonly secretsLocked = $derived(this.viewer?.secrets_locked === true);

	/** Ask the server who this is. The answer also carries the CSRF token for this session. */
	async load(): Promise<void> {
		try {
			const viewer = await api.get<Viewer>('/auth/me');
			this.viewer = viewer;
			setCsrfToken(viewer.csrf_token);
		} catch (error) {
			// A 401 is the ordinary answer for a visitor who has not signed in, not a failure.
			if (error instanceof ApiError && error.status === 401) {
				this.viewer = null;
				setCsrfToken(null);
				return;
			}
			throw error;
		}
	}

	/**
	 * Ask again whether this session's saved keys are locked, and change nothing else.
	 *
	 * The one part of who is signed in that moves under a session: a restart seals the keys and
	 * keeps the cookie, and a password given in another window unlocks them. Only the flag is taken,
	 * and with it which run of the server answered (`boot`, what a Not now on the unlock bar is
	 * held to), so a screen keyed on who is signed in does not start again for it. Silent on a
	 * failure: the next ask says.
	 */
	async recheck(): Promise<void> {
		const before = this.viewer;
		if (!before) return;
		try {
			const viewer = await api.get<Viewer>('/auth/me');
			if (this.viewer?.id !== viewer.id) return;
			if (this.viewer.secrets_locked !== viewer.secrets_locked) {
				this.viewer.secrets_locked = viewer.secrets_locked;
			}
			if (this.viewer.boot !== viewer.boot) this.viewer.boot = viewer.boot;
		} catch {
			// See above.
		}
	}

	/** Adopt the account a sign-in or a first-run setup just returned. */
	adopt(viewer: Viewer): void {
		this.viewer = viewer;
		setCsrfToken(viewer.csrf_token);
	}

	forget(): void {
		this.viewer = null;
		setCsrfToken(null);
	}
}

/**
 * Whether this instance still needs its one admin created.
 *
 * The sign-in screen has to know before it can draw anything: a fresh instance needs a
 * create-the-admin form, a configured one needs a login. Public, because there is nobody signed in
 * to ask on behalf of.
 */
export async function needsSetup(): Promise<boolean> {
	const status = await api.get<components['schemas']['SetupStatusResponse']>('/auth/status');
	return status.needs_setup;
}

export const session = new Session();
