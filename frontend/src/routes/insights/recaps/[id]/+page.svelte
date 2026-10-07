<script lang="ts">
	/*
	 * ONE RECAP: "September, in 8 cards", as a story read a card at a time.
	 *
	 * A page with an address, so a recap can be opened again next month from the Recaps list, from
	 * a link, or from the browser's own history, not a dialog that is gone once it is closed. The
	 * cards are read in order, one on screen at a time, the way a year in review is: the time
	 * viewed, the top person with her portrait, the top five, the top Site and tag, the favourite
	 * time as a ring, Theater, what was organized, and last the closing card ("That was
	 * September.") with the way to the same period on Insights under it.
	 *
	 * ## Paging
	 *
	 * The bar of segments at the head says how far through the story the reader is; Earlier and
	 * Later (and the arrow keys) turn a card, and the next one slides in from the side it comes
	 * from, at the slow pace, its figure counting up as it lands: the one card in view is the one
	 * thing moving. Every card is still in the page for a screen reader, as a slide of a carousel,
	 * the ones not in view hidden until turned to.
	 *
	 * ## A picture of a card
	 *
	 * Under the card, Save as picture draws the card as a picture (`share-card.ts`) and hands it to the
	 * door every screenshot takes. Offered only for a card that may be taken (`shareable`).
	 *
	 * ## What is decided here, and what is not
	 *
	 * Nothing about what is hidden. The server draws the recap for the reader's vault as it stands
	 * when it is asked: in the Show nothing mode a card that would name something hidden is not in
	 * the answer at all, in the placeholder mode it is a locked tile and the page carries the one
	 * line "Some of September is hidden. Unlock to include it." And the heading counts the cards
	 * actually sent. When the vault opens or shuts the recap is asked for again.
	 *
	 * Opening it ends its announcement, on Insights and on Browse's header at the same time: the
	 * server writes that when it draws the recap, and the shared list is told (`readRecap`).
	 *
	 * A recap is made to be shown a card at a time, and a card to be kept as a picture; what must
	 * not leave is kept back by `shareable` (see `RecapCard`).
	 */
	import { untrack } from 'svelte';

	import { page } from '$app/state';
	import { isMissing } from '$lib/api/client';
	import {
		BackButton,
		Button,
		Empty,
		Note,
		Problem,
		Skeleton,
		Tooltip
	} from '$lib/components/common';
	import type { Crumb } from '$lib/components/common';
	import RecapCard, { shareable } from '$lib/components/insights/RecapCard.svelte';
	import { shareCard, shareName, shareOf } from '$lib/components/insights/share-card';
	import { INSIGHTS_WORDS } from '$lib/components/insights/words';
	import { arrive } from '$lib/shell/motion.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { readRecap, type Recap } from '$lib/library/recaps.svelte';
	import { clock } from '$lib/shell/clock.svelte';

	/** The way to the same period on Insights, by the kind of period the recap is of. */
	const SEE_IT: Record<string, string> = {
		week: 'See this week in Insights',
		month: 'See this month in Insights',
		year: 'See this year in Insights'
	};

	const id = $derived(page.params.id ?? '');

	/* The card in view, and the side the last turn came from (1 forward, -1 back). */
	let at = $state(0);
	let turned = $state(1);
	let taking = $state(false);

	/** How far a card slides in from as it is turned to, in px: a card's width would be a lurch. */
	const TURN = 32;

	function turn(by: number): void {
		if (!recap) return;
		const next = Math.min(recap.cards.length - 1, Math.max(0, at + by));
		if (next === at) return;
		turned = by;
		at = next;
	}

	function onkeydown(event: KeyboardEvent): void {
		const target = event.target as HTMLElement | null;
		if (target?.closest('input, textarea, [contenteditable="true"]')) return;
		if (event.key === 'ArrowRight') turn(1);
		else if (event.key === 'ArrowLeft') turn(-1);
		else return;
		event.preventDefault();
	}

	const place = (index: number) => (recap ? `${index + 1} of ${recap.cards.length}` : '');

	async function take(): Promise<void> {
		const card = recap?.cards[at];
		if (!recap || !card || !shareable(card) || taking) return;
		taking = true;
		try {
			await shareCard(
				shareOf(card, recap.title, place(at), recap.span),
				shareName(recap.period, at)
			);
		} finally {
			taking = false;
		}
	}

	let recap = $state<Recap | null>(null);
	let missing = $state(false);
	let failed = $state(false);

	/* Answers are numbered so a slow one for an address already left cannot land on the next. */
	let asked = 0;

	async function read(which: string): Promise<void> {
		const mine = ++asked;
		try {
			const got = await readRecap(which);
			if (mine !== asked) return;
			recap = got;
			at = Math.min(at, Math.max(0, got.cards.length - 1));
			missing = false;
			failed = false;
		} catch (error) {
			if (mine !== asked) return;
			if (isMissing(error)) {
				recap = null;
				missing = true;
			} else {
				failed = true;
			}
		}
	}

	$effect(() => {
		const which = id;
		untrack(() => {
			recap = null;
			at = 0;
			missing = false;
			failed = false;
			void read(which);
		});
	});

	/* The vault opened or shut, or something was hidden or shared: which cards this reader is shown
	   has moved, and only the server knows what it moved to. */
	reloadOnLibraryChange(() => void read(id));

	/* The cards say a time of day on the reader's clock, so a change of clock asks again. */
	let clockSeen = untrack(() => clock.hours);
	$effect(() => {
		const now = clock.hours;
		if (now === clockSeen) return;
		clockSeen = now;
		untrack(() => void read(id));
	});

	/* The same period on Insights: a recap of a period says which, and its first day is inside it. */
	const kind = $derived(recap?.period.split(':')[0] ?? '');
	const onInsights = $derived(
		recap && recap.first_day && SEE_IT[kind]
			? {
					label: SEE_IT[kind],
					href: `/insights?period=${kind}&at=${encodeURIComponent(recap.first_day)}`
				}
			: null
	);

	const crumbs = $derived<Crumb[]>([
		{ label: 'Insights', href: '/insights' },
		{ label: 'Recaps', href: '/insights/recaps' },
		{ label: recap?.title ?? 'Recap' }
	]);
</script>

<svelte:head><title>{recap?.title ?? 'Recap'}</title></svelte:head>

<PageFrame {crumbs}>
	{#snippet header()}
		<PageHeader title={recap?.heading ?? 'Recap'}>
			{#snippet lede()}{recap?.span ?? ''}{/snippet}
		</PageHeader>
	{/snippet}

	{#if missing}
		<Empty scope="page">There's no recap here.</Empty>
		<BackButton to="/insights" label="Insights" />
	{:else if failed}
		<Problem message="This recap couldn't be opened." />
	{:else if !recap}
		<Skeleton shape="block" />
	{:else}
		<div class="recap">
			{#if recap.hidden_line}
				<Note icon="visibility_off">{recap.hidden_line}</Note>
			{/if}

			<!-- The arrow keys turn a card while the story has the focus: the carousel's own keys, not
			     an app-wide shortcut. -->
			<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
			<section
				class="story"
				aria-roledescription="carousel"
				aria-label={recap.heading}
				tabindex="0"
				{onkeydown}
			>
				<div class="progress" aria-hidden="true">
					{#each recap.cards as card, index (`${card.id}-${index}`)}
						<span class="segment" class:read={index <= at}></span>
					{/each}
				</div>
				<ol class="cards">
					{#each recap.cards as card, index (`${card.id}-${index}`)}
						<li
							role="group"
							aria-roledescription="slide"
							aria-label={place(index)}
							hidden={index !== at}
						>
							{#if index === at}
								<div class="turned" in:arrive={{ x: turned * TURN, pace: 'slow' }}>
									<RecapCard {card} heading={recap.title} place={place(index)} />
								</div>
							{/if}
						</li>
					{/each}
				</ol>
				<div class="controls">
					<Tooltip label={INSIGHTS_WORDS.earlier}>
						<Button
							icon="chevron_left"
							tone="ghost"
							aria-label={INSIGHTS_WORDS.earlier}
							disabled={at === 0}
							onclick={() => turn(-1)}
						/>
					</Tooltip>
					<Tooltip label="A picture of this card, where your screenshots go">
						<Button
							icon="save"
							tone="ghost"
							disabled={!shareable(recap.cards[at]) || taking}
							onclick={() => void take()}>Save as picture</Button
						>
					</Tooltip>
					<Tooltip label={INSIGHTS_WORDS.later}>
						<Button
							icon="chevron_right"
							tone="ghost"
							aria-label={INSIGHTS_WORDS.later}
							disabled={at === recap.cards.length - 1}
							onclick={() => turn(1)}
						/>
					</Tooltip>
				</div>
			</section>

			{#if onInsights}
				<a class="see-it" href={onInsights.href}>{onInsights.label}</a>
			{:else}
				<BackButton to="/insights" label="Insights" />
			{/if}
		</div>
	{/if}
</PageFrame>

<style>
	.recap {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-4);
		max-inline-size: var(--page-measure);
	}

	/* The story: its progress, the one card in view, and the controls under it, centred. */
	.story {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-4);
	}

	.progress {
		display: flex;
		gap: var(--space-1);
		inline-size: min(100%, var(--story-width));
	}

	/* A card's segment: faint until it has been read, then the accent's tint, over the fast pace. */
	.segment {
		flex: 1;
		block-size: var(--chart-mark-gap);
		border-radius: var(--radius-sm);
		background: var(--sift-surface-4);
		transition: background-color var(--dur-fast) var(--ease);
	}

	.segment.read {
		background: var(--sift-accent-tint-1);
	}

	.cards {
		display: grid;
		justify-items: center;
		inline-size: 100%;
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.turned {
		display: grid;
		justify-items: center;
		inline-size: 100%;
	}

	.controls {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* The way on, in the link face every quiet way on in Insights wears. */
	.see-it {
		inline-size: fit-content;
		border-radius: var(--radius-sm);
		font: var(--text-body);
		color: var(--sift-accent-text);
		text-decoration: underline;
		text-decoration-color: transparent;
		text-underline-offset: 3px;
		transition: text-decoration-color var(--dur-instant) var(--ease);
	}

	.see-it:hover,
	.see-it:focus-visible {
		text-decoration-color: currentColor;
	}
</style>
