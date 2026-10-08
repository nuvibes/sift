<script lang="ts">
	/*
	 * INSIGHTS: what you viewed, organized and added, for a day, a week, a month, a year or all of it.
	 *
	 * The server says every sentence and figure (`GET /api/insights`): the first sentences, and each
	 * block in its fixed order, or below its floor "Not enough yet to say." This screen lays the
	 * answer out and composes nothing; a block the server did not send (What Sift did, to a guest)
	 * is not drawn.
	 *
	 * As a story in the order a person asks it: the Overview (its first sentence as the headline,
	 * time viewed as the lead figure, the period's chart and heat-map on a card), When and By kind
	 * side by side, Most viewed with each list's number one large, then the rest as a grid of cards.
	 * The groups rise in one after another when a period's answer lands. While the viewing is below
	 * its floor the page opens on the first sentences instead, and no empty block is drawn.
	 *
	 * A period is a place: the tabs are links and the arrows move the address (`period`, `at`), and
	 * where it starts and ends is the server's answer. Nothing waits on anything else: the heading
	 * and tabs draw immediately, the recaps read their own answers, and a period changing keeps the
	 * last figures dimmed until the next are in. Your path is under Settings, in Get to know Sift.
	 *
	 * Beside Stats, a press opens this period's recap as its deck of cards, or says there is none.
	 */
	import { goto } from '$app/navigation';
	import { toasts } from '$lib/shell/toasts.svelte';

	import { Button, Problem, SectionHeading, Skeleton } from '$lib/components/common';
	import { PeriodAnswer } from '$lib/components/insights/answer.svelte';
	import InsightsBlock from '$lib/components/insights/InsightsBlock.svelte';
	import PeriodBar from '$lib/components/insights/PeriodBar.svelte';
	import RecapAnnouncement from '$lib/components/insights/RecapAnnouncement.svelte';
	import RecapHeads from '$lib/components/insights/RecapHeads.svelte';
	import Statements from '$lib/components/insights/Statements.svelte';
	import {
		INSIGHTS_PATH,
		STATS_PATH,
		addressOf,
		type InsightsBlock as Block,
		type Place
	} from '$lib/components/insights/period';
	import { drawsAnything } from '$lib/components/insights/figures';
	import { DECK_WORDS, INSIGHTS_WORDS, STATS_WORDS } from '$lib/components/insights/words';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { onAssetStateChange, reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { periodKey, recapShelf } from '$lib/library/recaps.svelte';

	let { data }: { data: Place } = $props();

	/* The blocks drawn from other answers than this one, or elsewhere: see the header. */
	const DRAWN_ELSEWHERE = new Set(['recaps', 'path']);

	/* Where each block stands: the two read side by side, and the smaller ones as a grid of cards.
	   A group stands where its first block would. */
	const SIDE_BY_SIDE = new Set(['by_kind', 'when']);
	const SMALL = new Set(['theater', 'sittings', 'opinions', 'organizing', 'arrived', 'machine']);
	const STORY = ['overview', 'pair', 'most_viewed', 'row'];

	/* How many recaps the Recaps block names; every one is a link away. */
	const RECAPS_NAMED = 6;

	const read = new PeriodAnswer(() => data);
	reloadOnLibraryChange(() => void read.reread());
	onAssetStateChange(() => void read.reread());
	const answer = $derived(read.answer);
	const arrival = $derived(read.arrival);
	const failed = $derived(read.failed);
	const loading = $derived(read.loading);

	/* The viewing below its floor: the page opens on what there is instead (see the header). */
	const overview = $derived(answer?.blocks.find((block) => block.id === 'overview') ?? null);
	/* A block below its floor, or past it with nothing to draw, is left out: a heading over nothing
	   is an empty box on the page (see `drawsAnything`). Alongside is the exception while the
	   viewing is past its floor: it is looked for by name, so it says "Not enough yet to say.". */
	const blocks = $derived(
		answer?.blocks.filter(
			(block) =>
				!DRAWN_ELSEWHERE.has(block.id) &&
				((block.floor_reached && drawsAnything(block)) ||
					(block.id === 'alongside' && overview?.floor_reached === true))
		) ?? []
	);
	const lead = $derived(
		answer === null || overview?.floor_reached
			? []
			: answer.first_sentences.length > 0
				? answer.first_sentences
				: (overview?.statements ?? [])
	);
	/* The deck press's words: every period but All has a recap of its own. */
	const deck = $derived(data.period === 'all' ? null : DECK_WORDS[data.period]);

	/* The recap of the period answered, opened as its deck; where none was created, said why. */
	async function openDeck(): Promise<void> {
		if (answer === null || deck === null) return;
		await recapShelf.load();
		const recap = recapShelf.of(periodKey(answer.period, answer.from) ?? '');
		if (recap) await goto(`/insights/recaps/${encodeURIComponent(recap.id)}`);
		else toasts.show(deck.none);
	}

	type Group = {
		id: string;
		size: 'large' | 'small';
		kind: 'one' | 'pair' | 'row';
		blocks: Block[];
	};
	const groups = $derived.by(() => {
		const out: Group[] = [];
		const pair: Group = { id: 'pair', size: 'small', kind: 'pair', blocks: [] };
		const row: Group = { id: 'row', size: 'small', kind: 'row', blocks: [] };
		for (const block of blocks) {
			const shared = SIDE_BY_SIDE.has(block.id) ? pair : SMALL.has(block.id) ? row : null;
			if (shared === null) {
				out.push({ id: block.id, size: 'large', kind: 'one', blocks: [block] });
				continue;
			}
			if (shared.blocks.length === 0) out.push(shared);
			shared.blocks.push(block);
		}
		/* The story's order (see the header): the pair of cards straight under the Overview, before
		   the lists. Any group not named keeps its place after them. */
		const rank = (group: Group) => {
			const at = STORY.indexOf(group.id);
			return at < 0 ? STORY.length : at;
		};
		return out.sort((a, b) => rank(a) - rank(b));
	});
</script>

<svelte:head><title>{INSIGHTS_WORDS.title}</title></svelte:head>

{#snippet stats()}
	<div class="presses">
		{#if deck}
			<Button size="small" tone="secondary" onclick={() => void openDeck()}>{deck.see}</Button>
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
		{:else if answer}
			<div class="answer" class:stale={loading} aria-busy={loading}>
				<Statements lines={lead} lead />
				{#each groups as group, order (`${arrival}-${group.id}`)}
					<div
						class="group"
						class:pair={group.kind === 'pair'}
						class:row={group.kind === 'row'}
						style:--order={order}
					>
						{#each group.blocks as block (block.id)}
							{#if group.kind === 'one'}
								<InsightsBlock
									{block}
									size="large"
									hero={block.id === 'overview'}
									lead={block.id === 'most_viewed'}
								/>
							{:else}
								<InsightsBlock
									{block}
									size="small"
									variant="card"
									ring={block.id === 'when'}
									lead={block.id === 'opinions'}
								/>
							{/if}
						{/each}
					</div>
				{/each}
			</div>
		{:else}
			<Skeleton lines={4} />
		{/if}

		<section aria-labelledby="insights-recaps">
			<SectionHeading id="insights-recaps">{INSIGHTS_WORDS.recaps}</SectionHeading>
			<RecapHeads limit={RECAPS_NAMED} />
		</section>
	</div>
</PageFrame>

<style>
	.insights,
	.answer {
		display: flex;
		flex-direction: column;
		gap: var(--space-8);
	}

	/* Each group rises into place after the one above it: the story arriving in its order. The
	   step is the quickest duration, so the last of seven is in by about half a second; reduced
	   motion draws every one immediately (`app.css` holds every animation to one instant pass). */
	.group {
		min-inline-size: 0;
		animation: rise var(--dur-slow) var(--ease) both;
		animation-delay: calc(var(--dur-instant) * var(--order, 0));
	}

	/* By kind and When side by side where there is room for both at a readable width. */
	.pair {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 28rem), 1fr));
		gap: var(--space-4);
	}

	/* The smaller blocks as a grid of cards, as many to a row as fit at a readable width. */
	.row {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(100%, 20rem), 1fr));
		align-items: start;
		gap: var(--space-4);
	}

	.pair {
		align-items: start;
	}

	.presses {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	/* The last period's figures while the next are on their way: still readable, plainly not current. */
	.stale {
		opacity: 0.55;
		transition: opacity var(--dur-instant) var(--ease);
	}
</style>
