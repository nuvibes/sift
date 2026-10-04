<script lang="ts">
	import { Button, PinBox } from '$lib/components/common';
	/*
	 * NOT ON THE GALLERY: one of these exists, in the layout, for the whole application, for the
	 * same reason as the app's one toast host and its settings panel. The layout owns it so any
	 * screen can raise it (an Unlock button on a "some of these are in your vault" message,
	 * for one). A second instance on the gallery would be a second listener to `vaultPrompt`, and a
	 * PIN box opening because somebody scrolled past a design page is the wrong kind of surprise.
	 */

	/* The PIN box. Every reveal of hidden things goes through this, every time.
	 *
	 * It is asked for again even when the vault is already open, and that is deliberate rather than
	 * a missing optimisation: showing hidden things is the one act this whole feature exists to make
	 * a decision rather than a side effect. There is no server route that opens the vault without a
	 * PIN, so there is nothing here that could be relaxed into skipping it.
	 *
	 * The insistent flavour, for the reason the confirm dialog uses it: this is a box asking for a
	 * credential, and assistive technology should be told it interrupted on purpose.
	 */
	import Modal from '$lib/components/common/Modal.svelte';
	import CreatePin from './CreatePin.svelte';
	import { motion } from '$lib/shell/motion.svelte';
	import { vault, type UnlockFailure } from '$lib/shell/vault.svelte';

	interface Props {
		open?: boolean;
		/** Called once the vault is actually open. The screen reloads from here. */
		onunlocked?: () => void;
		/**
		 * What the PIN is asked FOR, when it is an act waiting on it rather than showing hidden
		 * items: the act's words ("Put it back"), said as the title, and the sentence says the PIN
		 * lets Sift do it. Absent is the prompt's own reason. See `vaultPrompt.opened`.
		 */
		reason?: string | null;
	}

	let { open = $bindable(false), onunlocked, reason = null }: Props = $props();

	/* The prompt's own words, or the waiting act's. Either way the PIN opens Hidden on this device,
	   and the sentence says so, because that is what it does. */
	const said = $derived(
		reason
			? {
					title: reason,
					description:
						'Enter your PIN so Sift can do this. Hidden items then stay showing on this device until you hide them again, and hide themselves when Sift restarts.'
				}
			: {
					title: 'Show hidden items',
					description:
						'Enter your PIN to show what you have hidden. It stays showing on this device until you hide it again, and it hides itself when Sift restarts.'
				}
	);

	let pin = $state('');
	let error = $state<string | null>(null);
	let working = $state(false);
	let pinInput = $state<HTMLInputElement | null>(null);

	let sheet = $state<HTMLElement | null>(null);

	/* A refusal has to be felt as well as read.
	 *
	 * The message alone is easy to miss: the box does not move, the field simply empties, and
	 * somebody who typed a digit wrong reads that as the app having eaten the entry rather than as
	 * having been told no.
	 *
	 * Driven from script rather than from a class, because a class cannot do it. A CSS animation
	 * runs when the class arrives and never again while it stays, so the second wrong PIN, which
	 * is the one somebody most needs an answer to, would sit perfectly still. Every call here
	 * starts a new animation.
	 *
	 * Silent for anybody who has asked their machine for less movement. Reduced motion is also the
	 * escape hatch on a slow machine, and the message is already there and already announced.
	 */
	function refuse() {
		if (!sheet || motion.reduced) return;
		sheet.animate(
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

	// Cleared on the way in, not on the way out: a PIN left in the box is a PIN sitting on screen
	// for whoever the vault was being hidden from.
	$effect(() => {
		if (open) {
			pin = '';
			error = null;
			working = false;
		}
	});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (working || pin.length === 0) return;
		working = true;
		error = null;
		let failure: UnlockFailure | null;
		try {
			failure = await vault.unlock(pin);
		} catch {
			// Anything that is not a refusal: the network gone, the server down. Without catching
			// it the throw escapes this handler as an unhandled rejection, `working` never goes back
			// to false, and the box stays disabled with no message and no way out but a reload.
			error = 'Sift could not be reached. Try again.';
			pin = '';
			working = false;
			return;
		}
		working = false;
		if (failure === 'wrong-pin') {
			error = 'Incorrect PIN.';
			pin = '';
			refuse();
			return;
		}
		if (failure === 'too-many-attempts') {
			error = 'Too many tries. Wait a few minutes and try again.';
			pin = '';
			refuse();
			return;
		}
		open = false;
		onunlocked?.();
	}
</script>

<!--
	`onOpenAutoFocus` is this box saying where it would rather focus went. The library moves focus to
	the sheet itself on open (that is what makes Escape and Tab work from the first render) and
	that lands AFTER a plain `autofocus` on the input, so the native attribute never won on its own.
	Here it goes to the field the whole box exists to collect.

	`bind:sheet` is for the shake below, which needs the element the library drew.
-->
<Modal
	bind:open
	bind:sheet
	insistent
	title={said.title}
	description={said.description}
	onOpenAutoFocus={(event) => {
		event.preventDefault();
		pinInput?.focus();
	}}
>
	{#snippet children({ Cancel })}
		<form onsubmit={submit}>
			<!-- The shared PIN control: `PinBox` owns the digit cells, the paste and the caret. -->
			<PinBox
				bind:element={pinInput}
				label="PIN"
				invalid={Boolean(error)}
				bind:value={pin}
				disabled={working}
			/>

			<!-- Announced when it appears: the person may already be reaching for the button. This is
			     the message under a field rather than the banner at the top of a screen, so it is the
			     form-field error and not the shared `Problem`. -->
			{#if error}
				<p class="error" role="alert">{error}</p>
			{/if}

			<div class="buttons">
				<!--
					`type="button"`, and it is load-bearing.

					A button in a form with no type IS a submit button, and the browser's implicit
					submission (pressing Enter in the field) activates the FIRST one in the form.
					That is this one. Without the type, typing a PIN and pressing Enter would press
					Cancel: the box would vanish and the request go out anyway, leaving a lone 401 in
					the console with nothing on screen to explain it. Pressing Show would work, so it
					would read as the app eating a wrong PIN rather than as the keyboard doing
					something different from the mouse.
				-->
				<Cancel type="button" class="cancel">Cancel</Cancel>
				<Button tone="primary" type="submit" disabled={working || pin.length === 0}>
					{working ? 'Checking\u2026' : 'Show'}
				</Button>
			</div>
		</form>
	{/snippet}
</Modal>

<!-- Its twin, for somebody with no PIN to type: drawn beside this one so the one prompt the shell
     mounts answers both questions. See `vaultPrompt.ask`. -->
<CreatePin />

<style>
	/*
	 * `.veil`, `.sheet`, `.title`, `.consequence`, `.cancel` and `.confirm` are dressed once in
	 * `app.css`: the same look, because this is the same kind of interruption and two dialogs that
	 * differ slightly read as two different apps. These rules stay plain and scoped even though
	 * `Modal` portals the sheet away: a snippet is compiled where it is written, so the form below
	 * is this file's markup wherever it ends up on the page.
	 *
	 * The PIN box's height and digit spacing are `PinBox`'s own, not this screen's: they are what a
	 * row of cells is.
	 */

	.error {
		margin: var(--space-2) 0 0;
		font: var(--text-body-sm);
		color: var(--sift-bad-text);
	}
</style>
