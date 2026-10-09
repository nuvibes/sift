<script lang="ts">
	/* The row at the top of every screen: what it is on the left, what it can do on the right. */
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
		/** The bytes behind a count of files, said beside the count. */
		bytes?: number | null;
		/** A control belonging to the title, drawn right after the name. */
		beside?: Snippet;
		/** Hide the title and count where a row of tabs is the heading; clipped, not removed. */
		titleHidden?: boolean;
		/** A word about what the screen is DOING, beside the title. Not what it contains. */
		status?: Snippet;
		/** This page's own controls: sort, size, filters, whatever it has. */
		controls?: Snippet;
		/** A sentence under the title, for a screen whose whole idea needs one. */
		lede?: Snippet;
		/** For a screen that scrolls itself with no layout padding: the row supplies it. */
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

		<!-- Beside the title, so the heading stays the screen's name; one count formatter. -->
		{#if count !== undefined && count > 0 && !titleHidden}
			<span class="count">{withSize(counted(count), count, bytes)}</span>
		{/if}

		{@render status?.()}

		<!-- Always rendered, so the row's height holds with or without controls. -->
		<div class="controls">{@render controls?.()}</div>
	</div>

	{#if lede}
		<p class="lede">{@render lede()}</p>
	{/if}
</header>

<style>
	/* No gap of its own: the frame puts `--page-gap` between header and body. */
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

	/* A screen owning its scroll gets no layout padding, so the row stands in for it. */
	.page-header.inset {
		padding: var(--space-6) var(--space-6) var(--page-gap);
	}

	/* One display size for every screen's title. */
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

	/* Quieter when the row is a section of a page; the controls are the same. */
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

	/* The glyph is a landmark, quieter than the name it points at. */
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

	/* Beside tabs, a tab with no box keeps its room, so the strip wraps alike on every tab. */
	@media (min-width: 768px) {
		.tabbed .controls {
			flex: 1 1 0%;
			min-block-size: var(--control-height);
			min-inline-size: var(--tab-box-floor);
		}
	}

	/* Clipped, not hidden, so the section stays named in the accessible tree. */
	.clipped {
		position: absolute;
		width: 1px;
		height: 1px;
		clip-path: inset(50%);
		overflow: hidden;
		white-space: nowrap;
	}

	/* On a phone the controls take the line under the title; nothing scrolls sideways. */
	@media (max-width: 767px) {
		.row {
			flex-wrap: wrap;
		}

		.controls {
			flex: 1 0 100%;
			margin-left: 0;
			/* Packed to the end, the one right edge every control on a phone ends at. */
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
