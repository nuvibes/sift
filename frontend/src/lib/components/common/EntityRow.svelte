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
	   chips the caller wrote, and one button. The only thing that behaves is the measurement of
	   whether the line overflows, and that is the site's own ResizeObserver rather than anything
	   a component library ships. */

	/*
	 * One kind, one row: the glyph that says which kind, the chips, and the way to see the rest.
	 *
	 * The file dialog names five kinds of thing a file belongs to (people, sites, collections,
	 * photo sets, tags), and five hand-written rows would be five answers to where the glyph sits
	 * and what happens with forty chips. It knows nothing about what a chip is: the caller renders
	 * them, because what a chip links to, what its cross does and what hovering opens are not facts
	 * about the row.
	 *
	 * An empty row is the caller's decision: a snippet is opaque, so this cannot tell "no chips"
	 * from chips that render nothing. The caller knows how many it has.
	 *
	 * "See all" is measured, never guessed. The chips sit on one line with the overflow hidden; the
	 * run is laid out at its natural width inside a box that clips it, and the word appears exactly
	 * when the run is wider than the box. A count would offer it over three chips that fit and hide
	 * it over four that do not. Both sides are observed: the box changes width with the window, and
	 * the run changes width when a chip is added or removed, which the box's own size would not
	 * report.
	 */
	import type { Snippet } from 'svelte';
	import Button from './Button.svelte';
	import Tooltip from './Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** The kind's glyph, at the head of the row. */
		icon: IconName;
		/**
		 * What the kind is CALLED: "People", "Tags".
		 *
		 * The glyph's whole name, said in a tooltip and to a screen reader. The row has no heading of
		 * its own: five words down the left of a dialog is a column of labels beside a column of
		 * chips, and the glyph is the thing the eye actually uses to tell one row from the next.
		 */
		label: string;
		/** The chips. Whatever kind of chip this row is about; this draws them in a line. */
		children: Snippet;
		/**
		 * A control at the end of the row: the tag picker's button, today.
		 *
		 * OUTSIDE the line that clips, on purpose. Put among the chips it would be the first thing
		 * pushed out of sight on a file with a lot of them, which is a control that disappears
		 * exactly when the row it belongs to is busiest.
		 */
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
		/*
		 * Measured only while the row is closed, and that is the whole of the behaviour. An open
		 * row wraps, so an honest measurement of it answers "this fits" and the word would remove
		 * itself the instant it was pressed, leaving no way back. Frozen, the answer stays the one
		 * true of the closed row, which is the question the word asks; it is re-measured the moment
		 * the row is put back. The markup asks `overflows` and nothing else.
		 *
		 * A pixel of slack: sub-pixel layout can measure a run that fits exactly a hundredth wider
		 * than its box.
		 */
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
	<!-- The kind, as its glyph. `role="img"` with the name on it, because a span holding a ligature
	     is text a screen reader would otherwise read out as the icon's own name.

	     The box around the tooltip is this file's, because the tooltip's own wrapper is compiled in
	     that file's scope: this is the element that can be told not to shrink and to stand the
	     height of one chip. -->
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

	<!--
		One question, asked once: is anything hidden while this row is closed? `measure` above is
		where the answer is frozen for as long as the row is open, and why.

		The same quiet, small button the facet panel's "View N more" wears, so the two read as one
		kind of footnote rather than as two controls that happen to do the same thing.
	-->
	{#if overflows}
		<Button tone="quiet" size="small" class="see" onclick={() => (open = !open)}>
			{open ? 'See fewer' : 'See all'}
		</Button>
	{/if}
</div>

<style>
	/*
	 * The glyph, the chips, and whatever is on the end. Aligned to the START rather than centred:
	 * opened out, the chips are several lines tall, and a glyph floating level with the middle of
	 * them reads as belonging to none of them. Closed, there is one line and the two are identical.
	 */
	.entity-row {
		display: flex;
		align-items: flex-start;
		gap: var(--space-2);
	}

	/*
	 * The glyph stands the height of ONE chip, so it is centred against the first line of them
	 * rather than against the top of the text. The chip's own token, because that is literally what
	 * it is being lined up with: if the chip height changes, this follows it.
	 */
	.kind {
		display: flex;
		flex: none;
		align-items: center;
		block-size: var(--chip-height);
		color: var(--sift-ink-3);
	}

	/*
	 * The glyph itself, inside whatever the tooltip wraps it in. A box rather than a line of text,
	 * so the ligature does not sit on a baseline with room reserved under it.
	 *
	 * It stands a chip's height, like `.kind` around it: `.kind` is the flex item and the tooltip's
	 * wrapper sits between the two, so a height on the outer box alone would leave this one shorter
	 * than the chips, and the leading mark is the only way to tell one kind's row from the next.
	 *
	 * The glyph inside is 20, the largest sanctioned icon size that fits the chip's height with the
	 * air a chip gives its own label. Icon sizes are a closed set with no 26, so the box takes the
	 * chip's height and the mark takes the size the scale has, matching every other mark on this
	 * screen.
	 */
	.glyph {
		display: flex;
		align-items: center;
		block-size: var(--chip-height);
	}

	/*
	 * The window the chips are seen through. `min-inline-size: 0` because a flex item's floor is its
	 * content, and without it the box would be as wide as every chip laid end to end and would push
	 * the button off the row instead of clipping.
	 */
	.line {
		flex: 1 1 auto;
		min-inline-size: 0;
		overflow: hidden;
	}

	/*
	 * The chips at their NATURAL width, which is the measurement the header describes: laid out as
	 * though there were room, inside a box that clips. `max-content` is what makes the run's own
	 * width mean "what this row needs": with `auto` it would be the width of the box and could
	 * never be found to be wider than it.
	 */
	.run {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		inline-size: max-content;
	}

	/* Opened out: every chip, on as many lines as it takes. The run goes back to filling the row so
	   there is something for it to wrap within. */
	.line.open {
		overflow: visible;
	}

	.line.open .run {
		flex-wrap: wrap;
		inline-size: auto;
	}

	/* The adder, on the first line of the row and never squeezed. Its own height is the control's,
	   not this file's: a control drawn at one height here and another everywhere else is exactly
	   what the size tokens exist to prevent. */
	.adder {
		display: flex;
		flex: none;
		align-items: center;
	}

	/* The class is handed to `Button`, so it lands on an element compiled in THAT file's scope and a
	   plain `.see` here would reach nothing. Pinned to the start for the same reason the glyph is:
	   while the row is open it must not ride down beside the last line of wrapped chips. */
	.entity-row :global(.see) {
		flex: none;
		align-self: flex-start;
	}
</style>
