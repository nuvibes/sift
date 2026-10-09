<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Note',
		category: 'composition',
		role: 'a standing fact about a screen, said with a symbol in front of it',
		basis: 'own',
		states: ['info', 'caution']
	} satisfies DesignEntry;

	/** What kind of aside this is, which decides the glyph and the ink. */
	export type NoteTone = 'info' | 'caution';
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: bits-ui ships behaviour, and an aside has none: no focus, no keyboard, no
	   state. It is a glyph, a sentence and two tokens. */

	/* A sentence with a symbol on its own row: a standing fact, read in its turn, unlike Problem.
	 * The glyph's offset is (1lh - 16px) / 2, after a plain fallback. */
	import type { Snippet } from 'svelte';

	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** `info` is a fact worth knowing; `caution` is one worth reading twice. */
		tone?: NoteTone;
		/** A different glyph, where one says more than the tone's own. */
		icon?: IconName;
		children: Snippet;
	}

	let { tone = 'info', icon, children }: Props = $props();

	const glyph = $derived(icon ?? (tone === 'caution' ? 'warning' : 'info'));
</script>

<p class="note {tone}">
	<!-- Unlabelled: the sentence says it. -->
	<Icon name={glyph} size={16} />
	<span>{@render children()}</span>
</p>

<style>
	/* Beside the words, never above; it never shrinks. */
	.note {
		display: flex;
		flex-direction: row;
		align-items: flex-start;
		gap: var(--space-2);
		margin: 0;
		font: var(--text-body-sm);
		max-inline-size: 60ch;
	}

	.note :global(.icon) {
		flex-shrink: 0;
		margin-block-start: 0.125rem;
		margin-block-start: calc((1lh - 16px) / 2);
	}

	.info {
		color: var(--sift-ink-3);
	}

	/* Only the mark is coloured: amber words would read as something gone wrong. */
	.caution {
		color: var(--sift-ink-2);
	}

	.caution :global(.icon) {
		color: var(--sift-warn);
	}
</style>
