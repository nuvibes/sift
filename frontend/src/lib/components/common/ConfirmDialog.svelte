<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ConfirmDialog',
		category: 'surface',
		role: 'a dialog that asks one question and offers one act and one way out',
		basis: 'composes:Modal',
		states: ['plain', 'destructive']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: the dialog behaviour IS bits-ui's: one layer down, in Modal, which this composes. Adding
	a second import here would be a second dialog implementation inside the first. */
	// The insistent flavour: it takes the role that says it interrupted on purpose.
	import type { Snippet } from 'svelte';
	import Modal from './Modal.svelte';

	interface Props {
		open?: boolean;
		/** What is about to happen, as a question. "Delete 12 files from disk?" */
		title: string;
		/** Required: the consequence and the count, never "Are you sure?". */
		consequence: string;
		/** Say the verb. "Delete", never "OK": a button called OK is a button nobody read. */
		confirmLabel: string;
		/** Red and the whole weight of it. Off only when the action does not destroy anything. */
		destructive?: boolean;
		/** More the question needs, between the consequence and the buttons; never in its place. */
		extra?: Snippet;
		/** Under the buttons: an offer to stop asking, not part of the question. */
		below?: Snippet;
		/** Whether the question can be answered yet; Cancel never disables. */
		confirmDisabled?: boolean;
		/** The way out, where "Cancel" would read as the act ("Keep going"). */
		cancelLabel?: string;
		/** A class for the consequence, for a sheet whose `extra` follows it closely. */
		consequenceClass?: string;
		onconfirm: () => void;
	}

	let {
		open = $bindable(false),
		title,
		consequence,
		confirmLabel,
		destructive = true,
		extra,
		below,
		confirmDisabled = false,
		cancelLabel,
		consequenceClass,
		onconfirm
	}: Props = $props();

	function confirm() {
		open = false;
		onconfirm();
	}
</script>

<Modal bind:open {title} description={consequence} descriptionClass={consequenceClass} insistent>
	{#snippet children({ Cancel, Act })}
		{#if extra}{@render extra()}{/if}

		<div class="buttons">
			<Cancel class="cancel">{cancelLabel ?? 'Cancel'}</Cancel>
			<Act
				class="confirm {destructive ? 'destructive' : ''}"
				disabled={confirmDisabled}
				onclick={confirm}
			>
				{confirmLabel}
			</Act>
		</div>

		{#if below}{@render below()}{/if}
	{/snippet}
</Modal>

<style>
	/* The shared sheet chrome is in app.css; only this file's own rules are here. */
</style>
