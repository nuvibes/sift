<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'ActionBar',
		category: 'surface',
		role: 'the bar that floats over a screen while something is picked, carrying the verbs for the pick',
		basis: 'own',
		states: ['resting', 'with a count', 'with verbs']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	import Button from './Button.svelte';
	import Scroller from './Scroller.svelte';
	/* WHY NOT BITS-UI: the floating region, not the controls inside it. The verbs arrive as a snippet from
	   whoever opened the bar, and bits-ui's Toolbar owns its own items: roving focus belongs to
	   VerbButtons, which renders them, not to the box they sit in. */
	/*
	 * The bar that appears when things are picked, and says what will happen to them.
	 *
	 * It floats over the grid rather than pushing it down, because a bar that reflowed the layout
	 * would move the tile under the pointer at the moment somebody is clicking tiles.
	 *
	 * The count is not decoration. Every action offered here runs over several things at once, and
	 * the number is the only thing on screen that says how many, so it is stated here, and stated
	 * again in the confirm dialog, and the two come from the same place.
	 */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { arrive } from '$lib/shell/motion.svelte';
	import { MOST_AT_ONCE } from '$lib/grid/grid.svelte';
	import { phoneWidth } from './phone-width.svelte';

	interface Props {
		count: number;
		/**
		 * How many the whole question matches, when that is more than is on screen.
		 *
		 * The number at the foot of the page. Absent on a list that is never paged, where "all of
		 * them" and "all of the ones loaded" are the same set and an offer would be noise.
		 */
		total?: number;
		/**
		 * Pick every one of them, not just the loaded page.
		 *
		 * Asynchronous because it means reading the rest of the list, and the bar shows that it is
		 * working rather than appearing to have ignored the press. Absent means the offer is not
		 * made at all, which is what a list with nothing more to fetch hands over.
		 */
		onselectall?: () => Promise<void>;
		/** What the things are, singular. "file", "person". Pluralized here so callers do not each
		 * write their own `count === 1 ? ... : ...`. */
		noun?: string;
		/** The plural, where adding an "s" is wrong ("person", "people"). */
		plural?: string;
		onclear: () => void;
		/** The buttons. Given as a snippet so the bar has no opinion about what can be done. */
		actions: Snippet;
		/**
		 * The door at the end, holding the verbs the bar does not name. See `VerbMore`.
		 *
		 * Its own slot rather than the last thing in `actions`, because it must not be inside the
		 * strip that scrolls: the whole point of a bar that names a few verbs is that the way to the
		 * rest is always in the same place, and a door that can scroll out of sight is a door
		 * somebody has to find twice.
		 *
		 * Optional. A bar with one verb (agreeing to a page of names) has nothing left over, and
		 * `VerbMore` draws nothing when handed nothing either way.
		 */
		overflow?: Snippet;
	}

	let {
		count,
		noun = 'item',
		plural,
		total,
		onselectall,
		onclear,
		actions,
		overflow
	}: Props = $props();

	const many = $derived(plural ?? `${noun}s`);
	/* Grouped thousands, for the reason the pager gives about its own readout: this is a number
	   somebody reads rather than one they compute with, and "8709" scans as four digits of
	   something. It also has to match the offer beside it: "Select all 8,709" answered by "8709
	   selected" reads as two different numbers. */
	const label = $derived(`${count.toLocaleString()} ${count === 1 ? noun : many} selected`);

	/* Whether there is more of the question than has been picked.
	 *
	 * Offered here rather than as a control that is always on screen, which is the arrangement every
	 * list with a long tail arrives at: nothing has been picked, so there is nothing to say; one
	 * thing has been picked, so "and the other eight thousand" is the obvious next sentence.
	 *
	 * The number is the one the pager shows, handed in rather than counted here. A bar that worked
	 * it out from what is loaded would say "select all 48" on a library of eight thousand, which is
	 * the fault this exists to fix wearing a button.
	 */
	const more = $derived(total !== undefined && onselectall !== undefined && count < total);

	/* WHAT THE PRESS WILL ACTUALLY DO, which on a large library is not "all" of it.
	 *
	 * Selecting a whole query is capped (see `MOST_AT_ONCE`) and a button reading "Select all
	 * 84,000" that hands back a thousand is the exact fault the cap's own comment warns about: a
	 * short answer nobody was told about. So the offer states the ceiling wherever the query is
	 * bigger than it, and says "all" only where all is what it means. */
	const allLabel = $derived(
		(total ?? 0) > MOST_AT_ONCE
			? `Select ${MOST_AT_ONCE.toLocaleString()} of ${(total ?? 0).toLocaleString()}`
			: `Select all ${(total ?? 0).toLocaleString()}`
	);

	/* Reading a whole library's ids is several requests, so a second press while the first is in
	   flight would run them twice and the second answer would win. Disabled rather than queued. */
	let picking = $state(false);

	async function pickEverything(): Promise<void> {
		if (picking || !onselectall) return;
		picking = true;
		try {
			await onselectall();
		} finally {
			picking = false;
		}
	}
</script>

{#if count > 0}
	<!-- A region rather than a dialog: it does not trap focus and nothing is blocked while it is up.
	     Picking more things is the obvious next thing to do, and a bar that stole the keyboard to
	     announce itself would be in the way of exactly that. -->
	<!-- It rises from the bottom rather than appearing there. The bar is the answer to something
	     that just happened somewhere else on the screen (a tile was picked) and the travel is
	     what connects the two: it comes from off the edge, so it reads as arriving rather than as
	     the page having been rebuilt around the selection. -->
	<div
		class="bar"
		role="region"
		aria-label="Selection"
		transition:arrive={{ y: 16, pace: 'base', spring: true }}
	>
		<button class="clear" type="button" onclick={onclear} aria-label="Clear selection">
			<Icon name="close" />
		</button>

		<!-- Announced when the number changes, so somebody using a screen reader is told what the
		     sighted user can see at a glance. Polite: it waits its turn rather than cutting in. -->
		<span class="count" aria-live="polite">{label}</span>

		<!--
			The rest of the question, when there is more of it than has been picked, before the
			verbs and beside the count it extends. "8 selected" and "Select all 1,200" are two
			readings of the same number, and somebody who has just read the count is looking here;
			at the far end it would be a control about the selection among controls about the files,
			one press from Delete.

			The price is paid knowingly: the offer goes when the pick reaches the whole question and
			the verbs shift left by its width, once, at the moment somebody is looking at the offer
			they just pressed.
		-->
		{#if more}
			<span class="all">
				<Button tone="ghost" size="small" disabled={picking} onclick={pickEverything}>
					{picking ? 'Selecting\u2026' : allLabel}
				</Button>
			</span>
		{/if}

		<!-- At a phone's width the verbs are a row of their own under the count, four columns and the
		     door, and they do not scroll: `barShape` names four there, which is what fits. The door
		     is in the same row as the verbs, so all five are one set of equal columns. -->
		{#if phoneWidth.yes}
			<div class="line">
				<div class="actions columns">{@render actions()}</div>
				{#if overflow}<span class="more">{@render overflow()}</span>{/if}
			</div>
		{:else}
			<Scroller horizontal>
				<div class="actions">{@render actions()}</div>
			</Scroller>

			<!-- And the rest of the verbs, outside the strip that scrolls. See `overflow`. -->
			{#if overflow}<span class="more">{@render overflow()}</span>{/if}
		{/if}
	</div>
{/if}

<style>
	/*
	 * Centred on the screen, not on the window.
	 *
	 * `position: absolute` inside the screen, because the rail takes width off the left: `fixed` at
	 * 50% of the window would sit off-centre and, on a narrow window, run under the rail.
	 * Subtracting the rail's width would silently go wrong when the rail collapses; the container
	 * is the box, so it follows the rail, top bar and page frame with nothing to keep in step.
	 *
	 * It needs a positioned ancestor, and every screen that draws this has one. `Tooltip` and the
	 * menus stay window-level, correctly: they are positioned against what opened them, and a modal
	 * covers the rail on purpose.
	 */
	.bar {
		position: absolute;
		z-index: var(--z-bar);
		/* Above the page's footer, not across it.
		   `--frame-footer` is what the frame publishes when it draws one; a screen with no footer
		   publishes nothing and the fallback puts the bar where it would otherwise be. Without it
		   the bar covers a footer pager completely, so a wall with anything picked cannot be
		   paged: the buttons are visible, enabled and unclickable. */
		inset-block-end: calc(var(--frame-footer, 0px) + var(--space-4));
		inset-inline-start: 50%;
		translate: -50% 0;
		display: flex;
		align-items: center;
		gap: var(--space-3);
		max-inline-size: calc(100% - var(--space-8));
		padding: var(--space-2) var(--space-3);
		border-radius: var(--radius-xl);
		background: var(--sift-surface-3);
		box-shadow: var(--elev-3);
	}

	.count {
		font: var(--text-label);
		color: var(--sift-ink);
		white-space: nowrap;
	}

	/*
	 * The verbs, allowed to shrink and, at the last resort, to scroll.
	 *
	 * Without `min-inline-size: 0` a flex child refuses to go below its content's width, so the row
	 * would overflow the bar's ceiling and a narrow window would cut the last verb off at the edge
	 * of the screen.
	 */
	.actions {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/*
	 * The buttons callers put in the bar, dressed by the bar.
	 *
	 * Reached globally because they come in through a snippet, so they are this component's
	 * children in the tree and nobody else's: the reach is exactly the slot. Styled HERE rather
	 * than left to each caller, because the first caller that forgets gets the browser's own button
	 * in the middle of the app: a grey rectangle in the system typeface.
	 */
	.actions :global(button) {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		block-size: 32px;
		padding-inline: var(--space-3);
		border: 0;
		border-radius: var(--radius-md);
		background: var(--sift-surface-4);
		color: var(--sift-ink);
		font: var(--text-body-sm);
		font-weight: 500;
		white-space: nowrap;
		cursor: pointer;
		/* The Light register: the ground steps, over --dur-instant, and nothing
		   moves, rather than every button in every selection bar snapping. */
		transition: background var(--dur-instant) var(--ease);
	}

	.actions :global(button:hover:not(:disabled)) {
		background: var(--sift-surface-2);
	}

	/* A verb that is offered and cannot be answered just now: a request already in flight, or a
	   row that does not apply to what is picked. Without a rule a disabled button in this bar
	   would be indistinguishable from a live one and still light up under the cursor: it would
	   look exactly like a button that had stopped working. */
	.actions :global(button:disabled) {
		color: var(--sift-ink-3);
		cursor: default;
	}

	.actions :global(button:focus-visible) {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* The one that destroys something wears the colour that means so, and only that one. */
	.actions :global(button.destructive) {
		background: var(--destructive);
		color: var(--destructive-foreground);
	}

	.clear {
		display: grid;
		place-items: center;
		inline-size: 32px;
		block-size: 32px;
		border: 0;
		border-radius: var(--radius-md);
		background: transparent;
		color: var(--sift-ink-2);
		cursor: pointer;
	}

	.clear:hover {
		background: var(--sift-surface-4);
		color: var(--sift-ink);
	}

	.clear:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* Wrappers only, so the phone's grid below can place what is in them. On a wide window they are
	   nothing: `contents` hands their child straight to the bar's row. */
	.all,
	.more {
		display: contents;
	}

	/*
	 * AT A PHONE'S WIDTH: two lines, the whole width of the screen, never off its edge.
	 *
	 * The first line is about the selection (the way out, the count, the offer of the rest); the
	 * second is what can be done to it: four verbs and the door to the others, as five equal columns
	 * with the glyph over the word, the shape every phone's photo library draws, each a finger's
	 * height. `barShape` names four at this width, so the row has what fits and nothing scrolls.
	 *
	 * Above the corner player when it is docked over the tabs: `--mini-docked` is its height and
	 * gap, published by the player only while it is there, and nothing at all otherwise.
	 */
	@media (max-width: 767px) {
		.bar {
			inset-inline: var(--space-2);
			inset-block-end: calc(var(--frame-footer, 0px) + var(--space-2) + var(--mini-docked, 0px));
			translate: none;
			display: grid;
			grid-template-columns: auto minmax(0, 1fr) auto;
			grid-template-areas:
				'clear count all'
				'verbs verbs verbs';
			gap: var(--space-1) var(--space-2);
			max-inline-size: none;
			padding: var(--space-1) var(--space-2) var(--space-2);
		}

		.clear {
			grid-area: clear;
			inline-size: var(--touch-target);
			block-size: var(--touch-target);
		}

		.count {
			grid-area: count;
			overflow: hidden;
			text-overflow: ellipsis;
		}

		.all {
			display: flex;
			grid-area: all;
			align-items: center;
		}

		/*
		 * The second line: every verb and the door as ONE set of equal columns, whatever wraps the
		 * button in each (the rating's chooser and a group's door each put an element of their own
		 * around it). A grid track rather than a shared flex, because a flex item's share starts from
		 * its padding: a bare button would come out wider than the rating's wrapper beside it, and
		 * the door, in a track of its own width, a third width. The verbs' own wrapper
		 * draws no box (`contents`), so the door is a column of the same row as they are.
		 */
		.line {
			grid-area: verbs;
			display: grid;
			grid-auto-flow: column;
			grid-auto-columns: minmax(0, 1fr);
			gap: var(--space-2);
		}

		.line > .columns {
			display: contents;
		}

		.columns > :global(*),
		.line > .more {
			display: flex;
			min-inline-size: 0;
		}

		.columns :global(button),
		.more :global(button) {
			display: flex;
			flex: 1 1 0;
			flex-direction: column;
			align-items: center;
			justify-content: center;
			gap: var(--space-1);
			min-inline-size: 0;
			block-size: auto;
			min-block-size: calc(var(--touch-target) + var(--space-2));
			padding: var(--space-1);
			border: 0;
			border-radius: var(--radius-md);
			background: var(--sift-surface-4);
			color: var(--sift-ink);
			font: var(--text-label);
			text-align: center;
			white-space: normal;
			overflow-wrap: anywhere;
			cursor: pointer;
		}

		.more :global(button:focus-visible) {
			outline: none;
			box-shadow: var(--focus-ring);
		}
	}
</style>
