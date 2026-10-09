<script lang="ts">
	import { Empty, Problem, Skeleton } from '$lib/components/common';
	/* The wall of cards and its toolbar: a plain CSS grid, since entities are dozens at one
	   shape and need none of the media grid's virtualising. */
	import type { Snippet } from 'svelte';
	import type { Crumb } from '$lib/components/common/Breadcrumbs.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageAbove from '$lib/components/shell/PageAbove.svelte';
	import type { IconName } from '$lib/design/icons';
	import { gridSize } from '$lib/grid/grid.svelte';
	import { cardWidthForStep } from '$lib/grid/justify';

	interface Props {
		title: string;
		/** The trail above this wall, handed to the frame's band. See `PageFrame.crumbs`. */
		crumbs?: Crumb[];
		/** The rail's glyph for this screen, so the row pressed and the screen opened agree. */
		icon: IconName;
		/**
		 * What to say when there is nothing; per screen, since "nobody yet" is not "nothing
		 * matched".
		 */
		empty: string;
		/** How many cards this page draws now; not the total, which lags a new row. */
		drawn: number;
		/** How many there are altogether, for the title's badge; defaults to `drawn`. */
		total?: number;
		loading?: boolean;
		/** The page as asked for; the entrance replays only when it changes. */
		page?: string | null;
		failed?: string | null;
		/** The cards are the last answer, kept quiet and out of reach while the next one comes. */
		held?: boolean;
		/** A control that is part of the title (an entity page's tabs), drawn just after it. */
		beside?: Snippet;
		/** Let `beside` name the section instead of the title. See `PageHeader.titleHidden`. */
		titleHidden?: boolean;
		/** What the wall is doing, after the count. See `PageHeader.status`. */
		status?: Snippet;
		/** A band above the title, for a wall embedded in a page about something. */
		above?: Snippet;
		/** The screen's own controls: a search box, an add form. */
		controls?: Snippet;
		/** A sentence under the title. See PageHeader. */
		lede?: Snippet;
		children: Snippet;
		/** The screen's pager, drawn in one place under every wall. */
		pager?: Snippet;
		/** Attached to the wall, so the screen's paging can measure a real card. */
		measure?: (element: HTMLElement) => (() => void) | void;
		/** Floats over the wall (the selection bar), in the frame's floating slot. */
		floating?: Snippet;
		/**
		 * Drawn inside a screen with its own frame, as an Organize queue is: one frame per screen.
		 */
		inside?: boolean;
	}

	let {
		title,
		icon,
		beside,
		titleHidden = false,
		status,
		above,
		crumbs,
		empty,
		drawn,
		total,
		loading = false,
		page,
		failed = null,
		held = false,
		controls,
		lede,
		children,
		pager,
		measure,
		floating,
		inside = false
	}: Props = $props();

	/** The count for the heading's badge. */
	const counted = $derived(total ?? drawn);

	/* The size slider's chosen width; null keeps the responsive floor and stretching. */
	const chosenWidth = $derived(cardWidthForStep(gridSize.step));
</script>

<!-- The one page shape, from PageFrame. The wall eases in on each new answer; the pager goes
to the frame's footer, absent when the screen pages nothing. -->

{#snippet wall()}
	{#if failed}
		<Problem message={failed} />
	{:else if loading && drawn === 0}
		<!-- The cards' own footprint while loading: one busy region, the placeholders hidden. -->

		<div class="wall waiting" role="status" aria-busy="true" aria-label="Loading">
			{#each Array.from({ length: 12 }, (_, at) => at) as at (at)}
				<div class="ghost">
					<div class="ghost-face">
						<Skeleton shape="block" />
					</div>
					<Skeleton shape="text" lines={2} />
				</div>
			{/each}
		</div>
	{:else if drawn === 0}
		<!-- The wall's own glyph, so an empty wall reads as this one's. -->
		<Empty scope="page" {icon}>{empty}</Empty>
	{:else}
		<div
			class="wall"
			class:sized={chosenWidth !== null}
			class:held
			inert={held}
			aria-busy={held || undefined}
			style={chosenWidth === null ? undefined : `--card-width: ${chosenWidth}px`}
			{@attach (element) => measure?.(element)}
		>
			{@render children()}
		</div>
	{/if}
{/snippet}

{#if inside}
	{@render wall()}
{:else}
	<PageFrame
		{crumbs}
		arrival={() => (page !== undefined ? page : loading ? null : 'drawn')}
		footer={pager}
		{floating}
	>
		{#snippet header()}
			{#if above}
				<PageAbove>{@render above()}</PageAbove>
			{/if}
			<PageHeader
				{title}
				{icon}
				count={counted}
				{beside}
				{titleHidden}
				{status}
				{controls}
				{lede}
			/>
		{/snippet}

		{@render wall()}
	</PageFrame>
{/if}

<style>
	/* The placeholder: an EntityCard's 2:3 picture and two lines of words. */
	.ghost {
		display: grid;
		gap: var(--space-2);
		padding-block-end: var(--space-2);
	}

	.ghost-face {
		aspect-ratio: 2 / 3;
	}

	/* auto-fill, so a lone card is not stretched to the window; min() so a column never
	overflows the wall. */

	.wall {
		/* Room for the top row's hover lift, which the scroller would clip. */
		padding-block-start: var(--space-1);
		--card-floor: 260px;
		/* Declared as well as set inline: an undefined property would drop the whole track. */
		--card-width: var(--card-floor);
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(var(--card-floor), 100%), 1fr));
		gap: var(--space-4);
	}

	/* A chosen notch is a floor, so the cards fill the row with no hole at its end. */
	.wall.sized {
		grid-template-columns: repeat(auto-fill, minmax(min(var(--card-width), 100%), 1fr));
	}

	/* Quieter while loading, so it reads as filling in. */
	.wall.waiting {
		opacity: 0.6;
	}

	/* The held answer dims after a beat, so a quick answer swaps with no dimming. */
	.wall {
		transition: opacity var(--dur-fast) var(--ease);
	}

	.wall.held {
		opacity: 0.6;
		transition: opacity var(--dur-base) var(--ease) var(--dur-fast);
	}

	/* Three columns on a phone by default: two make a card taller than half the screen. */
	@media (max-width: 640px) {
		.wall {
			--card-floor: 104px;
			gap: var(--space-3);
		}
	}
</style>
