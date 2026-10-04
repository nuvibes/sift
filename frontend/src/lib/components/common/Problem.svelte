<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Problem',
		category: 'composition',
		role: 'the line a screen shows when something failed, announced once',
		basis: 'own',
		states: ['default', 'with a retry']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	/*
	 * WHY NOT BITS-UI: bits-ui has no alert primitive. This is a coloured block with role="alert";
	 * its behaviour is that one attribute, which the browser implements.
	 *
	 * Something failed, said once, the same way everywhere.
	 *
	 * The announcement travels with the styling and neither can be chosen per screen: it always
	 * interrupts. The same failure must not interrupt a screen-reader user on one screen and be
	 * silent on another. An error nobody is told about cannot be recovered from; one told twice is
	 * noise.
	 *
	 * It uses the text red, not the fill red meant for shapes, for words on a dark surface.
	 *
	 * Not the message under a form field: that belongs to its field, is tied to it by name so a
	 * screen reader reads them together, and is drawn by `Field`. A field has an error; a screen
	 * has a problem.
	 */
	interface Props {
		/**
		 * What went wrong, in a sentence, and what to do about it where there is something to do.
		 *
		 * Nothing is drawn when there is none, so a caller can hand this whatever its state holds
		 * without wrapping it in a condition.
		 */
		message?: string | null;
		/**
		 * Something to DO about it, after the sentence.
		 *
		 * For the case where the fix is a setting: the message can say what is wrong and this can be
		 * the link that goes there, rather than the message ending in the name of a pane and leaving
		 * the reader to go and find it. Drawn only alongside a message, because a way out of a
		 * problem nobody has been told about is not a problem report.
		 */
		action?: Snippet;
		/** So a field can name this line in its `aria-describedby`, when the failure is about that field. */
		id?: string;
	}

	let { message, action, id }: Props = $props();
</script>

{#if message}
	<p class="problem" role="alert" {id}>
		{message}{#if action}&nbsp;{@render action()}{/if}
	</p>
{/if}

<style>
	.problem {
		margin-block-end: var(--space-4);
		padding: var(--space-3) var(--space-4);
		border-radius: var(--radius-md);
		background: var(--sift-bad-bg);
		color: var(--sift-bad-text);
		font: var(--text-body-sm);
	}
</style>
