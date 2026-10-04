<script lang="ts">
	/*
	 * ONE BLOCK OF THE INSIGHTS SCREEN, WHICHEVER IT IS: its title, its figures as cards, its one
	 * chart, its heat-map, its ranked lists and its notes, each drawn only when the server sent it.
	 *
	 * One component for every block rather than one per block, because every block is the same
	 * shape on the wire (`InsightsBlock`) and differs only in what it carries: ten components would
	 * be ten copies of this markup, free to drift. What makes Overview Overview is the answer.
	 *
	 * The statements are the block's figures said as sentences, so past its floor they are not drawn
	 * again beside the figures; each figure carries the one sentence worth reading under it. They
	 * are drawn where there is nothing else to show: below the floor ("Not enough yet to say."), or
	 * where every figure is zero ("You spent no time in Theater this month."). The one exception is
	 * a CARD of the grid (`variant="card"`), which leads with the block's first statement set large:
	 * a card says one thing, and its figures are the evidence under it.
	 */
	import { Panel, SectionHeading } from '$lib/components/common';
	import HeatMap from '$lib/components/charts/HeatMap.svelte';
	import HistorySentence from '$lib/components/common/HistorySentence.svelte';

	import Figures from '$lib/components/insights/Figures.svelte';
	import { wordsOf, worthACard } from '$lib/components/insights/figures';
	import KindBars from '$lib/components/insights/KindBars.svelte';
	import NamedList from '$lib/components/insights/NamedList.svelte';
	import type { InsightsBlock } from '$lib/components/insights/period';
	import Statements from '$lib/components/insights/Statements.svelte';

	interface Props {
		block: InsightsBlock;
		/** `large` for the period's headline block, `small` for one card among several. */
		size?: 'large' | 'small';
		/**
		 * `section`, a band of the page under its heading; `card`, one card of the grid the smaller
		 * blocks stand in, which leads with the block's one statement (a card says one thing, and
		 * its figures are the evidence under it).
		 */
		variant?: 'section' | 'card';
		/** Lead each list with its first row, drawn large with its picture. */
		lead?: boolean;
		/** Draw a chart of the twenty-four hours as the hour ring. */
		ring?: boolean;
		/**
		 * The block the page opens on: its first statement set as the page's headline, its first
		 * figure as the one the page leads with (`Figures`' `lead`), its chart on a card of its own.
		 */
		hero?: boolean;
	}

	let {
		block,
		size = 'large',
		variant = 'section',
		lead = false,
		ring = false,
		hero = false
	}: Props = $props();

	const heading = $derived(`insights-${block.id}`);
	const calendar = $derived(block.calendar ?? null);
	/* Which figures earn a card is one rule (`worthACard`); this asks only whether any would. */
	const saysOnlyWords = $derived(
		!block.floor_reached ||
			(!block.figures.some(worthACard) && !block.chart && !calendar && block.lists.length === 0)
	);
	/* A card's one statement: the first the block says. */
	const statement = $derived(variant === 'card' || hero ? (block.statements[0] ?? null) : null);
</script>

{#snippet body()}
	<SectionHeading id={heading} band={variant === 'card'}>{block.title}</SectionHeading>
	{#if saysOnlyWords}
		<Statements lines={block.statements} />
	{:else}
		{#if statement}
			<p class="statement" class:headline={hero}><HistorySentence pieces={statement} /></p>
		{/if}
		<Figures figures={block.figures} {size} lead={hero} bare={variant === 'card'} />
		{#if hero && (block.chart || (calendar && calendar.days.length > 0))}
			<!-- The period drawn: its bars and, for a month or a year, its days, on one card. -->
			<Panel tone="raised" corner="lg" inset="md">
				<div class="inside drawn">
					{#if block.chart}
						<div class="bars"><KindBars chart={block.chart} label={block.title} {ring} /></div>
					{/if}
					{#if calendar && calendar.days.length > 0}
						<HeatMap
							days={calendar.days}
							format={wordsOf(calendar.days, calendar.unit)}
							label={block.title}
						/>
					{/if}
				</div>
			</Panel>
		{:else}
			{#if block.chart}
				<KindBars chart={block.chart} label={block.title} {ring} />
			{/if}
			{#if calendar && calendar.days.length > 0}
				<HeatMap
					days={calendar.days}
					format={wordsOf(calendar.days, calendar.unit)}
					label={block.title}
				/>
			{/if}
		{/if}
		{#if block.lists.length > 0}
			<div class="lists">
				{#each block.lists as list, index (index)}
					<NamedList {list} {lead} />
				{/each}
			</div>
		{/if}
	{/if}
	{#if block.floor_reached && block.notes && block.notes.length > 0}
		<Statements lines={block.notes} quiet />
	{/if}
{/snippet}

<section
	class="block {size} {variant}"
	class:below={!block.floor_reached}
	data-block={block.id}
	aria-labelledby={heading}
>
	{#if variant === 'card'}
		<Panel tone="raised" corner="lg" inset="md">
			<div class="inside">{@render body()}</div>
		</Panel>
	{:else}
		{@render body()}
	{/if}
</section>

<style>
	.block {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		min-inline-size: 0;
	}

	/* The lists side by side where there is room, each as wide as a name and its figure need. */
	.lists {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 16rem), 1fr));
		gap: var(--space-6) var(--space-8);
	}

	/* A small block keeps its lists in one column under its cards. */
	.small .lists {
		grid-template-columns: minmax(0, 1fr);
	}

	/* A block with nothing to say yet keeps its heading and its one line, and takes less room. */
	.below {
		gap: var(--space-1);
	}

	/* A card of the grid: its contents in a column inside the panel, the room of a section. */
	.card {
		display: grid;
	}

	.inside {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		min-inline-size: 0;
		padding: var(--space-2);
	}

	/* The page's headline: the period's first sentence, the largest words on the page but its figure. */
	.statement.headline {
		max-inline-size: 40ch;
		font: var(--text-display);
		letter-spacing: var(--tracking-display);
	}

	/* The period drawn: the bars take the room, the heat-map stands beside them where it fits. */
	.drawn {
		flex-flow: row wrap;
		align-items: flex-start;
		gap: var(--space-8);
	}

	.bars {
		flex: 1 1 28rem;
		min-inline-size: 0;
	}

	/* The card's one statement, set as the thing the card says. */
	.statement {
		margin: 0;
		font: var(--text-h2);
		letter-spacing: var(--tracking-h2);
		text-wrap: balance;
		color: var(--sift-ink);
	}
</style>
