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
	// The insistent flavour rather than the plain sheet, and the difference is not cosmetic: it
	// takes the role that says this interrupted on purpose. The veil, the layering and the
	// transition are `Modal`'s; what is here is the question itself.
	import type { Snippet } from 'svelte';
	import Modal from './Modal.svelte';

	interface Props {
		open?: boolean;
		/** What is about to happen, as a question. "Delete 12 files from disk?" */
		title: string;
		/**
		 * Required, and that is the point of it being a prop rather than a nicety.
		 *
		 * "Are you sure?" names nothing, so the only way to answer it is to already know what you
		 * asked for. People learn to click through a confirm that never tells them anything, and then
		 * the one that mattered gets clicked through too. Name the consequence and the count: "This
		 * removes them from the disk and cannot be undone."
		 */
		consequence: string;
		/** Say the verb. "Delete", never "OK": a button called OK is a button nobody read. */
		confirmLabel: string;
		/** Red and the whole weight of it. Off only when the action does not destroy anything. */
		destructive?: boolean;
		/**
		 * Anything the question needs beyond a sentence: a choice between two ways of doing it, a
		 * list of what is about to be affected. Drawn between the consequence and the buttons.
		 *
		 * Deliberately not a free-form body in place of `consequence`. The sentence stays required
		 * whatever else is here, so a dialog cannot become a form with no plain statement of what
		 * pressing the button will do.
		 */
		extra?: Snippet;
		/**
		 * Anything that belongs under the two buttons: an offer to stop asking, never part of the
		 * question. Above the buttons it would read as one more thing to answer before pressing.
		 */
		below?: Snippet;
		/**
		 * Whether the question can be answered yet.
		 *
		 * For a confirm whose `extra` asks for something before the verb makes sense: a destination
		 * to move to. Cancel is never disabled, so there is always a way out.
		 */
		confirmDisabled?: boolean;
		/**
		 * The way out, where "Cancel" would be read as the act itself. A dialog asking whether to
		 * cancel a download has two buttons that both start with the word, so its way out says
		 * "Keep going" instead. Everywhere else the one word is right and this is left unset.
		 */
		cancelLabel?: string;
		/**
		 * A class for the consequence sentence, for a sheet whose `extra` follows it closely.
		 *
		 * The sheet keeps `--space-6` under the sentence for a button row. A sheet that puts an
		 * answer row there (the delete question's "I understand") must not pull it up with a
		 * negative margin, which the scroll region clips. The space is the sentence's, so the
		 * sentence is what a sheet dresses: this reaches `Modal.descriptionClass`, the same door
		 * the hidden and sharing sheets use.
		 */
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
	/*
	 * The chrome every sheet shares (the veil, the sheet, its title, its two buttons) is in
	 * `app.css`, so no dialog's styling depends on whether a confirm is part of the same bundle.
	 * Only what is this component's own is here. It stays a plain scoped rule even though `Modal`
	 * portals the sheet away, because a snippet is compiled where it is written: the row below is
	 * this file's markup wherever it ends up.
	 */
</style>
