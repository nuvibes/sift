<script lang="ts">
	/* LIVE: nothing moves it (the lock screen reads who is signed in once, and unlocking leaves it) */
	/*
	 * Sift, locked.
	 *
	 * This screen is drawn because the SERVER refused the request that brought us here, not to hide
	 * a page that is still working underneath. The session carries a locked mark on its row, so
	 * every other tab, a reload and the credential replayed straight at the API are refused the same
	 * way. There is nothing behind this to reach.
	 *
	 * Two ways out and both are offered. The PIN reopens the session where it was. Signing out ends
	 * it, which is the stronger act and the one somebody wants when they have forgotten the PIN,
	 * so it is a plain link rather than something to find.
	 *
	 * A run of wrong PINs destroys the session and lands the person at the password. The count is
	 * not shown, and that is deliberate: telling somebody guessing how many tries are left is
	 * telling them how long they may keep going.
	 */
	import { onMount, tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { Button, DoorCard, Field, PinBox, TextInput } from '$lib/components/common';
	import { api, ApiError } from '$lib/api/client';
	import { motion } from '$lib/shell/motion.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { theme } from '$lib/theme/theme.svelte';
	import type { components } from '$lib/api/schema';

	let pin = $state('');
	let password = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);
	let pinInput = $state<HTMLInputElement | null>(null);
	let passwordInput = $state<HTMLInputElement | null>(null);

	let card = $state<HTMLElement | null>(null);

	/* Which secret this screen is asking for.
	 *
	 * The password is always accepted, so it is the fallback and the default for anybody who has not
	 * asked for the shortcut. Locking shuts a session rather than ending it, which is what makes a
	 * password box here meaningfully different from the sign-in page: no username, and the session,
	 * the saved logins and the place you were are all still there behind it.
	 */
	let usingPin = $state(false);
	//: Whether there is a PIN to switch to at all. Fixed once the answer lands; `usingPin` moves.
	let pinOffered = $state(false);

	onMount(() => {
		void (async () => {
			try {
				// `/auth/me` is one of the three routes a locked session may still call, so this is
				// answerable from here. Both halves of "is the PIN on offer" are resolved on the
				// server, which is why this screen asks once rather than reading two settings.
				const me = await api.get<components['schemas']['ViewerResponse']>('/auth/me');
				pinOffered = me.pin_unlock_offered;
				usingPin = pinOffered;
			} catch {
				// Left on the password, which always works. Guessing the other way would show a PIN
				// box to somebody who has none.
			}
			// After the redraw: the PIN box only exists once `usingPin` has drawn it, so focusing
			// before then finds nothing and the first digits typed go nowhere.
			await tick();
			(usingPin ? pinInput : passwordInput)?.focus();
		})();
	});

	/* The same refusal the vault box gives, for the same reason and by the same means.
	 *
	 * From script rather than a class: a CSS animation runs once when its class arrives, so the
	 * second wrong PIN would not move at all. Silent under reduced motion: the message is already
	 * there and already announced. */
	function refuse() {
		if (!card || motion.reduced) return;
		card.animate(
			[
				{ transform: 'translateX(0)' },
				{ transform: 'translateX(-6px)' },
				{ transform: 'translateX(5px)' },
				{ transform: 'translateX(-3px)' },
				{ transform: 'translateX(0)' }
			],
			{ duration: 220, easing: 'ease-in-out' }
		);
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (busy || (usingPin ? pin : password).length === 0) return;
		busy = true;
		error = null;
		try {
			if (usingPin) await api.post('/auth/unlock', { body: { pin } });
			else await api.post('/auth/unlock/password', { body: { password } });
			/*
			 * A full load rather than a client-side navigation. Everything this browser was holding
			 * was thrown away when the lock sent it here, and coming back through the shell's own
			 * boot is what re-reads it: who is signed in, what is on the grid, whether Hidden is
			 * open. It is not: unlocking Sift and unlocking Hidden are two acts and this was the
			 * first one.
			 */
			window.location.replace('/browse');
		} catch (caught) {
			pin = '';
			password = '';
			if (usingPin && caught instanceof ApiError && caught.status === 403) {
				// The server takes a PIN only from the local network. It says so in a sentence, and
				// the password is the way in from here, so the screen moves to it for good.
				error = caught.detail ?? 'Use your password to unlock Sift from here.';
				pinOffered = false;
				usingPin = false;
			} else if (caught instanceof ApiError && caught.status === 429) {
				error = 'Too many attempts. Wait a few minutes, or sign out and use your password.';
			} else if (caught instanceof ApiError && caught.status === 401) {
				// Named after what was actually typed. The same words whether this was the first
				// wrong one or the last (see above), but telling somebody their PIN is wrong when
				// they typed a password is telling them to check the wrong thing.
				error = usingPin ? 'Incorrect PIN.' : 'Incorrect password.';
			} else {
				error = 'That did not work.';
			}
			refuse();
			// After the redraw, so a switch to the password lands in the field it just drew.
			await tick();
			(usingPin ? pinInput : passwordInput)?.focus();
		} finally {
			busy = false;
		}
	}

	async function signOut() {
		try {
			await api.post('/auth/logout');
		} catch {
			// Signed out here regardless: a sign-out the server did not hear still clears this
			// browser, and the door is the right place to land either way.
		}
		session.forget();
		theme.forget();
		await goto('/login', { replaceState: true });
	}
</script>

<svelte:head>
	<title>Sift is locked</title>
</svelte:head>

<!--
	NO EXPLAINING LINE, DELIBERATELY.

	The heading says Locked, the field is labelled PIN or Password, and the button says Unlock; the
	only thing a sentence would carry on its own is WHICH credential is wanted, and the field's own
	label is where somebody actually looks for that. `DoorCard` makes the same call about its
	heading one file over: a line that repeats the control under it says the same word twice to
	everybody who can see it.
-->
<DoorCard heading="Locked" centred bind:element={card} onsubmit={submit}>
	{#if usingPin}
		<Field label="PIN">
			{#snippet control({ id, describedBy })}
				<!-- The shared PIN control. See `PinBox`. -->
				<PinBox {id} bind:element={pinInput} bind:value={pin} {describedBy} />
			{/snippet}
		</Field>
	{:else}
		<Field label="Password">
			{#snippet control({ id, describedBy })}
				<TextInput
					{id}
					bind:element={passwordInput}
					type="password"
					name="password"
					bind:value={password}
					{describedBy}
					autocomplete="current-password"
					required
				/>
			{/snippet}
		</Field>
	{/if}

	{#if error}
		<!-- Announced, because somebody using a screen reader has no other way to know the form
		     came back with something to say. -->
		<p class="error" role="alert">{error}</p>
	{/if}

	<Button type="submit" tone="primary" full {busy}>Unlock</Button>

	{#if pinOffered}
		<!-- Only when there is a PIN to switch to. The password is always accepted, so this is a
		     shortcut between two working doors rather than the difference between in and out. -->
		<Button
			tone="link"
			onclick={() => {
				usingPin = !usingPin;
				error = null;
				pin = '';
				password = '';
			}}
		>
			{usingPin ? 'Use your password instead' : 'Use your PIN instead'}
		</Button>
	{/if}

	<Button tone="link" onclick={signOut}>Sign out instead</Button>
</DoorCard>

<style>
	.error {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-bad-text);
	}
</style>
