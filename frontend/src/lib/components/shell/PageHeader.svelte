<script lang="ts">
	/*
	 * The row at the top of a screen: what this page is on the left, what it can do on the right.
	 * One component, so the title and the controls sit the same on every screen.
	 */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { counted, withSize } from '$lib/entity/entity-counts';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		/** The screen's name, as the rail writes it. */
		title: string;
		/** The rail's glyph for this destination, the same name. Absent on a section of a page. */
		icon?: IconName;
		/** `2` under something that has already named the page, so a document has one `h1`. */
		level?: 1 | 2;
		/** How many things are on the screen. Scoped by the server to what this viewer may see. */
		count?: number;
		/**
		 * How big the files `count` counts are, in bytes, said beside it ("12,000 \u00b7 1.1 TB").
		 * Only for a count of FILES, off the same answer as the count; null or absent says no size.
		 */
		bytes?: number | null;
		/** A control belonging to the TITLE, drawn right after the name (Browse's folder button). */
		beside?: Snippet;
		/** Hide the title and the count where the row of tabs is the heading; clipped, not removed. */
		titleHidden?: boolean;
		/** A word about what the screen is DOING, beside the title. Not what it contains. */
		status?: Snippet;
		/** This page's own controls: sort, size, filters, whatever it has. */
		controls?: Snippet;
		/** A sentence under the title, for a screen whose whole idea needs one. */
		lede?: Snippet;
		/**
		 * True on a screen that reaches the edges of the window and gets no padding from the layout
		 * around it: a grid that scrolls itself. The row supplies that padding instead, so its title
		 * lands where every other screen's does.
		 */
		inset?: boolean;
	}

	let {
		title,
		icon,
		level = 1,
		count,
		bytes = null,
		beside,
		titleHidden = false,
		status,
		controls,
		lede,
		inset = false
	}: Props = $props();
</script>

<header class="page-header" class:inset>
	<div class="row" class:tabbed={titleHidden}>
		{#if level === 2}
			<h2 class:clipped={titleHidden}>{title}</h2>
		{:else}
			<h1 class:clipped={titleHidden}>
				{#if icon}<Icon name={icon} size={20} />{/if}
				{title}
			</h1>
		{/if}

		{@render beside?.()}

		<!-- Beside the title rather than in it, so the heading stays the name of the screen. Grouped
		     by the one count formatter, so the header and a filter panel write a number the same
		     way. -->
		{#if count !== undefined && count > 0 && !titleHidden}
			<span class="count">{withSize(counted(count), count, bytes)}</span>
		{/if}

		{@render status?.()}

		<!-- Always rendered, even empty. It is what pushes the title to the left and holds the row's
		     height steady, so a screen with controls and a screen without do not sit at two heights. -->
		<div class="controls">{@render controls?.()}</div>
	</div>

	{#if lede}
		<p class="lede">{@render lede()}</p>
	{/if}
</header>

<style>
	/*
	 * No gap of its own under the row: the frame puts `--page-gap` between the header and the body,
	 * and the gap belongs to the thing that puts the two next to each other, not to each screen.
	 */
	.page-header {
		padding-block-end: 0;
	}

	.row {
		display: flex;
		align-items: center;
		gap: var(--space-3);
	}

	.lede {
		max-width: 60ch;
		margin: var(--space-1) 0 0;
		font: var(--text-body);
		color: var(--sift-ink-3);
	}

	/* On a screen that owns its own scroll there is no padding from the layout, so the row stands in
	   for it: the same inset on the top and sides. Everywhere else the layout has already done
	   this and a second copy would double it. */
	.page-header.inset {
		padding: var(--space-6) var(--space-6) var(--page-gap);
	}

	/*
	 * One size for every screen: the display size, 1.4375rem.
	 *
	 * A title left to each screen ranges from about 1rem to the browser's own 2em, which puts the
	 * two extremes of the same thing about twice as far apart as they should ever be.
	 */
	h1 {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		/* A toolbar control's height, so the title sits at one line with or without controls. */
		min-block-size: 36px;
		/* Pinned to the top, so taller controls (People's tabs) do not carry the title down. */
		align-self: flex-start;
		margin: 0;
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
		color: var(--sift-ink);
	}

	/* Quieter when the row is a section of a page rather than the page. The controls beside it are
	   the same either way: somebody looking at one person's files wants the tile size just as much. */
	h2 {
		min-block-size: 36px;
		display: flex;
		align-items: center;
		align-self: flex-start;
		margin: 0;
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
		color: var(--sift-ink);
	}

	/* The glyph is quieter than the word. It is a landmark, not the name: at the same weight as
	   the text it competes with the thing it is pointing at. */
	h1 :global(.icon) {
		color: var(--sift-ink-3);
	}

	.count {
		padding: 2px var(--space-2);
		border-radius: var(--radius-full);
		background: var(--sift-surface-3);
		color: var(--sift-ink-3);
		font: var(--text-label);
		font-variant-numeric: tabular-nums;
	}

	.controls {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		margin-left: auto;
		flex-wrap: wrap;
		justify-content: flex-end;
	}

	/* Beside a row of tabs the controls take what the tabs leave, and a tab with no box keeps the
	   box's room, so the strip wraps the same on every tab. */
	@media (min-width: 768px) {
		.tabbed .controls {
			flex: 1 1 0%;
			min-block-size: var(--control-height);
			min-inline-size: var(--tab-box-floor);
		}
	}

	/* Off the screen, still in the accessible tree. Clipped rather than hidden, because
	   `display: none` and `visibility: hidden` both take it out of the tree and leave the section
	   unnamed: the same rule `Field` uses for a label it does not draw. */
	.clipped {
		position: absolute;
		width: 1px;
		height: 1px;
		clip-path: inset(50%);
		overflow: hidden;
		white-space: nowrap;
	}

	/*
	 * At a phone's width the title keeps its line with the count beside it, and the controls take
	 * the line under it, starting where the title starts, wrapping onto another line when they
	 * must. Beside the title they would wrap into a stack of right-aligned lines with the
	 * count floating between them; nothing on a page scrolls sideways.
	 */
	@media (max-width: 767px) {
		.row {
			flex-wrap: wrap;
		}

		.controls {
			flex: 1 0 100%;
			margin-left: 0;
			/* Packed to the end, the one right edge every control on a phone ends at: a form's
			   Cancel and Save stand here and again at the foot of the form, and the two pairs read
			   as one only when they pack the same way. */
			justify-content: flex-end;
		}

		.controls:empty {
			display: none;
		}

		.tabbed .controls,
		.tabbed .controls:empty {
			display: flex;
			min-block-size: var(--control-height);
		}
	}
</style>
