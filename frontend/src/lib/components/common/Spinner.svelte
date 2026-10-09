<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Spinner',
		category: 'primitive',
		role: 'a turning arc and a word for a screen reader, while something is in flight',
		basis: 'own',
		states: ['sm', 'md', 'lg']
	} satisfies DesignEntry;

	/** 12 inside a chip or badge, 16 beside a word, 20 alone, 28 for a panel. */
	export type SpinnerSize = 10 | 12 | 16 | 20 | 28;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no spinner in it and there will not be. A spinner has no focus, no
	keyboard and no state: a rotating arc and a live-region word. */

	/* Work with no shape, worth watching; a Skeleton is for a known shape on its way. An arc in
	   currentColor, so it reads at 12px and takes the colour of what it sits in. */
	interface Props {
		size?: SpinnerSize;
		/** What is being waited for, for a spinner with no words beside it. */
		label?: string;
	}

	let { size = 16, label }: Props = $props();
</script>

<span
	class="spinner size-{size}"
	role={label ? 'status' : undefined}
	aria-label={label}
	aria-hidden={label ? undefined : 'true'}
></span>

<style>
	/* One side drawn, so an arc turns; `currentColor` fits it to any button or badge. */
	.spinner {
		inline-size: var(--spinner-size);
		block-size: var(--spinner-size);
		border-width: var(--spinner-border);
		display: inline-block;
		flex: none;
		/* The size includes the border, so a 14px arc matches a 16px glyph's ink. */
		box-sizing: border-box;
		border-radius: var(--radius-full);
		border-style: solid;
		border-color: currentColor;
		border-inline-end-color: transparent;
		border-block-end-color: transparent;
		opacity: 0.9;
		animation: turn var(--dur-loop) linear infinite;
		vertical-align: -0.125em;
	}

	/* A heavier arc as it grows; each rung sets two numbers. The smallest sits inside a badge. */
	.size-10 {
		--spinner-size: 10px;
		--spinner-border: 1.5px;
	}

	.size-12 {
		--spinner-size: 12px;
		--spinner-border: 2px;
	}

	.size-16 {
		--spinner-size: 16px;
		--spinner-border: 2.5px;
	}

	.size-20 {
		--spinner-size: 20px;
		--spinner-border: 3px;
	}

	.size-28 {
		--spinner-size: 28px;
		--spinner-border: 4px;
	}

	/* Reduced motion stills it as a whole faint ring: a still arc reads as a fault. */
	:global(:root[data-motion='reduce']) .spinner {
		animation: none;
		border-color: currentColor;
		opacity: 0.5;
	}
</style>
