<script lang="ts">
	/*
	 * A block's figures as a row of cards: the label the server gave, the number in its unit, and
	 * the one sentence the server put under it (a comparison, a busiest day).
	 *
	 * A figure of nothing is left out (`worthACard`). The number counts up when a period's answer
	 * arrives (`FigureCard`), the one count in the app that moves. While the vault is open a figure
	 * can carry the part of it that came from hidden things, and the card says how much beside the
	 * crossed-out eye, which is named Hidden; while it is locked the server sends 0 there (a locked
	 * page says nothing about what it left out), so nothing is drawn.
	 *
	 * What a figure counts is one hover away: the server's one sentence (`defines`), in the tooltip
	 * of the small press at the end of the figure's foot line. A press holds it; Escape lets go.
	 *
	 * `lead` sets the first figure as the one the page leads with: the figure size, on the accent's
	 * run, twice the width of the others. A figure the server sent a `trend` for draws it along its
	 * foot. While it counts, a figure's in-between frames are worded by the same short form the
	 * server words its final figure in (`figureWords`), so a count steps through words, never bare
	 * digits.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Tooltip } from '$lib/components/common';
	import FigureCard from '$lib/components/charts/FigureCard.svelte';
	import HistorySentence from '$lib/components/common/HistorySentence.svelte';

	import { figureWords, saidOf, worthACard, type Figure } from '$lib/components/insights/figures';
	import { INSIGHTS_WORDS } from '$lib/components/insights/words';

	interface Props {
		figures: readonly Figure[];
		/** `large` for a period's headline figures, `small` for a block among several. */
		size?: 'large' | 'small';
		/** Lead with the first figure, drawn as the page's own. */
		lead?: boolean;
		/** Inside a card already: the figures stand bare in a row, not on cards of their own. */
		bare?: boolean;
	}

	let { figures, size = 'large', lead = false, bare = false }: Props = $props();

	const shown = $derived(figures.filter(worthACard));
	/* The figure whose definition a press is holding up. */
	let held = $state<number | null>(null);
</script>

{#if shown.length > 0}
	<div class="figures {size}">
		{#each shown as figure, index (index)}
			{#snippet foot()}
				{#if figure.hidden_part > 0}
					<Icon name="visibility_off" size={16} label={INSIGHTS_WORDS.hidden} />
					{saidOf(figure.hidden_said, figure.hidden_part, figure.unit)}
				{/if}
				{#if figure.defines.length > 0}
					<span class="defines">
						<Tooltip label={INSIGHTS_WORDS.defines} held={held === index}>
							{#snippet detail()}<HistorySentence pieces={figure.defines} />{/snippet}
							<Button
								tone="ghost"
								size="small"
								icon="info"
								aria-label={INSIGHTS_WORDS.defines}
								onclick={() => (held = held === index ? null : index)}
								onkeydown={(event: KeyboardEvent) => {
									if (event.key === 'Escape') held = null;
								}}
								onblur={() => (held = null)}
							/>
						</Tooltip>
					</span>
				{/if}
			{/snippet}
			{#snippet sentence()}
				<HistorySentence pieces={figure.caption ?? []} />
			{/snippet}
			<div class="figure" class:first={lead && index === 0}>
				<FigureCard
					label={figure.label}
					value={figure.value}
					format={(value) =>
						value === figure.value
							? saidOf(figure.said, value, figure.unit)
							: figureWords(value, figure.unit)}
					counts={figure.unit !== 'minute_of_day'}
					size={lead && index === 0 ? 'hero' : size}
					tone={lead && index === 0 ? 'lit' : 'plain'}
					trend={figure.trend ?? []}
					ground={!bare}
					aside={figure.hidden_part > 0 || figure.defines.length > 0 ? foot : undefined}
					caption={figure.caption && figure.caption.length > 0 ? sentence : undefined}
				/>
			</div>
		{/each}
	</div>
{/if}

<style>
	/* As many cards to a row as fit at a readable width, each the same width as its neighbours.
	   `auto-fit`, so a row of fewer cards than would fit shares the whole width between them
	   rather than leaving the room of a card that is not there. */
	.figures {
		display: grid;
		gap: var(--space-4);
	}

	.large {
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 13rem), 1fr));
	}

	.small {
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 11rem), 1fr));
	}

	.figure {
		display: grid;
		min-inline-size: 0;
	}

	/* The press at the end of the figure's foot line, after the hidden part where there is one. */
	.defines {
		margin-inline-start: auto;
	}

	/* The figure a page leads with takes two columns where the row has them. */
	.first {
		grid-column: span 2;
	}

	@media (max-width: 767px) {
		.first {
			grid-column: auto;
		}
	}
</style>
