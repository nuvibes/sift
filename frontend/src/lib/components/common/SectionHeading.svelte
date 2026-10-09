<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'SectionHeading',
		category: 'primitive',
		role: 'the heading over one group of a page, with a hairline above every group after the first',
		basis: 'composes:Separator',
		states: [
			'first of its page',
			'after another group',
			'with actions beside it',
			'band',
			'a link onwards'
		]
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is nothing to behave. A heading is the site's own element, a hairline
	is `Separator`, and which groups get one is the stylesheet's. */

	/* The section header (--text-h2, display face, full ink) every group gets. Every group after the
	 * first on a page (`section-stack`) opens with a hairline, decided by the stylesheet. `level`
	 * is for the outline only. `band` is the quieter heading over a block inside a page, with no
	 * hairline; `href` makes the words a link that still reads as the heading. */
	import type { Snippet } from 'svelte';

	import Separator from './Separator.svelte';
	import Tooltip from './Tooltip.svelte';
	import PathCopy from '$lib/settings-ui/PathCopy.svelte';

	interface Props {
		children: Snippet;
		/** The anchor a search or deep link lands on, on the heading so the ring is one line. */
		id?: string;
		/** `2` for a group of a page, `3` for a block inside a group. See above: the outline only. */
		level?: 2 | 3;
		/** A control belonging to the heading, at the end of its line. */
		actions?: Snippet;
		/** A control at the start of the heading's line (File info's Edit). */
		leading?: Snippet;
		/** The heading over a strip or a block inside a page, rather than over a group. See above. */
		band?: boolean;
		/** Where the heading's words lead, making them a link. See above. */
		href?: string;
		/** Run on a press of that link, before it is followed. */
		onclick?: (event: MouseEvent) => void;
		/** The link's tooltip, where the heading has more to say than its words. */
		tip?: string;
	}

	let { children, id, level, actions, leading, band = false, href, onclick, tip }: Props = $props();
	const outline = $derived(level ?? (band ? 3 : 2));

	/* The heading's words as drawn, for the settings path its copy button puts on the clipboard. */
	let words = $state<HTMLElement | null>(null);
	const said = () => words?.textContent?.trim() ?? '';
</script>

{#snippet link()}
	<a class="heading-link" {href} {onclick}>{@render children()}</a>
{/snippet}

{#snippet wording()}
	{#if href && tip}
		<Tooltip label={tip}>{@render link()}</Tooltip>
	{:else if href}
		{@render link()}
	{:else}
		{@render children()}
	{/if}
{/snippet}

<div class="section-heading" class:band>
	{#if !band}<Separator class="section-rule" />{/if}
	<!-- `heading-line`, so a caller's own `.line` is not found first. -->
	<div class="heading-line path-host">
		<!-- Inside Settings only: elsewhere there is no settings path and it draws nothing. -->
		<PathCopy name={said} heading />
		{#if leading}
			<span class="leading">{@render leading()}</span>
		{/if}
		{#if outline === 3}
			<h3 {id} bind:this={words}>{@render wording()}</h3>
		{:else}
			<h2 {id} bind:this={words}>{@render wording()}</h2>
		{/if}
		{#if actions}
			<span class="actions">{@render actions()}</span>
		{/if}
	</div>
</div>

<style>
	.section-heading {
		margin-block-end: var(--space-2);
	}

	.heading-line {
		position: relative;
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* Full ink, never the accent, which means a link. */
	h2,
	h3 {
		margin: 0;
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
		color: var(--sift-ink);
		text-wrap: balance;
		/* A name in a heading can be one unbroken token; it breaks rather than leaving the box. */
		overflow-wrap: anywhere;
	}

	/* A subheading, one rung down, so it heads its block without matching the group's. */
	h3 {
		font: var(--text-h3);
	}

	/* A band sits in the layout of the block it heads, which owns the distance between the two. */
	.section-heading.band {
		margin-block-end: 0;
	}

	/* One quiet ink for every band, never the help's colour. */
	.band h2,
	.band h3 {
		font: var(--text-body-sm);
		font-weight: 600;
		letter-spacing: normal;
		color: var(--sift-ink-2);
	}

	.heading-link {
		color: inherit;
		text-decoration: none;
	}

	.heading-link:hover,
	.heading-link:focus-visible {
		text-decoration: underline;
	}

	/* A finger's reach on a phone, without the heading growing: the ring `Pressable` draws. */
	@media (max-width: 767px) {
		.heading-link {
			position: relative;
		}

		.heading-link::after {
			content: '';
			position: absolute;
			inset-block: min(0px, calc((100% - var(--touch-target)) / 2));
			inset-inline: min(0px, calc((100% - var(--touch-target)) / 2));
		}
	}

	.actions,
	.leading {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* No rule unless a group comes before; its top margin collapses with the group above. */
	.section-heading :global(.separator.section-rule) {
		display: none;
		margin-block: var(--space-6) var(--space-4);
	}

	/* The rule on a heading with a group before it in the same `.section-stack`: an earlier
	 * sibling that is or holds a group, or such a sibling of an ancestor. */
	:global(
			.section-stack
				:is(.section-heading:not(.band), .ruled-row, :has(.section-heading:not(.band), .ruled-row))
		)
		~ .section-heading
		:global(.separator.section-rule),
	:global(
			.section-stack
				:is(.section-heading:not(.band), .ruled-row, :has(.section-heading:not(.band), .ruled-row))
				~ *
		)
		.section-heading
		:global(.separator.section-rule) {
		display: block;
	}

	/* Never between a heading and its own first subheading. */
	:global(.section-stack .section-heading:not(.band))
		+ .section-heading
		:global(.separator.section-rule),
	:global(.section-stack .section-heading:not(.band) + *)
		> .section-heading:first-child
		:global(.separator.section-rule) {
		display: none;
	}
	/* PathCopy places itself in the gutter of whatever carries this class. */
	.path-host {
		position: relative;
	}
</style>
