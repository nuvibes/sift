<script lang="ts">
	/* NOT ON THE GALLERY: it draws nothing at all. Every line of it is a timer or a window event deciding
	   when to ask the server to lock the vault; there is no element and nothing to look at. */

	/*
	 * When the vault shuts by itself. The browser notices; every trigger asks the server to lock,
	 * which is what takes hidden things off the screen. Nothing may fail open: the preferences
	 * start at the strict defaults. Two timers: the short one shuts Hidden, the long one shuts
	 * Sift.
	 */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/api/client';
	import { vault } from '$lib/shell/vault.svelte';
	import { watching } from '$lib/player/watching.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { matches } from '$lib/shell/shortcuts';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import {
		APP_LOCK_AFTER_IDLE_KEY,
		APP_LOCK_ENABLED_KEY,
		LOCK_AFTER_IDLE_KEY,
		LOCK_ON_BLUR_KEY,
		LOCK_ON_CLOSE_KEY,
		STRICT_DEFAULTS,
		readVaultPreferences,
		type VaultPreferences
	} from '$lib/shell/vault-preferences';

	// Strict, not empty: a trigger firing before the preferences land must lock.
	let prefs = $state<VaultPreferences>({ ...STRICT_DEFAULTS });

	// Only armed while the vault is open.
	let idleTimer: ReturnType<typeof setTimeout> | undefined;

	// Armed whenever the app lock is on: it is about the machine being left alone.
	let appLockTimer: ReturnType<typeof setTimeout> | undefined;

	async function lockNow(why: string, { always = false } = {}) {
		// `always` for the panic control: a lock that never landed still marks itself locked here.
		if (!vault.unlocked && !always) return;
		const reached = await vault.lock();
		if (!reached) {
			toasts.show("Sift couldn't be reached, so it may still be showing them elsewhere", {
				tone: 'error'
			});
			return;
		}
		toasts.show(why, { tone: 'info' });
	}

	function armIdleTimer() {
		clearTimeout(idleTimer);
		const minutes = prefs[LOCK_AFTER_IDLE_KEY];
		// Zero means never, honoured exactly.
		if (!vault.unlocked || minutes <= 0) return;
		// WATCHING A VIDEO IS USING SIFT: not armed while something plays (see the effect below).
		if (watching.anything) return;
		idleTimer = setTimeout(
			() => void lockNow('Hidden items hidden again after a quiet spell.'),
			minutes * 60_000
		);
	}

	async function lockSiftNow() {
		// The mark goes on the session row, so every tab and client is shut together.
		try {
			await api.post('/auth/lock');
		} catch {
			// A lock the server did not take is not a lock; the timer fires again later.
			return;
		}
		vault.sessionLocked();
		// The lock screen: nothing on the front page would re-ask.
		await goto('/locked', { replaceState: true });
	}

	function armAppLockTimer() {
		clearTimeout(appLockTimer);
		const minutes = prefs[APP_LOCK_AFTER_IDLE_KEY];
		// The PIN too: locking an account without one ends at a door with no key. A timer does
		// nothing instead; the panic shortcut signs out.
		if (!prefs[APP_LOCK_ENABLED_KEY] || !vault.pinSet || minutes <= 0) return;
		if (watching.anything) return;
		appLockTimer = setTimeout(() => void lockSiftNow(), minutes * 60_000);
	}

	function onActivity() {
		armIdleTimer();
		armAppLockTimer();
	}

	function onLeft() {
		if (prefs[LOCK_ON_BLUR_KEY]) void lockNow('Hidden items hidden again when you left Sift.');
	}

	function onVisibilityChange() {
		// `hidden`: another tab, another window, or the screen locking.
		if (document.visibilityState === 'hidden' && prefs[LOCK_ON_BLUR_KEY]) {
			void lockNow('Hidden items hidden again when you left the tab.');
		}
	}

	function onPageHide() {
		if (!prefs[LOCK_ON_CLOSE_KEY] || !vault.unlocked) return;
		// `keepalive` lets the request outlive the closing page.
		void vault.lock({ keepalive: true });
	}

	/* Re-armed whenever these move; without the launch lock, which is about a tab being opened. */
	whenChanged(settingChanges, () => {
		void (async () => {
			prefs = await readVaultPreferences();
			armIdleTimer();
			armAppLockTimer();
		})();
	});

	onMount(() => {
		/*
		 * `scroll` does not bubble and the document never scrolls, so it is caught in the capture
		 * phase.
		 */
		window.addEventListener('scroll', onActivity, { capture: true, passive: true });

		void (async () => {
			/* The launch lock is the layout's (`lockOnLaunch`). */
			const loaded = await readVaultPreferences();
			prefs = loaded;
			await vault.load();
			armIdleTimer();
			armAppLockTimer();
		})();

		return () => {
			clearTimeout(idleTimer);
			clearTimeout(appLockTimer);
			window.removeEventListener('scroll', onActivity, { capture: true });
		};
	});

	// Re-armed when Hidden opens or shuts or the preferences land. Reading `watching.anything` is
	// what starts the clock from the moment playback stops.
	$effect(() => {
		void vault.unlocked;
		void watching.anything;
		armIdleTimer();
		armAppLockTimer();
	});

	/*
	 * The panic control, from anywhere, even a text box. Not Escape, which people press constantly.
	 */
	function onKeydown(event: KeyboardEvent) {
		// The registry turns down Alt for every shortcut.
		if (matches(event, 'vault.panicLock')) {
			event.preventDefault();
			void lockNow('Hidden items hidden.', { always: true });
			return;
		}
		// Any other key is somebody being here.
		onActivity();
	}
</script>

<!-- `blur` too: switching to another application leaves the tab visible. -->
<svelte:window
	onkeydown={onKeydown}
	onpointerdown={onActivity}
	onpagehide={onPageHide}
	onblur={onLeft}
/>
<svelte:document onvisibilitychange={onVisibilityChange} />
