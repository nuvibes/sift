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
	whoever opened the bar; roving focus belongs to VerbButtons, not the box. */
	/* The bar that floats over the grid when things are picked, so no tile moves under a click; the
	 * count is the only place that says how many. */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { arrive } from '$lib/shell/motion.svelte';
	import { MOST_SELECTED } from '$lib/grid/grid.svelte';
	import { phoneWidth } from './phone-width.svelte';

	interface Props {
		count: number;
		/** How many the whole question matches, when more than is on screen. */
		total?: number;
		/** Pick every one of them, not just the loaded page; absent, not offered. */
		onselectall?: () => Promise<void>;
		/** What the things are, singular ("file"), pluralized here. */
		noun?: string;
		/** The plural, where adding an "s" is wrong ("person", "people"). */
		plural?: string;
		onclear: () => void;
		/** The buttons. Given as a snippet so the bar has no opinion about what can be done. */
		actions: Snippet;
		/** The door to the verbs the bar does not name, outside the scrolling strip. */
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
	/* Grouped thousands, matching the offer beside it. */
	const label = $derived(`${count.toLocaleString()} ${count === 1 ? noun : many} selected`);

	/* More of the question than is picked, by the pager's own number. */
	const more = $derived(total !== undefined && onselectall !== undefined && count < total);

	/* The offer states the cap (`MOST_SELECTED`) where the query is bigger, never a false "all". */
	const allLabel = $derived(
		(total ?? 0) > MOST_SELECTED
			? `Select ${MOST_SELECTED.toLocaleString()} of ${(total ?? 0).toLocaleString()}`
			: `Select all ${(total ?? 0).toLocaleString()}`
	);

	/* Disabled while reading, so a second press cannot run it twice. */
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
	<!--
	A region, not a dialog: it traps nothing. It rises from the bottom, arriving from the pick.
	-->
	<div
		class="bar"
		role="region"
		aria-label="Selection"
		transition:arrive={{ y: 16, pace: 'base', spring: true }}
	>
		<button class="clear" type="button" onclick={onclear} aria-label="Clear selection">
			<Icon name="close" />
		</button>

		<!-- Announced politely when the number changes. -->
		<span class="count" aria-live="polite">{label}</span>

		<!-- The rest of the question beside the count it extends, far from Delete. -->

		{#if more}
			<span class="all">
				<Button tone="ghost" size="small" disabled={picking} onclick={pickEverything}>
					{picking ? 'Selecting\u2026' : allLabel}
				</Button>
			</span>
		{/if}

		<!-- On a phone the verbs and the door are one row of five equal columns. -->
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
	/* Centred on the screen, not the window: absolute inside the screen, so it follows the rail. */
	.bar {
		position: absolute;
		z-index: var(--z-bar);
		/* Above the frame's footer (--frame-footer), or it covers the pager. */
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

	/* The verbs may shrink and, last, scroll; min 0 so the last is not cut off. */
	.actions {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* The callers' buttons, dressed here through the slot. */
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
		/* The ground steps, nothing moves. */
		transition: background var(--dur-instant) var(--ease);
	}

	.actions :global(button:hover:not(:disabled)) {
		background: var(--sift-surface-2);
	}

	/* An offered verb that cannot be answered now looks it, and does not light. */
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

	/* Wrappers only, `contents` on a wide window, placed by the phone grid. */
	.all,
	.more {
		display: contents;
	}

	/* At a phone's width: two lines, the selection and then five equal columns of verbs, above the
	 * docked corner player (--mini-docked). */
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

		/* One grid of equal columns for every verb and the door, whatever wraps each. */
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
