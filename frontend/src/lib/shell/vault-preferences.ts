/* The vault's preferences, by the names the server stores them under.
 *
 * Written out here rather than typed into each screen, because these strings are a contract with
 * the server: a typo in one of them is a preference that silently reads as its default and a save
 * that comes back as a 400. One spelling, in one file.
 */

/* WHY NOT FOLLOWED: lib/components/vault/LockTriggers.svelte watches `settingChanges` and reads
   this again. Nothing here holds anything: it is one function that asks and answers, so there is
   no state in this file that could go stale. What holds the answer is what has to be told. */
import { fetchSettingValues } from '$lib/settings-ui/settings';

export const CONCEALMENT_KEY = 'vault.concealment';
export const LOCK_AFTER_IDLE_KEY = 'vault.lock_after_idle_minutes';
export const LOCK_ON_BLUR_KEY = 'vault.lock_on_blur';
export const LOCK_ON_LAUNCH_KEY = 'vault.lock_on_launch';
export const LOCK_ON_CLOSE_KEY = 'vault.lock_on_close';
export const APP_LOCK_ENABLED_KEY = 'vault.app_lock_enabled';
export const APP_LOCK_AFTER_IDLE_KEY = 'vault.app_lock_after_idle_minutes';

/** A day. Not a meaningful privacy setting at that length, but the server bounds it and the
 * control should not offer what would only be refused. */
export const MAX_IDLE_MINUTES = 24 * 60;

export type Concealment = 'fully_gone' | 'placeholder';

export interface VaultPreferences {
	[CONCEALMENT_KEY]: Concealment;
	[LOCK_AFTER_IDLE_KEY]: number;
	[LOCK_ON_BLUR_KEY]: boolean;
	[LOCK_ON_LAUNCH_KEY]: boolean;
	[LOCK_ON_CLOSE_KEY]: boolean;
	[APP_LOCK_ENABLED_KEY]: boolean;
	[APP_LOCK_AFTER_IDLE_KEY]: number;
}

/* The defaults, which are the server's defaults written down a second time, and the state the
 * lock triggers start in before the real values have arrived.
 *
 * That duplication is deliberate and is the safe direction. These are only reached when the request
 * for the real values failed, and every one of them errs towards locking sooner rather than later:
 * a browser that could not read the preferences must not decide, on its own, to leave the vault
 * open longer than the person asked.
 */
export const STRICT_DEFAULTS: VaultPreferences = {
	[CONCEALMENT_KEY]: 'fully_gone',
	[LOCK_AFTER_IDLE_KEY]: 15,
	[LOCK_ON_BLUR_KEY]: false,
	[LOCK_ON_LAUNCH_KEY]: true,
	[LOCK_ON_CLOSE_KEY]: true,
	/* The two app-lock settings break the "strict" rule above, and on purpose.
	 *
	 * Everywhere else the fallback errs towards locking sooner. Here it errs towards NOT turning the
	 * app lock on, because turning it on is what makes the PIN able to reopen a session: swapping a
	 * password for four digits. A browser that could not read the preferences must not make that
	 * trade on somebody's behalf. Off, the lock shortcut ends the session, which is the stricter of
	 * the two behaviours anyway.
	 */
	[APP_LOCK_ENABLED_KEY]: false,
	[APP_LOCK_AFTER_IDLE_KEY]: 0
};

export async function readVaultPreferences(): Promise<VaultPreferences> {
	try {
		const values = await fetchSettingValues();
		return {
			[CONCEALMENT_KEY]: asConcealment(values.get(CONCEALMENT_KEY)),
			[LOCK_AFTER_IDLE_KEY]: asWholeNumber(
				values.get(LOCK_AFTER_IDLE_KEY),
				STRICT_DEFAULTS[LOCK_AFTER_IDLE_KEY]
			),
			[LOCK_ON_BLUR_KEY]: asBoolean(
				values.get(LOCK_ON_BLUR_KEY),
				STRICT_DEFAULTS[LOCK_ON_BLUR_KEY]
			),
			[LOCK_ON_LAUNCH_KEY]: asBoolean(
				values.get(LOCK_ON_LAUNCH_KEY),
				STRICT_DEFAULTS[LOCK_ON_LAUNCH_KEY]
			),
			[LOCK_ON_CLOSE_KEY]: asBoolean(
				values.get(LOCK_ON_CLOSE_KEY),
				STRICT_DEFAULTS[LOCK_ON_CLOSE_KEY]
			),
			[APP_LOCK_ENABLED_KEY]: asBoolean(
				values.get(APP_LOCK_ENABLED_KEY),
				STRICT_DEFAULTS[APP_LOCK_ENABLED_KEY]
			),
			[APP_LOCK_AFTER_IDLE_KEY]: asWholeNumber(
				values.get(APP_LOCK_AFTER_IDLE_KEY),
				STRICT_DEFAULTS[APP_LOCK_AFTER_IDLE_KEY]
			)
		};
	} catch {
		return { ...STRICT_DEFAULTS };
	}
}

/* What a typed idle-timeout should be stored as, or null to leave it alone.
 *
 * Null rather than a default, because the one value that must never be inferred is 0: it means
 * "never lock", and an empty box is somebody midway through typing a new number, not a request to
 * turn the timer off. Clamped at both ends so the control cannot send what the server would only
 * refuse.
 */
export function idleMinutesFrom(entered: number): number | null {
	if (Number.isNaN(entered)) return null;
	return Math.min(MAX_IDLE_MINUTES, Math.max(0, Math.trunc(entered)));
}

function asConcealment(value: unknown): Concealment {
	return value === 'placeholder' ? 'placeholder' : 'fully_gone';
}

function asWholeNumber(value: unknown, fallback: number): number {
	return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : fallback;
}

function asBoolean(value: unknown, fallback: boolean): boolean {
	return typeof value === 'boolean' ? value : fallback;
}

/**
 * The launch lock: the preferences read, and Hidden shut first where they ask for that.
 *
 * Awaited by the layout before it draws the screen, so no read of the library leaves before the
 * lock has landed. A page asking for its list first would be answered with the vault still open.
 * `lock` is the vault's own; it is handed in so this stays a plain read of the preferences.
 */
export async function lockOnLaunch(lock: () => Promise<unknown>): Promise<VaultPreferences> {
	const loaded = await readVaultPreferences();
	if (loaded[LOCK_ON_LAUNCH_KEY]) await lock();
	return loaded;
}
