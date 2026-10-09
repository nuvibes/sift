<script lang="ts">
	/*
	 * Every Organize list of cards, so a card is one width and every row one height on every wall.
	 * `cards` is the wall's paging (`CardPaging.cards`), which measures a card here, so a page is
	 * whole rows.
	 */
	import type { Snippet } from 'svelte';
	import type { Attachment } from 'svelte/attachments';

	interface Props {
		children: Snippet;
		/** The wall's paging attachment, where the wall pages. */
		cards?: Attachment<HTMLElement>;
		/** The list's accessible name. */
		label?: string;
		/** The board's narrower column. */
		board?: boolean;
	}

	let { children, cards, label, board = false }: Props = $props();
</script>

<ul class="wall" class:board aria-label={label} {@attach cards}>
	{@render children()}
</ul>

<style>
	/* Every row the tallest card's height; `min()` keeps a narrow window from scrolling sideways. */
	.wall {
		--wall-column: var(--card-column-min);
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(var(--wall-column), 100%), 1fr));
		grid-auto-rows: 1fr;
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.wall.board {
		--wall-column: var(--board-column-min);
		gap: var(--space-4);
	}

	.wall > :global(li) {
		display: grid;
		min-inline-size: 0;
	}

	@media (max-width: 767px) {
		.wall {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
