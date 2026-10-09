<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * WHY NOT BITS-UI: a row of links, each a glyph and a number with the app's own Tooltip on it.
	 * What one entity has, each cell a way into that tab of its page: one row for the hover card
	 * and the wall card, the cells `tabsFor`'s, so it agrees with the page's tab strip.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import {
		cellFigure,
		cellSaid,
		fitCells,
		type CountCell,
		type FittedCells
	} from './entity-counts';

	interface Props {
		/** Whose numbers these are, for the row's accessible name. */
		name: string;
		cells: readonly CountCell[];
		/**
		 * Keep the row to one line, folding the rest into a "+n" that opens this page (wall cards).
		 */
		fold?: string;
		/** Each number with its word ("4 sites"), for the hover card, which has room. */
		worded?: boolean;
	}

	let { name, cells, fold, worded = false }: Props = $props();

	/* No number is a dash: a nought would be a claim, a blank a failure. */
	const NOTHING = '—';

	/* Which cells stand in the line, measured: null draws every cell; the rest stay laid out but
	   hidden (`.aside`) so the next resize can measure them, the row and every cell observed. */
	let row = $state<HTMLElement | null>(null);
	let fitted = $state<FittedCells | null>(null);

	$effect(() => {
		const nav = row;
		const all = cells;
		if (fold === undefined || !nav) return;
		/* The last answer as a key, held apart so the effect does not read what it writes. */
		let last = '';
		const measure = (): void => {
			const widthOf = (id: string): number =>
				nav.querySelector<HTMLElement>(`[data-cell="${id}"]`)?.getBoundingClientRect().width ?? 0;
			const plus =
				nav.querySelector<HTMLElement>('[data-more]')?.getBoundingClientRect().width ?? 0;
			const gap = Number.parseFloat(getComputedStyle(nav).columnGap) || 0;
			const next = fitCells(
				all.map((one) => ({ id: one.id, width: widthOf(one.id) })),
				nav.clientWidth,
				gap,
				plus
			);
			/* Only a changed answer is written. */
			const key = `${next.shown.join()}|${next.folded.join()}`;
			if (key !== last) {
				last = key;
				fitted = next;
			}
		};
		measure();
		const watch = new ResizeObserver(measure);
		watch.observe(nav);
		for (const one of nav.querySelectorAll('[data-cell], [data-more]')) watch.observe(one);
		return () => watch.disconnect();
	});

	/* The answer applies only while the row is being fitted; without `fold` every cell stands. */
	const answer = $derived(fold === undefined ? null : fitted);
	const standing = (id: string): boolean => answer === null || answer.shown.includes(id);
	const folded = $derived(
		answer === null ? [] : cells.filter((one) => answer?.folded.includes(one.id))
	);
	/* What the "+n" holds, in words: the same label and number each folded cell says aloud. */
	const holds = $derived(folded.map((one) => `${one.label}: ${one.count ?? NOTHING}`).join(', '));
	/* The "+n" number; one digit's worth while nothing folds, so it can be measured. */
	const more = $derived(Math.max(folded.length, 1));
</script>

<nav
	class="counts"
	class:fitted={fold !== undefined}
	aria-label="What {name} is on"
	bind:this={row}
>
	<!-- A cell's height, so a card with nothing to count keeps the wall's height. -->
	<span class="strut" aria-hidden="true"></span>
	{#each cells as cell (cell.id)}
		<!-- Each pair a glyph and a number, named by a tooltip in the tab's own words. -->
		<span class="cell" class:aside={!standing(cell.id)} data-cell={cell.id}>
			<Tooltip label={cell.label}>
				<a
					class="count"
					href={cell.href}
					aria-label={worded
						? (cellSaid(cell) ?? `${cell.label}: not known`)
						: `${cell.label}: ${cellFigure(cell) ?? 'not known'}`}
				>
					<Icon name={cell.icon} size={16} />
					<span class="figure"
						>{worded
							? (cellSaid(cell) ?? `${cell.label} ${NOTHING}`)
							: (cellFigure(cell) ?? NOTHING)}</span
					>
				</a>
			</Tooltip>
		</span>
	{/each}
	{#if fold !== undefined}
		<!--
		The folded tail: hover names it, a press opens the page; always present to be measured.
		-->
		<span class="cell" class:aside={folded.length === 0} data-more>
			<Tooltip label={holds || name}>
				<a
					class="count"
					href={fold}
					aria-label={holds ? `+${counted(more)} more: ${holds}` : `+${counted(more)} more`}
				>
					<span class="figure">+{counted(more)}</span>
				</a>
			</Tooltip>
		</span>
	{/if}
</nav>

<style>
	/* Wrapping, since the count of tabs is the tab strip's business. */
	.counts {
		position: relative;
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
		/* Pulled out by each cell's padding, so the first glyph aligns with the words above. */
		margin-inline-start: calc(-1 * var(--space-2));
	}

	/* The wall card's row: one line; the rest folds. */
	.counts.fitted {
		flex-wrap: nowrap;
	}

	/* A cell's height with no width. */
	.strut {
		flex: none;
		inline-size: 0;
		block-size: calc(1lh + 2 * var(--space-1));
		margin-inline-end: calc(-1 * var(--space-1));
		font: var(--text-data);
	}

	/* A cell never shrinks, or the fold could not measure it. */
	.cell {
		flex: none;
	}

	/* Out of the line and sight, still measured; `visibility` takes it from the tab order too. */
	.cell.aside {
		position: absolute;
		inset-block-start: 0;
		inset-inline-start: 0;
		visibility: hidden;
		pointer-events: none;
	}

	/* A link, whose ground steps on hover. */
	.count {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		padding: var(--space-1) var(--space-2);
		border-radius: var(--radius-md);
		color: var(--sift-ink-2);
		text-decoration: none;
		transition: background var(--dur-instant) var(--ease);
	}

	.count:hover {
		background: var(--sift-surface-4);
		color: var(--sift-ink);
	}

	.count:focus-visible {
		box-shadow: var(--focus-ring);
	}

	.figure {
		font: var(--text-data);
	}
</style>
