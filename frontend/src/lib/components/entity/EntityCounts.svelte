<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * WHY NOT BITS-UI: a row of links, each a glyph and a number with the app's own Tooltip on it.
	 * There is no behaviour here for a library to own; the tooltip is already the library's.
	 *
	 * What one entity has, as a row of glyphs and numbers, each a way into that tab of its page.
	 *
	 * One implementation for the hover card on a link (`EntityPreview`) and the card on an entity
	 * wall (`EntityCard`), so a glyph, an unknown number and a cell's spoken name mean one thing in
	 * both. The cells are `tabsFor`'s (the set, words, glyphs and addresses), so the row agrees
	 * with the tab strip of the page it opens. Which cells to hand in is the caller's business: the
	 * hover card hands every tab, and the wall card leaves out the files (said in words under the
	 * name) and a person's Seen with.
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
		 * Keep the row to one line, and where the "+n" at its end goes when it does not fit: the
		 * page of the thing the card is, where every tab is.
		 *
		 * The wall card's only: a wall is a grid of equal cards and a second line under some of
		 * them breaks the shape (see `fitCells`). The hover card is one thing on its own with room
		 * to wrap, so it leaves this out and keeps every cell.
		 */
		fold?: string;
		/**
		 * Each number with the word for what it counts ("4 sites"), instead of a glyph's tooltip
		 * saying it. The hover card's: it has room to wrap, and a figure alone there is a bare number.
		 */
		worded?: boolean;
	}

	let { name, cells, fold, worded = false }: Props = $props();

	/* What a cell with no number says. The em dash the rest of the app draws for "nothing recorded":
	   a nought would be a claim, and a blank reads as a number that failed to arrive. */
	const NOTHING = '—';

	/*
	 * Which cells stand in the line, measured rather than counted.
	 *
	 * Null until the first measurement, which draws every cell: the honest first frame, and what a
	 * browser with no layout (a test, a print) keeps. Every cell stays in the document; those that
	 * do not fit are taken out of the flow and out of sight (`.aside`), because an undrawn cell
	 * cannot be measured and the next resize has to know its width. `visibility: hidden` also
	 * removes it from the tab order and from a screen reader, so the folded cells are said once, by
	 * the "+n".
	 *
	 * Watched on the row and on every cell: the row changes width with the window, and a cell
	 * changes width when a number gains a digit or the face finishes loading, which the row's own
	 * size would not report.
	 */
	let row = $state<HTMLElement | null>(null);
	let fitted = $state<FittedCells | null>(null);

	$effect(() => {
		const nav = row;
		const all = cells;
		if (fold === undefined || !nav) return;
		/* The last answer written, as a key, held here rather than read back from `fitted`, which
		   would make this effect depend on what it writes. */
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
			/* Only a CHANGED answer is written: the classes this sets move no cell's width, so a
			   second measurement agrees with the first, and writing an equal answer anyway would
			   redraw the row for nothing on every observed resize. */
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
	/* The number the "+n" shows, and says: one digit's worth while nothing is folded, so the hidden
	   tail still has a width to measure. */
	const more = $derived(Math.max(folded.length, 1));
</script>

<nav
	class="counts"
	class:fitted={fold !== undefined}
	aria-label="What {name} is on"
	bind:this={row}
>
	<!-- As tall as a cell, and no wider than nothing: a card with nothing to count keeps the height
	     of one that has, so a wall of cards stays one height. -->
	<span class="strut" aria-hidden="true"></span>
	{#each cells as cell (cell.id)}
		<!-- Each pair is a glyph and a number and says nothing else, which is exactly what a
		     tooltip is for: a row of shapes, and which shape meant Photo Sets rather than
		     Collections is otherwise a thing to learn by opening one. The words are the TAB's own, so
		     the label agrees with the page the number opens. -->
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
		<!-- The tail, folded: hovering names what it holds, pressing opens the page, where every tab
		     is. Always in the document so it can be measured: out of sight while nothing is folded,
		     for the reason the cells are. The name a screen reader hears starts with what is on
		     screen ("+2") and says the rest. -->
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
	/* A wrapping row rather than a fixed set of columns: how many tabs a thing has is the tab
	   strip's business, and a grid written here would have to be rewritten when it changes. */
	.counts {
		position: relative;
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
		/*
		 * The first glyph flush with the words above it. Each cell carries its own inline padding
		 * (the ground that steps on hover, and the area a pointer can land on), so the row is
		 * pulled out by exactly that padding: the hit area and hover ground keep their size and
		 * only the inset moves. Both hosts have more room than this on that side (the card's body
		 * pads by `--space-3`), so the ground is never cut.
		 */
		margin-inline-start: calc(-1 * var(--space-2));
	}

	/* The wall card's row: ONE line, always. See `fold` and `fitCells`. What does not fit is
	   folded into the "+n", never wrapped and never shrunk. */
	.counts.fitted {
		flex-wrap: nowrap;
	}

	/* A cell's height with no width: the figure's line and the link's padding, and a negative end
	   margin taking back the gap after it, so the first cell starts where it did. */
	.strut {
		flex: none;
		inline-size: 0;
		block-size: calc(1lh + 2 * var(--space-1));
		margin-inline-end: calc(-1 * var(--space-1));
		font: var(--text-data);
	}

	/* A cell never gives up width to its neighbours: the numbers stay the size they are everywhere,
	   and a squeezed cell would be one whose width the fold could no longer measure. */
	.cell {
		flex: none;
	}

	/* A cell that did not fit, and the "+n" while nothing is folded: out of the line and out of
	   sight, but still laid out, so it can be measured again the moment the card grows. `visibility`
	   takes it out of the tab order and the accessibility tree too. */
	.cell.aside {
		position: absolute;
		inset-block-start: 0;
		inset-inline-start: 0;
		visibility: hidden;
		pointer-events: none;
	}

	/* Each pair is a link, so it wears a ground that steps on hover: the Light register
	   for a control that has one. */
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
