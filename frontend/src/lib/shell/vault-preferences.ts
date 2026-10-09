/* The vault's preferences by the server's names: a typo is a silent default and a 400. */

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

/** The server bounds it. */
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

/* The server's defaults again, used when the read failed, each erring towards locking sooner. */
export const STRICT_DEFAULTS: VaultPreferences = {
	[CONCEALMENT_KEY]: 'fully_gone',
	[LOCK_AFTER_IDLE_KEY]: 15,
	[LOCK_ON_BLUR_KEY]: false,
	[LOCK_ON_LAUNCH_KEY]: true,
	[LOCK_ON_CLOSE_KEY]: true,
	/*
	 * Except the app lock, which errs towards OFF: turning it on lets four digits reopen a session.
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

/*
 * Null, never 0: 0 means never lock, and an empty box is somebody typing. Clamped to the server's
 * bounds.
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

/** Awaited before the screen draws, so no read leaves before the lock lands. */
export async function lockOnLaunch(lock: () => Promise<unknown>): Promise<VaultPreferences> {
	const loaded = await readVaultPreferences();
	if (loaded[LOCK_ON_LAUNCH_KEY]) await lock();
	return loaded;
}
