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

	/*
	 * A sentence with a symbol in front of it: something worth knowing, beside the thing it is
	 * about.
	 *
	 * A component so every aside has one glyph size, gap and ink, rather than a different weight on
	 * each screen.
	 *
	 * The symbol is always on the sentence's own row. A glyph stacked on a line above spends a
	 * whole line on 16 pixels and pushes the mark away from its sentence, and one arrangement keeps
	 * every aside alike. The left edge stays straight because the sentence is its own flex item and
	 * wraps inside it.
	 *
	 * The offset that puts the glyph on the text's own line is `(1lh - 16px) / 2`, the line box's
	 * height minus the icon's, halved, so it stays true if the small-text token changes; the plain
	 * value before it is for a browser too old for `lh`.
	 *
	 * Not `Problem`, and not an alert. `Problem` reports a failure and interrupts a screen reader;
	 * this is a standing fact that was true before anybody arrived, so it is read in its turn. An
	 * aside that interrupts trains people to ignore interruptions.
	 */
	import type { Snippet } from 'svelte';

	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** `info` is a fact worth knowing; `caution` is one worth reading twice. */
		tone?: NoteTone;
		/** A different glyph, where one says more than the tone's own. */
		icon?: IconName;
		/** The sentence. */
		children: Snippet;
	}

	let { tone = 'info', icon, children }: Props = $props();

	const glyph = $derived(icon ?? (tone === 'caution' ? 'warning' : 'info'));
</script>

<p class="note {tone}">
	<!-- Unlabelled on purpose: the sentence beside it says the same thing in words, so a label here
	     would have a screen reader read the meaning twice. -->
	<Icon name={glyph} size={16} />
	<span>{@render children()}</span>
</p>

<style>
	/* The mark beside the words, never above them, lifted onto the text's own baseline.
	   `flex-shrink` because a glyph that squashes is worse than one that wraps. */
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

	/* The caution's words stay in the ordinary quiet ink and only the MARK is coloured. A whole
	   sentence in warning amber reads as something having gone wrong, and nothing has: this is a
	   fact about the screen, not a report about it. */
	.caution {
		color: var(--sift-ink-2);
	}

	.caution :global(.icon) {
		color: var(--sift-warn);
	}
</style>
