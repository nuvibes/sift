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
	   closed, on the keyboard, in the accessibility tree, and found by find-in-page. bits-ui's
	   Collapsible would replace all of that with script. */

	/*
	 * A part of a pane folded away: every Site's own tunnel, the whole table of supported Sites.
	 *
	 * `MoreAbout` folds the second half of an explanation under words that never change. This folds
	 * a part of the pane that somebody opens on purpose, so its words say what is behind it and how
	 * much ("Show all 26 Sites"): the count is often the reason it is folded. One shape for every
	 * such fold, words that underline under a pointer and no box, so two on one pane cannot be
	 * drawn two ways.
	 *
	 * `section` is the other shape: a whole section of a screen that folds under its own band
	 * heading (who is in a file, what is similar to it, its record). The heading's words and the
	 * arrow saying which way pressing goes are ONE press, drawn as the Collapse control under the
	 * picture is: one ghost ground over both under a pointer, the arrow centred on the line, and
	 * the press centred ACROSS the row as that control is, with the heading's own controls at the
	 * row's end. A heading whose words lead onwards (`href`) is the exception: the words are a
	 * link, underlined at rest, and the arrow beside them is its own press; the words and their
	 * arrow are centred the same way, as one. The heading's own controls go with what they act on
	 * while it is shut. Not a `<details>`: a section's words can be a link
	 * onwards and its heading carries controls, and neither may sit inside a `<summary>`, which is
	 * itself a control. Open until somebody shuts it, since a section is part of the screen rather
	 * than something opened on purpose.
	 *
	 * `weight` is the section heading's weight: 600, every heading inside a page, unless a caller
	 * says 450. Only the popout player's sections under the picture (Enrichment, Who is in this,
	 * Similar to this, Same music, File info) say 450, and that is the Expand and Collapse control's
	 * own text: the same face, size and ink the band heading already wears (`--text-body-sm`,
	 * `--sift-ink-2`), at the weight that token carries, so the heading and the control beside the
	 * picture read as one line of the same text. The text only: the heading gains no ground and no ghost-button treatment of its own. A prop,
	 * not a rule in that screen, so the primitive keeps one heading weight everywhere else and the
	 * exception is named where it is used.
	 *
	 * `remember` keeps the last press in this browser under that key, as a band's open or shut
	 * does everywhere else: it is the arrangement of one screen, not a preference.
	 */
	import type { Snippet } from 'svelte';

	import Button from './Button.svelte';
	import SectionHeading from './SectionHeading.svelte';
	import Tooltip from './Tooltip.svelte';
	import { reveal } from '$lib/shell/motion.svelte';
	import { readStored, writeStored } from '$lib/shell/remembered.svelte';

	interface Props {
		/** What is behind it, counted where a count is why it is folded: "Show all 26 Sites". A
		 *  section's heading, in the section shape. */
		summary: string;
		/** An address, so a link to something inside can open it. */
		id?: string;
		/** A section of a screen under its band heading, open until shut. See above. */
		section?: boolean;
		/** The key this browser keeps the last press under. Unset, nothing is remembered. */
		remember?: string;
		/** Where a section heading's words lead, making them a link (`SectionHeading`'s). */
		href?: string;
		/** That link's tooltip. */
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
			<!-- The words lead onwards (a wall of every match), so they are a link, underlined at rest
			     so they read as one before anybody points at them, and the arrow beside them folds. -->
			<SectionHeading band {href} {tip} {onclick}>
				{summary}{#if tail}<span class="tail">{tail}</span>{/if}
				{#snippet actions()}
					<!-- Beside the words rather than among the controls: it belongs to the heading.
					     The button is named by the section it opens; `aria-expanded` says which way it is. -->
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
			<!-- The words and the arrow are ONE press, drawn the way the Collapse control under the
			     picture is: a ghost ground over both under a pointer, and the arrow a glyph of the
			     button centred on its line rather than riding at the words' cap height. -->
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
		/* Underlined only under a pointer, and the resting rule declares it clear rather than
		   absent, so the change is a step both ways. There is no ground to step. */
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

	/* A section spaces itself as a strip under its heading always has: the heading, then its body
	   one small gap under, and the things in the body that far apart too. Padding rather than
	   margin, so the slide that opens it carries the gap. */
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
	/* The heading's own press: the heading's words in the heading's face, with the ground a ghost
	   button steps in under a pointer. `:global` because the class is handed to `Button`. */
	.section :global(.fold-press) {
		font: inherit;
		color: inherit;
	}

	/* THE HEADING STANDS CENTRED ACROSS ITS ROW, as the Collapse control under the picture does:
	   two equal outer columns share what the heading leaves, so its middle is the row's middle
	   whatever stands at the end. The heading's own controls (File info's Save and Cancel) take
	   the last column, at the row's end; the press that opens them (Edit) the first. A heading that leads onwards has its arrow
	   in the column beside the words instead, so the words and the arrow are centred as one, the
	   way the press's words and arrow are. */
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

	/* A control that opens what the end of the row then holds (File info's Edit) stands at the
	   row's START, in the first column, so the heading stays centred between the two. */
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

	/* The section heading in the Expand control's text where its caller asked (`weight`): the
	   weight inside `--text-body-sm`, the token the control and the band heading are both set in,
	   so only the weight differs from the band's and it is the control's. The words, whether a
	   press or a link, take the heading's weight, so the one rule on the heading reaches both. The
	   number is written out because the band's 600 overrides the token's own; the test beside this
	   file holds it equal to the token's. */
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
