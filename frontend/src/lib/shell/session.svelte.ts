import { api, setCsrfToken, ApiError } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/* LIVE: followed by routes/+layout.svelte (whether the saved keys are locked, read again when the live connection comes up and on the jobs bell while they are; who is signed in moves nothing here: the live connection is keyed on it, and a session ended elsewhere closes the connection) */

/* Who is signed in. Nothing here is a permission: every endpoint checks for itself. */

export type Viewer = components['schemas']['ViewerResponse'];

class Session {
	/** null means nobody is signed in; undefined means not asked yet. */
	viewer = $state<Viewer | null | undefined>(undefined);

	readonly isAdmin = $derived(this.viewer?.role === 'admin');
	readonly isSignedIn = $derived(this.viewer != null);
	readonly adminUnlocked = $derived(this.isAdmin && this.viewer?.locked !== true);
	/** A courtesy; the save endpoint is the control. */
	readonly canSave = $derived(this.viewer?.can_save_to_device ?? false);
	readonly secretsLocked = $derived(this.viewer?.secrets_locked === true);

	/** The answer also carries the CSRF token. */
	async load(): Promise<void> {
		try {
			const viewer = await api.get<Viewer>('/auth/me');
			this.viewer = viewer;
			setCsrfToken(viewer.csrf_token);
		} catch (error) {
			// A 401 is the ordinary answer before signing in.
			if (error instanceof ApiError && error.status === 401) {
				this.viewer = null;
				setCsrfToken(null);
				return;
			}
			throw error;
		}
	}

	/** Only the locked flag and `boot` are taken, so nothing keyed on who is signed in restarts. */
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
			// The next ask says.
		}
	}

	adopt(viewer: Viewer): void {
		this.viewer = viewer;
		setCsrfToken(viewer.csrf_token);
	}

	forget(): void {
		this.viewer = null;
		setCsrfToken(null);
	}
}

/** Public: there is nobody signed in to ask on behalf of. */
export async function needsSetup(): Promise<boolean> {
	const status = await api.get<components['schemas']['SetupStatusResponse']>('/auth/status');
	return status.needs_setup;
}

export const session = new Session();
