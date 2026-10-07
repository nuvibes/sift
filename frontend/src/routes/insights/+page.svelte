<script lang="ts">
	/*
	 * INSIGHTS: what you viewed, organized and added, for a day, a week, a month, a year or all of it.
	 *
	 * ## Who says what
	 *
	 * The server says everything that is a sentence or a figure (`GET /api/insights`,
	 * `slices/insights/router.py`): the first sentences, and each block in its fixed order with its
	 * title, figures and their captions, chart, heat-map and lists, or, below its floor, "Not enough
	 * yet to say." This screen composes no sentence; it lays the answer out. A block the server did
	 * not send is not drawn: What Sift did is only ever in an admin's answer, so a guest's screen has
	 * no such heading because nothing here could put one there.
	 *
	 * ## How the answer is laid out
	 *
	 * As a story told in the order a person asks it, one statement a card, the figure large:
	 *
	 * * THE OVERVIEW OPENS IT: the period's first sentence as the page's headline, the time viewed as
	 *   the one figure the page leads with (the figure size, on the accent's run, its trend along its
	 *   foot), the other figures beside it with theirs, and the chart of the period on a card under
	 *   them with its heat-map.
	 * * WHEN AND BY KIND, side by side as two cards: the hours as the hour ring (a favourite time of
	 *   day read the way a clock is) and the time by kind as one whole in its shares.
	 * * MOST VIEWED: each list leads with its number one, its picture large, the rest ranked under
	 *   it, the way a chart of the week leads with its first place.
	 * * THE REST AS A GRID OF CARDS (Theater, Visits, Opinions, Organizing, what arrived, What Sift
	 *   did), each saying its one statement large over its figures.
	 *
	 * The groups arrive one after another when a period's answer does, each rising on the page's own
	 * arrival motion, a step of `--dur-instant` apart: the story told in its order. A block below its
	 * floor is left out
	 * rather than drawn as seven identical "Not enough yet to say." lines: while the viewing is
	 * below its floor, the page opens on the first sentences instead, which say what there is (or,
	 * for somebody new, that Sift is keeping count), and they are drawn nowhere else, because each
	 * of them is a figure already on a card.
	 *
	 * ## A period is a place
	 *
	 * The tabs are links and the arrows move the address (`period`, `at`), so Back goes to the period
	 * before and a period can be kept as a bookmark. Where the period starts and ends is the server's
	 * answer (`from`, `to`), never worked out here: the device's calendar decides the days.
	 *
	 * ## Nothing waits on anything else
	 *
	 * The heading and the tabs draw at once. The figures arrive into the page; the recap
	 * announcement and the recaps read their own answers and draw when those arrive. None of them
	 * waits for another, and a period changing keeps the last figures on screen, dimmed, until the
	 * next ones are in, so the page does not jump to a skeleton and back.
	 *
	 * The recaps are drawn by their own component rather than from the block of that name in this
	 * answer: it carries more than the answer's summary does (a recap's days and cards), and it is
	 * the one drawing of a recap wherever one is listed. Your path is under Settings, in Get to know
	 * Sift, and not drawn here.
	 */
	import { goto } from '$app/navigation';

	import { api } from '$lib/api/client';
	import { Button, Problem, SectionHeading, Skeleton, Tooltip } from '$lib/components/common';
	import Tabs from '$lib/components/common/Tabs.svelte';
	import InsightsBlock from '$lib/components/insights/InsightsBlock.svelte';
	import RecapAnnouncement from '$lib/components/insights/RecapAnnouncement.svelte';
	import RecapHeads from '$lib/components/insights/RecapHeads.svelte';
	import Statements from '$lib/components/insights/Statements.svelte';
	import {
		addressOf,
		stepsFrom,
		tabsFor,
		type InsightsBlock as Block,
		type InsightsPage,
		type Place
	} from '$lib/components/insights/period';
	import { drawsAnything } from '$lib/components/insights/figures';
	import { INSIGHTS_WORDS } from '$lib/components/insights/words';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { calendarDay } from '$lib/shell/when';
	import { clock } from '$lib/shell/clock.svelte';
	import { onAssetStateChange, reloadOnLibraryChange } from '$lib/library/changes.svelte';

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

	let answer = $state<InsightsPage | null>(null);
	/* Bumped when a period's answer lands, never by a re-read: the groups arrive (and count) on it. */
	let arrival = $state(0);
	let failed = $state(false);
	let loading = $state(true);

	$effect(() => {
		const period = data.period;
		const at = data.at;
		/* The server says a time of day on the reader's clock, so a change of clock in Appearance
		   asks for the page again rather than leaving its sentences on the old one. */
		void clock.hours;
		/* An answer to a period somebody has already left is dropped, not drawn over the newer one. */
		let current = true;
		loading = true;
		failed = false;
		api
			.get<InsightsPage>('/insights', { query: { period, at: at ?? undefined } })
			.then((found) => {
				if (!current) return;
				answer = found;
				arrival += 1;
				loading = false;
			})
			.catch(() => {
				if (!current) return;
				failed = true;
				loading = false;
			});
		return () => {
			current = false;
		};
	});

	/* A change elsewhere re-reads in place: the page as drawn stays until a different answer lands.
	   One read at a time, and one more after it if a bell rang meanwhile. */
	let rereading = false;
	let rereadOwed = false;
	async function rereadQuietly(): Promise<void> {
		if (rereading) {
			rereadOwed = true;
			return;
		}
		rereading = true;
		const period = data.period;
		const at = data.at;
		try {
			const found = await api.get<InsightsPage>('/insights', {
				query: { period, at: at ?? undefined }
			});
			if (loading || period !== data.period || at !== data.at) return;
			if (JSON.stringify(found) !== JSON.stringify(answer)) answer = found;
		} catch {
			// The page as drawn stays.
		} finally {
			rereading = false;
			if (rereadOwed) {
				rereadOwed = false;
				void rereadQuietly();
			}
		}
	}
	reloadOnLibraryChange(() => void rereadQuietly());
	onAssetStateChange(() => void rereadQuietly());

	const tabs = $derived(tabsFor(data));
	const steps = $derived(answer && !loading ? stepsFrom(answer) : { earlier: null, later: null });
	/* A block below its floor, or past it with nothing to draw, is left out: a heading over nothing
	   is an empty box on the page (see `drawsAnything`). */
	const blocks = $derived(
		answer?.blocks.filter(
			(block) => !DRAWN_ELSEWHERE.has(block.id) && block.floor_reached && drawsAnything(block)
		) ?? []
	);
	/* The viewing below its floor: the page opens on what there is instead (see the header). */
	const overview = $derived(answer?.blocks.find((block) => block.id === 'overview') ?? null);
	const lead = $derived(
		answer === null || overview?.floor_reached
			? []
			: answer.first_sentences.length > 0
				? answer.first_sentences
				: (overview?.statements ?? [])
	);
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
	/* The days the answer covers, as every date on screen is written (`$lib/shell/when`). */
	const days = $derived(
		answer === null
			? ''
			: answer.from === answer.to
				? calendarDay(answer.from)
				: `${calendarDay(answer.from)} \u2014 ${calendarDay(answer.to)}`
	);

	function go(place: Place | null) {
		if (place) void goto(addressOf(place));
	}
</script>

<svelte:head><title>{INSIGHTS_WORDS.title}</title></svelte:head>

<PageFrame>
	{#snippet header()}
		<PageHeader title={INSIGHTS_WORDS.title} icon="insights">
			{#snippet lede()}{INSIGHTS_WORDS.headLine}{/snippet}
		</PageHeader>
	{/snippet}
	{#snippet tools()}
		<div class="periods">
			<Tabs {tabs} current={data.period} label={INSIGHTS_WORDS.periods} />
			{#if data.period !== 'all'}
				<div class="steps">
					<Tooltip label={INSIGHTS_WORDS.earlier}>
						<Button
							icon="chevron_left"
							size="small"
							tone="ghost"
							aria-label={INSIGHTS_WORDS.earlier}
							disabled={steps.earlier === null}
							onclick={() => go(steps.earlier)}
						/>
					</Tooltip>
					<span class="days" class:stale={loading} aria-live="polite">{days}</span>
					<Tooltip label={INSIGHTS_WORDS.later}>
						<Button
							icon="chevron_right"
							size="small"
							tone="ghost"
							aria-label={INSIGHTS_WORDS.later}
							disabled={steps.later === null}
							onclick={() => go(steps.later)}
						/>
					</Tooltip>
				</div>
			{:else if days}
				<span class="days">{days}</span>
			{/if}
		</div>
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
	.periods {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-4);
	}

	/* On a phone the steps wrap under the periods, and a finger's reach round each needs the two
	   lines further apart than a desk does, or a press just under "Week" would go to Earlier. */
	@media (max-width: 767px) {
		.periods {
			row-gap: var(--space-4);
		}
	}

	.steps {
		display: flex;
		align-items: center;
		gap: var(--space-1);
	}

	.days {
		font: var(--text-label);
		color: var(--sift-ink-2);
		white-space: nowrap;
	}

	.insights,
	.answer {
		display: flex;
		flex-direction: column;
		gap: var(--space-8);
	}

	/* Each group rises into place after the one above it: the story arriving in its order. The
	   step is the quickest duration, so the last of seven is in by about half a second; reduced
	   motion draws every one at once (`app.css` holds every animation to one instant pass). */
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

	/* The last period's figures while the next are on their way: still readable, plainly not current. */
	.stale {
		opacity: 0.55;
		transition: opacity var(--dur-instant) var(--ease);
	}
</style>
