<script lang="ts">
	/*
	 * THE INSIGHTS BOARD: the period's tiles on a grid that takes the frame's whole width.
	 *
	 * Twelve columns, eight under 1200 px, four under 768, two under 480, counted in the board's own
	 * width; a row is one column's width, so a 1x1 tile is square at every width. A tile is a
	 * `RecapCard` at its tile size, so the board and a deck are the same cards. The grid packs each
	 * tile into the first room it fits, in reading order. A tile is a press to its table on Stats
	 * (`pressOnCard`, so a name inside it stays its own link), and the keyboard's way in is the
	 * tile's own link, which draws the ring round the whole tile.
	 */
	import { Skeleton } from '$lib/components/common';
	import { pressOnCard } from '$lib/components/common/card-press';
	import RecapCard from '$lib/components/insights/RecapCard.svelte';
	import type { Tile } from '$lib/components/insights/board/tiles';
	import { BOARD_WORDS } from '$lib/components/insights/words';

	interface Props {
		tiles: readonly Tile[];
		/** The address of a block's table on Stats. */
		statsAt: (block: string) => string;
		/** The period's own files, the ground of the tiles that name none. */
		ground?: readonly string[];
		/** The last period's tiles while the next answer is on its way. */
		stale?: boolean;
	}

	let { tiles, statsAt, ground = [], stale = false }: Props = $props();

	/* The tiles about the whole period stand on its files; a tile with pictures of its own, on them. */
	const GROUNDED = new Set(['headline', 'viewed']);
</script>

<div class="board" class:stale aria-busy={stale || tiles.some((tile) => tile.card === null)}>
	<ul class="grid">
		{#each tiles as tile (tile.slot)}
			{@const href = statsAt(tile.block)}
			<!-- svelte-ignore a11y_no_noninteractive_element_interactions: the tile only hands a
			     press on its ground to its link (`pressOnCard`); the link is the keyboard's way in. -->
			<!-- svelte-ignore a11y_click_events_have_key_events -->
			<li
				class="tile"
				data-slot={tile.slot}
				data-shape={tile.shape}
				data-family={tile.family}
				onclick={(event) => pressOnCard(event, tile.card ? href : undefined)}
			>
				{#if tile.card}
					<RecapCard
						card={tile.card}
						size="tile"
						shape={tile.shape}
						family={tile.family}
						heading={tile.title}
						ground={GROUNDED.has(tile.slot) && ground.length > 0 ? ground : null}
					/>
					<a class="open" {href}>{BOARD_WORDS.open(tile.title)}</a>
				{:else}
					<Skeleton shape="block" />
				{/if}
			</li>
		{/each}
	</ul>
</div>

<style>
	.board {
		container-type: inline-size;
	}

	.grid {
		--cols: 12;
		display: grid;
		grid-template-columns: repeat(var(--cols), minmax(0, 1fr));
		grid-auto-rows: calc((100cqi - (var(--cols) - 1) * var(--space-4)) / var(--cols));
		grid-auto-flow: row dense;
		gap: var(--space-4);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	@container (width < 1200px) {
		.grid {
			--cols: 8;
		}
	}

	@container (width < 768px) {
		.grid {
			--cols: 4;
		}
	}

	@container (width < 480px) {
		.grid {
			--cols: 2;
		}
	}

	.tile {
		--w: 2;
		--h: 2;
		position: relative;
		display: grid;
		grid-column: span var(--w);
		grid-row: span var(--h);
		min-inline-size: 0;
		min-block-size: 0;
		border-radius: var(--radius-lg);
	}

	.tile:has(.open) {
		cursor: pointer;
	}

	.tile[data-shape='1x1'] {
		--w: 1;
		--h: 1;
	}

	.tile[data-shape='2x1'] {
		--h: 1;
	}

	.tile[data-shape='4x2'] {
		--w: 4;
	}

	.tile[data-shape='6x2'] {
		--w: 6;
	}

	/* The four small figures stand as one square under the first screen's two rows, so the tiles
	   after them pack without a hole: at the start on twelve columns; on eight, beside the top file
	   and the Sites over the days. */
	@container (width >= 1200px) {
		.tile[data-slot='imported'] {
			grid-area: 3 / 1;
		}

		.tile[data-slot='visits'] {
			grid-area: 3 / 2;
		}

		.tile[data-slot='sessions'] {
			grid-area: 4 / 1;
		}

		.tile[data-slot='third'] {
			grid-area: 4 / 2;
		}
	}

	@container (768px <= width < 1200px) {
		.tile[data-slot='sites'] {
			grid-area: 3 / 3 / auto / span 2;
		}

		.tile[data-shape='2x1']:is([data-slot='days'], [data-slot='when']) {
			grid-area: 4 / 3 / auto / span 2;
		}

		.tile[data-slot='imported'] {
			grid-area: 3 / 5;
		}

		.tile[data-slot='visits'] {
			grid-area: 3 / 6;
		}

		.tile[data-slot='sessions'] {
			grid-area: 4 / 5;
		}

		.tile[data-slot='third'] {
			grid-area: 4 / 6;
		}
	}

	/* Narrower than a tile: the whole width, and its rows shrink with it. */
	@container (width < 768px) {
		.tile[data-shape='6x2'] {
			--w: 4;
			--h: 1;
		}
	}

	@container (width < 480px) {
		.tile[data-shape='4x2'],
		.tile[data-shape='6x2'] {
			--w: 2;
			--h: 1;
		}
	}

	/* The tile's link: no room of its own and no press (a press on the tile hands itself on), but
	   a stop for the keyboard, read out by its words. */
	.open {
		position: absolute;
		inset: 0;
		opacity: 0;
		pointer-events: none;
	}

	.tile:has(.open:focus-visible) {
		outline: var(--focus-outline);
		outline-offset: var(--focus-width);
	}

	/* The last period's tiles while the next are on their way: still readable, plainly not current. */
	.stale {
		opacity: 0.55;
		transition: opacity var(--dur-instant) var(--ease);
	}
</style>
