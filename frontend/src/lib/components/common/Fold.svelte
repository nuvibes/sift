<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Fold',
		category: 'composition',
		role: 'a part of a pane folded away under words that say what is behind them',
		basis: 'site:<details>; composes:SectionHeading,Button,Tooltip',
		states: [
			'shut',
			'open',
			'a section, open',
			'a section, shut',
			'a section leading onwards',
			'a section, its heading at weight 450'
		]
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: `<details>` is the site's disclosure and it is already right: open or
	closed, on the keyboard, in the accessibility tree and found by find-in-page. */

	/* A part of a pane folded away, its words saying what and how many ("Show all 26 Sites"). `section`
	 * folds a screen's section under its band heading, centred across its row with its controls at
	 * the
	 * end; `weight` 450 matches the popout's Collapse control; `remember` keeps the last press. */
	import type { Snippet } from 'svelte';

	import Button from './Button.svelte';
	import SectionHeading from './SectionHeading.svelte';
	import Tooltip from './Tooltip.svelte';
	import { reveal } from '$lib/shell/motion.svelte';
	import { readStored, writeStored } from '$lib/shell/remembered.svelte';

	interface Props {
		/** What is behind it, counted where that is why it is folded; a section's heading. */
		summary: string;
		/** An address, so a link to something inside can open it. */
		id?: string;
		/** A section of a screen under its band heading, open until shut. See above. */
		section?: boolean;
		/** The key this browser keeps the last press under. Unset, nothing is remembered. */
		remember?: string;
		/** Where a section heading's words lead, making them a link (`SectionHeading`'s). */
		href?: string;
		tip?: string;
		/** A section heading's tail: the count, in the quieter ink, after the words. */
		tail?: string;
		/** A section heading's own press, passed to the heading (the words are a link). */
		onclick?: (event: MouseEvent) => void;
		/** Where a section starts when this browser remembers nothing: open unless said otherwise. */
		open?: boolean;
		/** A section heading's own controls, drawn beside the arrow while the section is open. */
		actions?: Snippet;
		/** A section heading's control at the START of its row, drawn while the section is open. */
		leading?: Snippet;
		/** Told of every press, after the fold has turned. */
		ontoggle?: (open: boolean) => void;
		/** A section heading's weight: 600 unless a caller says 450. See above. */
		weight?: 450 | 600;
		children: Snippet;
	}

	let {
		summary,
		id,
		section = false,
		remember,
		href,
		tip,
		tail,
		onclick,
		open: startsOpen = section,
		actions: more,
		leading: lead,
		ontoggle,
		weight = 600,
		children
	}: Props = $props();

	/** Where it starts: the last press this browser kept, else the shape's own default. */
	function opening(): boolean {
		const kept = remember ? readStored(remember) : null;
		return kept === null ? startsOpen : kept === 'yes';
	}

	let open = $state(opening());

	/** The id the arrow points at, so what it opens is said rather than merely adjacent. */
	const uid = $props.id();
	const inside = `fold-${uid}`;

	function turn(next: boolean) {
		open = next;
		if (remember) writeStored(remember, next ? 'yes' : 'no');
		ontoggle?.(next);
	}
</script>

{#if section}
	<div
		class="fold section"
		class:onwards={href !== undefined}
		class:beside-control={weight === 450}
		{id}
	>
		{#if href}
			<!-- Words leading onwards are a link; the arrow beside them folds. -->
			<SectionHeading band {href} {tip} {onclick}>
				{summary}{#if tail}<span class="tail">{tail}</span>{/if}
				{#snippet actions()}
					<!-- Named by its section; `aria-expanded` says which way. -->
					<Tooltip label={open ? 'Collapse' : 'Expand'}>
						<Button
							tone="ghost"
							size="small"
							icon={open ? 'expand_less' : 'expand_more'}
							aria-label={summary}
							aria-expanded={open}
							aria-controls={inside}
							onclick={() => turn(!open)}
						/>
					</Tooltip>
					{#if open && more}{@render more()}{/if}
				{/snippet}
			</SectionHeading>
		{:else}
			<!-- Words and arrow as one press, as the Collapse control under the picture. -->
			<SectionHeading band leading={open ? lead : undefined}>
				<Button
					tone="ghost"
					size="small"
					class="fold-press"
					trailing={open ? 'expand_less' : 'expand_more'}
					aria-expanded={open}
					aria-controls={inside}
					onclick={() => turn(!open)}
				>
					{summary}{#if tail}<span class="tail">{tail}</span>{/if}
				</Button>
				{#snippet actions()}
					{#if open && more}{@render more()}{/if}
				{/snippet}
			</SectionHeading>
		{/if}
		{#if open}
			<div class="inside" id={inside} transition:reveal>{@render children()}</div>
		{/if}
	</div>
{:else}
	<details class="fold" {id} bind:open>
		<!-- Turned here rather than by the browser, so a press is the one thing remembered. -->
		<summary
			onclick={(event) => {
				event.preventDefault();
				turn(!open);
			}}>{summary}</summary
		>
		<div class="inside">{@render children()}</div>
	</details>
{/if}

<style>
	.fold {
		margin-block: var(--space-3);
	}

	.fold > summary {
		inline-size: fit-content;
		cursor: pointer;
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
		font-weight: 600;
		border-radius: var(--radius-sm);
		text-decoration: underline;
		/* Underlined only under a pointer, stepping both ways. */
		text-decoration-color: transparent;
		text-underline-offset: 3px;
		transition:
			color var(--dur-instant) var(--ease),
			text-decoration-color var(--dur-instant) var(--ease);
	}

	.fold > summary:hover {
		color: var(--hover-ink);
		text-decoration-color: currentColor;
	}

	.fold > summary:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* What is behind the words stands one gap under them; it is drawn only while open. */
	.inside {
		margin-block-start: var(--space-3);
	}

	/* Padding, so the slide that opens it carries the gap. */
	.fold.section {
		margin-block: 0;
	}

	.section > .inside {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin-block-start: 0;
		padding-block-start: var(--space-2);
	}
	/* Global: the class is handed to Button. */
	.section :global(.fold-press) {
		font: inherit;
		color: inherit;
	}

	/*
	 * Centred across its row by two equal outer columns; its controls at the end, Edit at the
	 * start.
	 */
	.section :global(.heading-line) {
		display: grid;
		grid-template-columns: 1fr auto auto 1fr;
		column-gap: 0;
	}

	.section :global(.heading-line > h3) {
		grid-row: 1;
		grid-column: 2;
	}

	.section :global(.heading-line > .actions) {
		grid-row: 1;
		grid-column: 4;
		justify-self: end;
	}

	/* A leading control in the first column. */
	.section :global(.heading-line > .leading) {
		grid-row: 1;
		grid-column: 1;
		justify-self: start;
	}
	.section.onwards :global(.heading-line > .actions) {
		grid-column: 3;
		justify-self: start;
		margin-inline-start: var(--space-2);
	}

	/* The asked-for weight; the test beside this file holds 450 equal to the token's. */
	.section.beside-control :global(.heading-line > h3) {
		font-weight: 450;
	}

	/* A heading that leads onwards reads as a link at rest. */
	.section :global(.heading-link) {
		text-decoration: underline;
		text-underline-offset: 3px;
	}

	/* A section heading's tail (a count), in the quieter ink so the words read first. */
	.section :global(.tail) {
		margin-inline-start: var(--space-2);
		font-weight: 400;
		color: var(--sift-ink-3);
	}
</style>
