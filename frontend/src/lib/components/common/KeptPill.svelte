<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'KeptPill',
		category: 'primitive',
		role: 'a kept filter or layout drawn as a pill that previews what it holds',
		basis: 'own',
		states: ['default', 'applied', 'working']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no pill in the library, and there is nothing to build: this is the
	   app's `Pressable` for the press, `RowMenu` for the three dots, `ContextMenu` for the right-click
	   and `Tooltip` for the bubble, arranged. What it owns is the arrangement and the box. */

	/*
	 * SOMETHING SOMEBODY KEPT UNDER A NAME, as one object you can pick up whole.
	 *
	 * ## Two things are kept in Sift, and they are one control
	 *
	 * A saved FILTER and a saved WALL are each a name you press to put it on, a hover bubble drawing
	 * what it holds, a three-dot menu and the same rows on a right-click. A wall drawn as a text
	 * button with a cross beside it would have no preview, no rename, no right-click, and a delete
	 * with no confirmation one pixel from the thing that opens it.
	 *
	 * They are the same object. Both are a named piece of setting-up somebody keeps and reaches for
	 * later, and the questions are the same three: what IS this one, put it on, and change or remove
	 * it. So there is one control, and the only thing a caller supplies beyond the name and the verbs
	 * is what to DRAW in the bubble, which is the one place they genuinely differ: a filter draws
	 * its chips, a wall draws its shape.
	 *
	 * ## The bubble is the whole point of it
	 *
	 * A list of names says nothing. `Set 3`, `New two`, `beach`: somebody who kept eight of these
	 * cannot tell them apart six weeks later, and the only other way to find out is to put one on and see
	 * what happened to the screen. Pointing at one answers it without changing anything.
	 */
	import type { Snippet } from 'svelte';
	import type { IconName } from '$lib/design/icons';
	import Icon from '$lib/components/Icon.svelte';
	import ContextMenu from './ContextMenu.svelte';
	import RowMenu from './RowMenu.svelte';
	import Spinner from './Spinner.svelte';
	import Tooltip from './Tooltip.svelte';
	import VerbMenuItems from './VerbMenuItems.svelte';
	import Pressable from './Pressable.svelte';
	import type { Verb } from './verbs';
	import { stage } from '$lib/components/shell/stage.svelte';

	interface Props {
		/** The id of the thing kept. What the verbs are run against. */
		id: string;
		name: string;
		/** Put it on. The press, and the only thing the pill itself does. */
		onapply: () => void;
		/**
		 * What this one holds, drawn in the bubble.
		 *
		 * A snippet rather than a description, because the two callers draw genuinely different
		 * things and neither is expressible as text: a filter is a row of the bar's own chips, a wall
		 * is a picture of its shape with the chips of every cell under it.
		 */
		holds?: Snippet;
		/** What to say when there is nothing to draw. A bubble with nothing in it looks broken. */
		nothing?: string;
		/** Whether what `holds` draws is a picture rather than a row of chips. See `Tooltip.wide`. */
		wide?: boolean;
		/** Everything else that can be done to it: rename, update, delete. Drawn twice, listed once. */
		verbs?: Verb[];
		/**
		 * What KIND of thing this one is, as a glyph before the name.
		 *
		 * Optional and off by default, because one of the two callers wants it and the other does
		 * not. Saved FILTERS sit in a panel full of filters and wear the funnel, so a row of them
		 * reads as filters at a glance rather than as a row of unexplained words; a saved WALL is
		 * the only kind of thing on the screen that draws it, and a mark saying so would be saying
		 * the only thing everything there already says.
		 */
		icon?: IconName;
		/**
		 * A write against this one is in flight: saving it, or updating it from what is on screen.
		 *
		 * The pill says so because it is where the press happened: a verb row closes the menu the
		 * instant it is pressed, and a write can take several seconds, so without this nothing on
		 * screen says it started and a press that appears to do nothing is pressed again. The same
		 * answer `Button.busy` gives (the spinner in place of the glyph, and no second press while
		 * it turns), said by the object the write is about.
		 */
		busy?: boolean;
	}

	let {
		id,
		name,
		onapply,
		holds,
		nothing = '',
		verbs = [],
		wide = false,
		icon,
		busy = false
	}: Props = $props();
</script>

<!--
	The whole pill is the right-click target, and the rows in that menu are the rows in the three-dot
	one. That is a rule rather than a courtesy: two menus on one object that offer different things is
	how somebody comes to believe a verb does not exist.

	`triggerClass` because the wrapper the context menu puts round its trigger is a plain block, and
	round something laid out by its parent (a pill in a wrapping row), a block that does not know
	it should shrink-wrap stretches to the row.
-->
<!--
	`portalTo` IS WHAT MAKES EITHER MENU DRAW AT ALL WHILE THE SCREEN IS FILLED.

	These pills sit in the filter panel, the panel drops out of the bar, and the bar is inside
	`.screen-box`, so on a filled Theater wall a menu portalled to the end of the document is not
	drawn by the browser at all: it would open, take the press and show nothing. The same answer, from
	the same place, as the column chooser two components up (`FacetPanel`) and the cell's own menu:
	the box that fills the window while one is filled, and null the rest of the time, which is the
	ordinary answer and the reason portalling exists.
-->
<ContextMenu
	items={rightClick}
	label="More for {name}"
	triggerClass="kept-trigger"
	portalTo={stage.whatFillsTheWindow}
>
	<div class="kept">
		<!-- NO HEADING in the bubble. "What this keeps" over the chips would be a caption on a
		     picture that has already said it: the chips ARE what it keeps, and the words would be
		     the widest thing in the bubble. The label is only for the one case with nothing to draw. -->
		<Tooltip label={holds ? '' : nothing} placement="top" {wide}>
			{#snippet detail()}
				{@render holds?.()}
			{/snippet}
			<!-- Inside the press rather than beside it: the glyph is part of the name, so pointing at
			     it lights the same ground the word does and there is one target instead of two. -->
			<Pressable
				class="name"
				feedback="wash"
				radius="sm"
				disabled={busy}
				aria-busy={busy ? 'true' : undefined}
				onclick={onapply}
			>
				<!-- The spinner stands where the glyph stands, so the pill does not change width while
				     it turns: a row of pills that reflows under the pointer is a row where the next
				     press lands on the wrong one. A pill with no glyph of its own gains one for the
				     length of the write, which is the one place the box moves and is worth it: with
				     nothing turning there is nothing at all to say a write is running. -->
				{#if busy}
					<Spinner size={16} label="Saving {name}" />
				{:else if icon}
					<Icon name={icon} size={16} />
				{/if}
				{name}
			</Pressable>
		</Tooltip>
		{#if verbs.length > 0}
			<!-- NOT disabled while a write runs, and that is deliberate rather than an omission: the
			     right-click offers the same rows and cannot be closed off the same way, and two doors
			     onto one object offering different things is the fault this file's comment above
			     names. The press is what is refused (it is this control's act, the way a `Button`'s
			     is) and a second write against the same one is refused by the caller that knows a
			     write is in flight. -->
			<RowMenu {verbs} ids={[id]} label="More for {name}" portalTo={stage.whatFillsTheWindow} />
		{/if}
	</div>
</ContextMenu>

{#snippet rightClick()}
	<VerbMenuItems {verbs} ids={[id]} subjectId={id} />
{/snippet}

<style>
	/*
	 * A box and the three dots, as one object, and the box is the COLUMN HEADER's box.
	 *
	 * These sit directly under the five column headers in the filter panel, which are that panel's
	 * other "pick a whole thing" control, so they take the same corner, the same ground and the same
	 * height. Drawn as full pills on a lighter surface they read as a different KIND of object from
	 * everything above them, which they are not: a kept filter is picked up whole exactly as a column
	 * is chosen whole, and so is a kept wall.
	 */
	/* The context menu's own wrapper, which is a plain block until it is told otherwise. */
	:global(.kept-trigger) {
		display: inline-flex;
	}

	.kept {
		display: inline-flex;
		align-items: center;
		gap: 2px;
		min-block-size: 36px;
		padding-inline: var(--space-1);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--sift-surface-3);
		/* The change steps over `--dur-instant` rather than happening between frames. */
		transition: border-color var(--dur-instant) var(--ease);
	}

	.kept:hover {
		border-color: var(--sift-line-strong);
	}

	/* The name takes the press. `:global` because the class is handed to `Pressable`, which compiles
	   it in its own file: an unscoped rule here would match nothing at all, silently.

	   Padded to the text and no further: a `--space-3` lead would make the box of a short name
	   twice the width of the name. */
	.kept :global(.name) {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		padding: var(--space-1) var(--space-2);
		border-radius: var(--radius-sm);
		color: var(--sift-ink);
		font: var(--text-body-sm);
		white-space: nowrap;
	}

	/* The kind mark, one step back from the name: it says what sort of thing this is, and the name
	   is what is being read for. */
	.kept :global(.name .icon) {
		color: var(--sift-ink-3);
	}

	/* The three dots, one size down from the row menus they are borrowed from: this sits inside a
	   pill rather than at the end of a table row, and the button's own 2rem square would make the
	   box taller than the column headers beside it. */
	.kept :global(button.more) {
		inline-size: 1.5rem;
		block-size: 1.5rem;
	}
</style>
