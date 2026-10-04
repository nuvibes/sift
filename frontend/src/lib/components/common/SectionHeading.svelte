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
	   is `Separator`, and deciding which groups get one is a stylesheet's question, not a script's. */

	/*
	 * The section header: `--text-h2`, 17px, in the display face and full ink, the one heading a
	 * group of rows, facts or forms gets, wherever that group is.
	 *
	 * A component so every pane uses one size: a long pane is read by its headings, and a heading
	 * smaller than the rows it heads reads as one of them.
	 *
	 * Every group after the first on a page opens with a hairline, so the eye finds where one group
	 * ends. The first gets none, since it sits under the page's title. Which heading is first is a
	 * fact about the page, and groups are drawn conditionally and in whatever order a pane allows,
	 * so the stylesheet decides it: a heading shows its rule when a group comes anywhere before it
	 * on the same page, written as two selectors (an earlier sibling that is or holds a group, or
	 * such a sibling of one of the heading's ancestors; see the rule below).
	 *
	 * A group is a heading or a row. A page's first group often has no heading (a feature's switch
	 * and its rows, straight under the title), and a heading after it opens the second group all
	 * the same. A paragraph is not a group: the lede under the title belongs to the title.
	 *
	 * "The same page" is the nearest element marked `section-stack`, which a page frame writes on
	 * the element its groups sit inside; without that bound, a screen under a settings panel could
	 * put a rule over the panel's first group. Outside any stack no rule is drawn.
	 *
	 * The level is for the outline, not the look: a page's title is `h1`, its groups `h2`, and a
	 * heading over a block inside a group (the results from one stash-box under the search that
	 * found them) `h3`, so a screen reader hears the nesting. A subheading is drawn one step down
	 * the type scale, in the same face and ink, so a heading and its first subheading stacked
	 * one under the other read as a parent and its child rather than as two equal groups.
	 *
	 * ## The band
	 *
	 * `band` is the other heading a page has: the small one over a strip or a block INSIDE a page
	 * (who is in this file, what looks like it, one list of Insights, a card on Organize). One face
	 * and one ink for every one of them, quieter than a group's heading and never mistaken for the
	 * help under a row. A band is not a group boundary, so it draws no hairline and does not count
	 * as a heading before the next group's.
	 *
	 * ## A heading that is a way onwards
	 *
	 * `href` makes the heading's words a link (a strip's heading that opens the whole wall it
	 * samples). It reads as the heading first: the same ink and weight at rest, the underline on
	 * hover and on keyboard focus. One rule here, so every strip that links its heading wears it.
	 */
	import type { Snippet } from 'svelte';

	import Separator from './Separator.svelte';
	import Tooltip from './Tooltip.svelte';
	import PathCopy from '$lib/settings-ui/PathCopy.svelte';

	interface Props {
		/** The heading's words. */
		children: Snippet;
		/**
		 * The anchor a settings search result or a deep link lands on. On the HEADING element, so the
		 * ring a search draws is one band on one line rather than a wash over the whole group.
		 */
		id?: string;
		/** `2` for a group of a page, `3` for a block inside a group. See above: the outline only. */
		level?: 2 | 3;
		/**
		 * A control that belongs to the heading rather than to any row under it: copying the facts
		 * the group lists, say. Drawn on the heading's own line, at its end.
		 */
		actions?: Snippet;
		/**
		 * A control drawn at the START of the heading's line, before the words: a press that opens
		 * what the heading's end then holds the way out of (File info's Edit, whose Cancel and Save
		 * stand at the end).
		 */
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
	<!-- `heading-line`, not `line`: the class is in the caller's document too, and a card that
	     names its own `.line` must not find this one first. -->
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

	/* The contract's section header. Full ink, never the accent: accent on words means a link, and
	   it would compete with the one lit row that says where you are. */
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

	/* A subheading: the next rung down the scale, still the display face and still semibold, so it
	   heads its block without matching the group heading above it. */
	h3 {
		font: var(--text-h3);
	}

	/* A band sits in the layout of the block it heads, which owns the distance between the two. */
	.section-heading.band {
		margin-block-end: 0;
	}

	/* One face and one ink for every heading inside a page: semibold, one step quieter than full
	   ink, the size of the help under a row but never its colour. */
	.band h2,
	.band h3 {
		font: var(--text-body-sm);
		font-weight: 600;
		letter-spacing: normal;
		color: var(--sift-ink-2);
	}

	/* A heading that is a link reads as the heading first: its ink and weight at rest, the
	   underline on hover and on keyboard focus. */
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

	/* No rule unless the page says this is not its first group. See the rule below. When it is
	   drawn it sits in the air between the two groups, closer to the heading it opens. The space
	   above it is a margin so that, in a page laid out as ordinary blocks, it COLLAPSES with the
	   space the group before already leaves rather than adding to it. */
	.section-heading :global(.separator.section-rule) {
		display: none;
		margin-block: var(--space-6) var(--space-4);
	}

	/*
	 * The rule, on every heading that has a group before it on the same page.
	 *
	 * "Before it" in document order, which a selector reaches in two ways:
	 *
	 *   1. a SIBLING that is a group or holds one, ahead of this heading:  S ~ .section-heading
	 *   2. the same, ahead of one of this heading's ancestors:             S ~ * .section-heading
	 *
	 * where S is a heading, a row (`.ruled-row`, which every row primitive carries), or an element
	 * holding either, inside the page's `.section-stack`. The stack bounds both, so an earlier group
	 * on some other page in the document never counts; the tail is this file's own element, so the
	 * rule reaches nothing but these headings.
	 */
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

	/*
	 * Never between a heading and its own first subheading. A heading followed at once by another
	 * (beside it, or first inside the block after it) is a parent over its first group, not a group
	 * that ended: a line there reads as an empty group under the parent's name.
	 */
	:global(.section-stack .section-heading:not(.band))
		+ .section-heading
		:global(.separator.section-rule),
	:global(.section-stack .section-heading:not(.band) + *)
		> .section-heading:first-child
		:global(.separator.section-rule) {
		display: none;
	}
	/* The marker the copy press stands against: PathCopy places itself in the gutter of whatever
	   carries this class, so the class itself is what makes that place. */
	.path-host {
		position: relative;
	}
</style>
