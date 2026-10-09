<script lang="ts">
	import { Button, PinBox } from '$lib/components/common';
	/*
	 * NOT ON THE GALLERY: one of these exists, in the layout, for the whole application, for the
	 * same reason as the app's one toast host and its settings panel. The layout owns it so any
	 * screen can raise it (an Unlock button on a "some of these are in your vault" message,
	 * for one). A second instance on the gallery would be a second listener to `vaultPrompt`, and a
	 * PIN box opening because somebody scrolled past a design page is the wrong kind of surprise.
	 */

	/*
	 * The PIN box: asked every time, even with the vault open; insistent, since it asks for a
	 * credential.
	 */
	import Modal from '$lib/components/common/Modal.svelte';
	import CreatePin from './CreatePin.svelte';
	import { motion } from '$lib/shell/motion.svelte';
	import { vault, type UnlockFailure } from '$lib/shell/vault.svelte';

	interface Props {
		open?: boolean;
		onunlocked?: () => void;
		/** The act waiting on the PIN ("Put it back"), said as the title (`vaultPrompt.opened`). */
		reason?: string | null;
	}

	let { open = $bindable(false), onunlocked, reason = null }: Props = $props();

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

	/* A refusal shakes the box, from script so every refusal shakes; still under reduced motion. */
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

	// Cleared on the way in, so no PIN sits on screen.
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
			// Not a refusal: without this the box stays disabled with no message.
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

<!-- `onOpenAutoFocus` puts focus in the field; `bind:sheet` is for the shake. -->
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
			<PinBox
				bind:element={pinInput}
				label="PIN"
				invalid={Boolean(error)}
				bind:value={pin}
				disabled={working}
			/>

			<!-- Announced as it appears: a form-field error, not the shared `Problem`. -->
			{#if error}
				<p class="error" role="alert">{error}</p>
			{/if}

			<div class="buttons">
				<!--
				`type="button"`, or Enter in the field would press Cancel, the form's first button.
				-->
				<Cancel type="button" class="cancel">Cancel</Cancel>
				<Button tone="primary" type="submit" disabled={working || pin.length === 0}>
					{working ? 'Checking\u2026' : 'Show'}
				</Button>
			</div>
		</form>
	{/snippet}
</Modal>

<!-- Its twin for somebody with no PIN (`vaultPrompt.ask`). -->
<CreatePin />

<style>
	/* The dialog's own classes are dressed once in `app.css`; the PIN cells are `PinBox`'s. */

	.error {
		margin: var(--space-2) 0 0;
		font: var(--text-body-sm);
		color: var(--sift-bad-text);
	}
</style>
