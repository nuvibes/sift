<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'EntityRow',
		category: 'composition',
		role: 'one kind of thing a file belongs to, as a glyph and a line of chips that can be opened out',
		basis: 'composes:Button,Tooltip',
		states: ['fits', 'overflows', 'opened out', 'with an adder']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no behaviour here for the library to own. It is a glyph, a line of
	chips the caller wrote, and one button; the overflow is measured with a ResizeObserver. */

	/* One kind of thing a file belongs to, as a row: its glyph, the caller's chips, and See all,
	   shown exactly when the run is measured wider than its box. */
	import type { Snippet } from 'svelte';
	import Button from './Button.svelte';
	import Tooltip from './Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** The kind's glyph, at the head of the row. */
		icon: IconName;
		/** What the kind is called, said by the glyph's tooltip and to a screen reader. */
		label: string;
		children: Snippet;
		/** A control at the row's end, outside the clipping line so it never disappears. */
		adder?: Snippet;
	}

	let { icon, label, children, adder }: Props = $props();

	/** Whether the row is opened out, showing every chip on as many lines as it takes. */
	let open = $state(false);

	/** The clipping box and the run of chips inside it. Both are measured; see the header. */
	let box = $state<HTMLElement | null>(null);
	let run = $state<HTMLElement | null>(null);

	/** Whether anything is hidden while the row is closed. See `measure`, which is the whole rule. */
	let overflows = $state(false);

	$effect(() => {
		const outer = box;
		const inner = run;
		if (!outer || !inner) return;
		/* Measured only while closed, so the word stays to close it again; a pixel of slack. */
		const measure = () => {
			if (open) return;
			overflows = inner.getBoundingClientRect().width > outer.clientWidth + 1;
		};
		measure();
		const watch = new ResizeObserver(measure);
		watch.observe(outer);
		watch.observe(inner);
		return () => watch.disconnect();
	});
</script>

<div class="entity-row">
	<!-- The kind's glyph as an image, its box told not to shrink and to stand a chip's height. -->

	<span class="kind">
		<Tooltip {label} placement="top">
			<span class="glyph" role="img" aria-label={label}>
				<Icon name={icon} size={20} />
			</span>
		</Tooltip>
	</span>

	<div class="line" class:open bind:this={box}>
		<div class="run" bind:this={run}>{@render children()}</div>
	</div>

	{#if adder}
		<div class="adder">{@render adder()}</div>
	{/if}

	<!-- The facet panel's quiet small button, so the two read as one kind. -->

	{#if overflows}
		<Button tone="quiet" size="small" class="see" onclick={() => (open = !open)}>
			{open ? 'See fewer' : 'See all'}
		</Button>
	{/if}
</div>

<style>
	/* Aligned to the start, so the glyph stays by the first line when open. */
	.entity-row {
		display: flex;
		align-items: flex-start;
		gap: var(--space-2);
	}

	/* A chip's height, so the glyph centres on the first line of chips. */
	.kind {
		display: flex;
		flex: none;
		align-items: center;
		block-size: var(--chip-height);
		color: var(--sift-ink-3);
	}

	/* A chip's height inside the tooltip's wrapper too; the glyph at 20, the largest that fits. */
	.glyph {
		display: flex;
		align-items: center;
		block-size: var(--chip-height);
	}

	/* min 0, so the box clips rather than pushing the button off. */
	.line {
		flex: 1 1 auto;
		min-inline-size: 0;
		overflow: hidden;
	}

	/* The run at its natural width, so it can be found wider than the box. */
	.run {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		inline-size: max-content;
	}

	/* Opened out: every chip, wrapping. */
	.line.open {
		overflow: visible;
	}

	.line.open .run {
		flex-wrap: wrap;
		inline-size: auto;
	}

	/* The adder on the first line, never squeezed, at a control's own height. */
	.adder {
		display: flex;
		flex: none;
		align-items: center;
	}

	/* Global: the class lands on Button's element. Pinned to the start, like the glyph. */
	.entity-row :global(.see) {
		flex: none;
		align-self: flex-start;
	}
</style>
