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
	 * a screen's failure, always announced, in the text red. A field's error is Field's.
	 */
	interface Props {
		/** What went wrong and what to do; nothing is drawn when absent. */
		message?: string | null;
		/** Something to do about it, such as a link to the setting; drawn only with a message. */
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
