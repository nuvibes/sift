<script lang="ts">
	/*
	 * INSIGHTS: what you viewed, organized and imported, for a day, a week, a month, a year or all
	 * of it, as a board of tiles across the whole width.
	 *
	 * The server says every sentence and figure (`GET /api/insights`); the board lays them out as
	 * tiles (`board/tiles.ts`) and composes nothing. The first screen holds the period whole: its
	 * line, the time viewed with its bars, the top People, the top file, the Sites, the days, what
	 * was imported and the visits; below it the families' tiles. The first screen's tiles stand as
	 * skeletons before the answer lands, so nothing moves when it does, and a period changing keeps
	 * the last tiles dimmed until the next are in.
	 *
	 * A period is a place: the tabs are links and the arrows move the address (`period`, `at`).
	 * Every tile opens its table on Stats; beside Stats, a press opens this period's recap as its
	 * deck of cards, or says there is none.
	 */
	import { goto } from '$app/navigation';
	import { toasts } from '$lib/shell/toasts.svelte';

	import { Button, Problem, SectionHeading } from '$lib/components/common';
	import { PeriodAnswer } from '$lib/components/insights/answer.svelte';
	import Board from '$lib/components/insights/board/Board.svelte';
	import { skeletonOf, tilesOf } from '$lib/components/insights/board/tiles';
	import PeriodBar from '$lib/components/insights/PeriodBar.svelte';
	import RecapAnnouncement from '$lib/components/insights/RecapAnnouncement.svelte';
	import RecapHeads from '$lib/components/insights/RecapHeads.svelte';
	import {
		INSIGHTS_PATH,
		STATS_PATH,
		addressOf,
		type Place
	} from '$lib/components/insights/period';
	import { DECK_WORDS, INSIGHTS_WORDS, STATS_WORDS } from '$lib/components/insights/words';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { onAssetStateChange, reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { periodKey, recapShelf } from '$lib/library/recaps.svelte';

	let { data }: { data: Place } = $props();

	/* How many recaps the Recaps block names; every one is a link away. */
	const RECAPS_NAMED = 6;

	const read = new PeriodAnswer(() => data);
	reloadOnLibraryChange(() => void read.reread());
	onAssetStateChange(() => void read.reread());
	const answer = $derived(read.answer);
	const failed = $derived(read.failed);
	const loading = $derived(read.loading);

	const tiles = $derived(answer ? tilesOf(answer) : skeletonOf(data.period));
	/* The period's own files, the ground of the tiles about the whole period. */
	const ground = $derived(
		(answer?.blocks.find((block) => block.id === 'most_viewed')?.lists ?? [])
			.flatMap((list) => list.rows.map((row) => row.cover ?? ''))
			.filter((cover) => cover.startsWith('/api/assets/'))
	);
	const statsAt = (block: string) => `${addressOf(data, STATS_PATH)}#${block}`;

	/* The deck press's words: every period but All has a recap of its own, and today says so. */
	const deck = $derived(data.period === 'all' ? null : DECK_WORDS[data.period]);
	const see = $derived(deck && (answer?.today_is_live && deck.today ? deck.today : deck.see));

	/* The recap of the period answered, opened as its deck; where none was created, said why. */
	async function openDeck(): Promise<void> {
		if (answer === null || deck === null) return;
		await recapShelf.load();
		const recap = recapShelf.of(periodKey(answer.period, answer.from) ?? '');
		if (recap) await goto(`/insights/recaps/${encodeURIComponent(recap.id)}`);
		else toasts.show(deck.none);
	}
</script>

<svelte:head><title>{INSIGHTS_WORDS.title}</title></svelte:head>

{#snippet stats()}
	<div class="presses">
		{#if deck}
			<Button size="small" tone="secondary" onclick={() => void openDeck()}>{see}</Button>
		{/if}
		<Button size="small" tone="secondary" onclick={() => void goto(addressOf(data, STATS_PATH))}
			>{STATS_WORDS.title}</Button
		>
	</div>
{/snippet}

<PageFrame>
	{#snippet header()}
		<PageHeader title={INSIGHTS_WORDS.title} icon="insights">
			{#snippet lede()}{INSIGHTS_WORDS.headLine}{/snippet}
		</PageHeader>
	{/snippet}
	{#snippet tools()}
		<PeriodBar place={data} {answer} {loading} path={INSIGHTS_PATH} after={stats} />
	{/snippet}

	<div class="insights">
		<RecapAnnouncement />

		{#if failed}
			<Problem message={INSIGHTS_WORDS.failed} />
		{:else}
			<Board {tiles} {statsAt} {ground} stale={loading && answer !== null} />
		{/if}

		<section aria-labelledby="insights-recaps">
			<SectionHeading id="insights-recaps">{INSIGHTS_WORDS.recaps}</SectionHeading>
			<RecapHeads limit={RECAPS_NAMED} />
		</section>
	</div>
</PageFrame>

<style>
	.insights {
		display: flex;
		flex-direction: column;
		gap: var(--space-8);
	}

	.presses {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}
</style>
