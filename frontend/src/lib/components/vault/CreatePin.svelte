<script lang="ts">
	/*
	 * NOT ON THE GALLERY: one of these exists, drawn by the shell's one PIN prompt, for the reason
	 * that prompt gives. What it is made of (the dialog, the field, the PIN cells) is on the gallery.
	 */

	/*
	 * Creating a PIN, over whatever screen somebody is on.
	 *
	 * Hidden is opened with the PIN and nothing else, so the first press on anything that shows or
	 * hides things, for somebody without one, has to end in a PIN. Asked here, in a dialog, rather
	 * than by sending them to a settings pane to find the form: the press was about Hidden, and the
	 * answer should arrive where they pressed.
	 *
	 * The same dialog asks for a six-digit PIN from somebody who has just opened Hidden with a
	 * shorter one kept from before the rule. That PIN goes on working, so this one can be put off.
	 *
	 * Their password as well as the PIN, because the server asks for it: a screen left signed in must
	 * not be enough to plant a PIN and take Hidden over.
	 */
	import { Button, Field, PinBox, TextInput } from '$lib/components/common';
	import { PIN_DIGITS } from '$lib/components/common/PinBox.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { vault, vaultPrompt } from '$lib/shell/vault.svelte';

	let password = $state('');
	let pin = $state('');
	let error = $state<string | null>(null);
	let working = $state(false);

	const why = $derived(vaultPrompt.creating);

	// Cleared on the way in: a password or a PIN left in a closed dialog is sitting in the page.
	$effect(() => {
		if (why !== null) {
			password = '';
			pin = '';
			error = null;
			working = false;
		}
	});

	const complete = $derived(password.length > 0 && pin.length === PIN_DIGITS);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (working || !complete) return;
		working = true;
		error = null;
		try {
			const failure = await vault.setPin(pin, password);
			if (failure === 'wrong-password') {
				error = "That password isn't correct.";
				password = '';
				return;
			}
			if (failure === 'not-a-pin') {
				error = `A PIN is ${PIN_DIGITS} digits.`;
				pin = '';
				return;
			}
		} catch {
			error = 'Sift could not be reached. Try again.';
			return;
		} finally {
			working = false;
		}
		vaultPrompt.creating = null;
		toasts.show('PIN saved', { tone: 'success' });
	}
</script>

<!-- Closing it by any route (Cancel, Escape, the veil) clears the reason it was open for. -->
<Modal
	open={why !== null}
	onOpenChange={(next) => {
		if (!next) vaultPrompt.creating = null;
	}}
	insistent
	title={why === 'longer' ? 'Choose a six-digit PIN' : 'Create a PIN'}
	description={why === 'longer'
		? 'A PIN is six digits. The one you just used is shorter, and it keeps working until you choose a new one.'
		: 'Hidden opens with a PIN and nothing else, so you need one before you can hide anything.'}
>
	{#snippet children({ Cancel })}
		<form class="create" onsubmit={submit}>
			<Field
				label="Your password"
				help="So that no one can change your PIN from a screen you left signed in."
			>
				{#snippet control({ id, describedBy })}
					<TextInput
						{id}
						{describedBy}
						type="password"
						autocomplete="current-password"
						bind:value={password}
						disabled={working}
					/>
				{/snippet}
			</Field>

			<Field label="New PIN" help="Six digits.">
				{#snippet control({ id, describedBy, invalid })}
					<PinBox {id} {describedBy} {invalid} bind:value={pin} disabled={working} />
				{/snippet}
			</Field>

			{#if error}
				<p class="error" role="alert">{error}</p>
			{/if}

			<div class="buttons">
				<!-- `type="button"`: the first button in a form with no type is what Enter presses. -->
				<Cancel type="button" class="cancel">{why === 'longer' ? 'Not now' : 'Cancel'}</Cancel>
				<Button tone="primary" type="submit" icon="save" disabled={working || !complete}>
					{working ? 'Saving\u2026' : 'Save PIN'}
				</Button>
			</div>
		</form>
	{/snippet}
</Modal>

<style>
	.create {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.error {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-bad-text);
	}
</style>
