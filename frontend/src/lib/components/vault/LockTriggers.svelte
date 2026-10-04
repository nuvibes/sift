<script lang="ts">
	/* NOT ON THE GALLERY: it draws nothing at all. Every line of it is a timer or a window event deciding
	   when to ask the server to lock the vault; there is no element and nothing to look at. */

	/*
	 * When the vault shuts by itself.
	 *
	 * The noticing happens here, because a timer and a tab event are things only the browser can
	 * see. What happens when one fires does not: every trigger asks the server to lock, and the
	 * server forgetting the unlock is what takes the hidden things off the screen. Closing it here
	 * would only be a class on a div, with the data still in the page.
	 *
	 * Nothing here may fail open. The preferences are fetched, so there is a moment before they
	 * arrive, and the settings this file reads start at the strict defaults rather than at nothing,
	 * because a tab switched away in that moment must still lock. Failing to lock is the failure
	 * that matters; the other direction only costs a PIN entry.
	 *
	 * Mounted once, inside the shell, for every signed-in account: a guest has their own Hidden and
	 * PIN, and needs the timer, the tab trigger and a way to close it short of signing out.
	 *
	 * Two timers, different sizes on purpose. The short one shuts Hidden and leaves the library
	 * open. The long one shuts Sift itself, and is off unless somebody turns it on; see
	 * `vault-preferences` for why that default does not follow this file's strict rule.
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

	// Starts strict, not empty. See the note above: a trigger that fires before the real preferences
	// land has to act, and acting means locking.
	let prefs = $state<VaultPreferences>({ ...STRICT_DEFAULTS });

	// The idle timer. Reset by any sign of a person, and only armed while the vault is actually open:
	// a timer running against a shut vault would fire a request that changes nothing, forever.
	let idleTimer: ReturnType<typeof setTimeout> | undefined;

	// And the longer one, which shuts Sift rather than Hidden. Armed whenever the app lock is on,
	// not only while Hidden is open: it is about the machine being left alone, which has nothing to
	// do with whether anything is hidden.
	let appLockTimer: ReturnType<typeof setTimeout> | undefined;

	async function lockNow(why: string, { always = false } = {}) {
		// `always` is for the panic control. The rest early-return when the client already believes
		// the vault is shut, which saves a pointless request, but the client can believe that
		// wrongly, because a lock whose request never landed still marks itself locked here. The
		// one control somebody presses when it matters must not be the one that trusts that belief.
		if (!vault.unlocked && !always) return;
		// The shell empties every vault-scoped cache when this lands, so nothing is redrawn here.
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
		// Zero means never, which is the one setting that has to be honoured exactly: somebody who
		// turned the timer off and then found the vault shut anyway would stop trusting the setting.
		if (!vault.unlocked || minutes <= 0) return;
		// WATCHING A VIDEO IS USING SIFT. Nothing else here can see that: the triggers below are
		// keys, scrolling and the pointer, and somebody watching a film touches none of them. Not
		// armed at all while something is playing, and armed afresh the moment it stops. See the
		// effect at the bottom of this file, which is what makes "the moment it stops" true.
		if (watching.anything) return;
		idleTimer = setTimeout(
			() => void lockNow('Hidden items hidden again after a quiet spell.'),
			minutes * 60_000
		);
	}

	async function lockSiftNow() {
		// Locking Sift is the server's: the mark goes on the session row, so every tab and every
		// client is shut at once. Nothing is drawn over anything here.
		try {
			await api.post('/auth/lock');
		} catch {
			// A lock the server did not take is not a lock. Say nothing and leave the timer to fire
			// again on the next quiet spell rather than pretending the screen is shut.
			return;
		}
		// Hidden shut with the session: see `vault.sessionLocked`.
		vault.sessionLocked();
		// The lock screen, not the front page: nothing there would re-ask, so the shell would carry
		// on drawing a library the server has already shut.
		await goto('/locked', { replaceState: true });
	}

	function armAppLockTimer() {
		clearTimeout(appLockTimer);
		const minutes = prefs[APP_LOCK_AFTER_IDLE_KEY];
		// All three have to be true. The timer is meaningless without the lock being on, and zero
		// means never: the one setting that has to be honoured exactly.
		//
		// The PIN is the third because it is the only thing that opens what this shuts. Locking an
		// account that has not set one leaves it at a door with no key: every attempt is refused, and
		// after a few of them the session is destroyed and the password is what comes back. That is
		// the deliberate manual behaviour and a surprising thing for a quiet spell to do on its own,
		// so this does nothing instead. The shortcut takes the same reading and signs out rather than
		// locking, because a panic control has to act; a timer does not.
		if (!prefs[APP_LOCK_ENABLED_KEY] || !vault.pinSet || minutes <= 0) return;
		// Same reasoning as the shorter timer, and it matters more here: this one takes the whole
		// window to the lock screen, so a film left playing would be ended by it rather than merely
		// hidden behind it.
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
		// Leaving the tab, not arriving at it. `hidden` fires for a switch to another tab, another
		// window, and for the screen locking, which are the three shapes of "somebody else can see
		// this now" that a browser is actually able to report.
		if (document.visibilityState === 'hidden' && prefs[LOCK_ON_BLUR_KEY]) {
			void lockNow('Hidden items hidden again when you left the tab.');
		}
	}

	function onPageHide() {
		if (!prefs[LOCK_ON_CLOSE_KEY] || !vault.unlocked) return;
		// The tab is going away, so there is no time for a promise to settle and nothing to tell the
		// person afterwards. `keepalive` is what lets the request outlive the page that sent it.
		void vault.lock({ keepalive: true });
	}

	/* And whenever one of these moves, wherever it was changed.
	 *
	 * WITHOUT THE LAUNCH LOCK, which is the whole reason this is not the block below run a second
	 * time: `lock_on_launch` is about a tab being OPENED, and re-running it here would shut the
	 * vault every time anybody moved an unrelated switch, including, absurdly, the switch that
	 * turned it on.
	 *
	 * The timers are re-armed because these preferences are what decide whether they run at all.
	 * Without this, turning idle-locking on would take effect at the next page load, and the desktop
	 * window's page never reloads, so for the app it would be the next restart: a security control
	 * that silently did not apply, which is worse than one that is off.
	 */
	whenChanged(settingChanges, () => {
		void (async () => {
			prefs = await readVaultPreferences();
			armIdleTimer();
			armAppLockTimer();
		})();
	});

	onMount(() => {
		/* Scrolling is the commonest thing somebody does here, and it is the one activity that never
		 * reaches `<svelte:window>`: `scroll` does not bubble, and nothing in this app scrolls the
		 * document: the shell and the grid each scroll an inner element. Listened for in the
		 * capture phase, on the window, which does see them. Without this, somebody reading a long
		 * grid is idle by every measure the timer has and gets locked out mid-scroll.
		 */
		window.addEventListener('scroll', onActivity, { capture: true, passive: true });

		void (async () => {
			/* The launch lock is the layout's (`lockOnLaunch`), landed before this or any screen
			 * mounts; this reads the preferences for the triggers that follow. */
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

	// Re-arm whenever Hidden opens or shuts, or the preferences land, so unlocking starts the clock
	// and locking stops it. The Sift timer is re-armed too, because the preferences arriving is what
	// tells it whether it should be running at all.
	//
	// `watching.anything` is read here as well, and that read is what makes playback work as
	// activity rather than merely as a veto: while something plays, both timers refuse to arm; when
	// it stops, this effect runs and starts the clock from THAT moment. Without the read, a film
	// that ended would leave both timers disarmed until the next keypress, which is the opposite
	// failure, and the more dangerous one.
	$effect(() => {
		void vault.unlocked;
		void watching.anything;
		armIdleTimer();
		armAppLockTimer();
	});

	/* The panic control: a key you can hit without looking, from anywhere, including from inside a
	 * text box. Escape is not it: Escape closes whatever is open and people press it constantly.
	 */
	function onKeydown(event: KeyboardEvent) {
		// Ctrl+Alt+Shift+L does not lock: the registry turns down Alt for every shortcut, because
		// an Alt combination belongs to the browser or the window manager.
		if (matches(event, 'vault.panicLock')) {
			event.preventDefault();
			void lockNow('Hidden items hidden.', { always: true });
			return;
		}
		// Any other key is somebody being here. `keydown` rather than `keypress`, which is
		// deprecated and never fires for the arrows, Tab or Backspace: the keys somebody reading
		// rather than typing actually uses.
		onActivity();
	}
</script>

<!--
	`blur` as well as `visibilitychange`, because the setting says "another tab or window" and the
	two events cover different halves of that: switching tabs fires visibility, switching to another
	application does not: the tab is still visible, it just is not in front.
-->
<svelte:window
	onkeydown={onKeydown}
	onpointerdown={onActivity}
	onpagehide={onPageHide}
	onblur={onLeft}
/>
<svelte:document onvisibilitychange={onVisibilityChange} />
