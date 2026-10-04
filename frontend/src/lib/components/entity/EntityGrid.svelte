<script lang="ts">
	import { Empty, Problem, Skeleton } from '$lib/components/common';
	/*
	 * The wall the cards sit in, and the toolbar over it.
	 *
	 * Plain CSS grid with a minimum card width, deliberately: not the justified, virtualised
	 * machinery the media grid uses. That machinery exists because a library is tens of thousands
	 * of items at true aspect ratios, and every part of it is a cost paid for that. A library has
	 * dozens of people and a handful of sites, at one fixed shape. Reusing the grid here would be
	 * importing a solution to a problem this screen does not have.
	 */
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
		/** What to say when there is nothing. Written per screen: "nobody yet" and "nothing matched"
		 *  are different facts and only the screen knows which one it is looking at. */
		empty: string;
		/**
		 * How many cards this wall is DRAWING, right now, on this page.
		 *
		 * Not how many exist. The scoped TOTAL is a different number and is zero at exactly the
		 * wrong moment: a store adds a newly created row to the page it is holding and has nothing
		 * to say about the total, so the first tag or the first collection on an install would be
		 * drawn under "No tags yet": 201 from the server, a row in the store, and an empty screen.
		 *
		 * The total belongs to the pager, which takes it separately. This is the wall's own count,
		 * and it is named for that so the two cannot be swapped.
		 */
		drawn: number;
		/**
		 * How many there are ALTOGETHER, for the number beside the title.
		 *
		 * A different question from the one above and it deserves a different prop: the badge on the
		 * heading says how big the library is, and the wall below it says how much of that is on this
		 * page. One number cannot answer both once a wall pages.
		 *
		 * Optional, falling back to what is drawn, for a wall that does not page.
		 */
		total?: number;
		loading?: boolean;
		/**
		 * Which page is on screen, as it was ASKED for: a paging wall hands over `paging.showing`.
		 *
		 * The wall plays its entrance when this changes and at no other time. Not the total: a re-read
		 * that finds one more thing (every library change while a task runs) is the same page, and
		 * replaying the arrival for it would make the whole wall flash. A wall that hands nothing
		 * over plays it once, when it first has something to draw.
		 */
		page?: string | null;
		failed?: string | null;
		/**
		 * The cards drawn are the LAST answer, kept while the next one is on its way: a tab
		 * changed, a page turned. They stay where they are, quieter and out of reach, so the wall
		 * never goes empty between two answers; a press on a card that is about to be replaced
		 * would act on the wrong thing.
		 */
		held?: boolean;
		/**
		 * A control belonging to the TITLE, drawn immediately after it.
		 *
		 * Different from `controls`, which is the row of things a screen can do and sits at the far
		 * end. This is for the rare control that is part of what the heading MEANS: an entity
		 * page's tabs, which decide what the word says and what is under it. Forwarded straight to
		 * `PageHeader`'s own slot.
		 */
		beside?: Snippet;
		/** Let `beside` name the section instead of the title. See `PageHeader.titleHidden`. */
		titleHidden?: boolean;
		/**
		 * A band between the frame's top and the title, for a wall EMBEDDED in a page about something.
		 *
		 * The entity page's identity band: a name, a cover, the breadcrumb. Inside the frame's
		 * header rather than above the frame, so the page keeps one scrolling region and the wall is
		 * measured against a box whose height is real. The same slot, in the same place, that
		 * `AssetGrid` has; without it every tab but Files would lose the name of the thing the
		 * page is about.
		 */
		above?: Snippet;
		/** The screen's own controls: a search box, an add form. */
		controls?: Snippet;
		/** A sentence under the title. Passed straight through; see PageHeader for why it lives
		 *  in the header rather than on the page. */
		lede?: Snippet;
		children: Snippet;
		/**
		 * The wall's pager, drawn under the cards.
		 *
		 * A slot rather than something this builds, because only the screen knows what it is paging
		 * and how far along it is, but it goes HERE rather than after the wall in each screen's own
		 * markup, so every wall puts it in the same place.
		 */
		pager?: Snippet;
		/**
		 * Attached to the wall itself, so a screen can measure the cards it is drawing.
		 *
		 * That is how a page comes to hold whole rows of the window rather than a fixed count chosen
		 * once: `CardPaging` reads a real card off this element and works out how many fit. Passed in
		 * rather than done here because the paging belongs to the screen, which is what fetches.
		 */
		measure?: (element: HTMLElement) => (() => void) | void;
		/** What floats over the wall while it is used: the selection bar. Handed to the frame's own
		    floating slot, so it is positioned against the screen and clears the pager. */
		floating?: Snippet;
		/**
		 * Drawn INSIDE a screen that already has its frame: the wall and its states, no frame, no
		 * title, no pager of its own.
		 *
		 * An Organize queue is one: the route draws the trail, the queue's title, its tabs and the
		 * pager in its own frame, and a wall that drew a second frame inside it would put a second
		 * title under the first and a second scrolling box inside the first. The inner box would
		 * never scroll (the outer one does), so it would grow to the height of every card, and
		 * WebKit can composite that one tall layer as a black box over the top bar's corner on a
		 * phone. One frame per screen.
		 */
		inside?: boolean;
	}

	let {
		title,
		icon,
		beside,
		titleHidden = false,
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

	/** How many there are, for the badge on the heading, and what is on the wall when a screen does
	 *  not page and has nothing else to say. */
	const counted = $derived(total ?? drawn);

	/*
	 * The size slider, on a wall of cards. It means how big the things on this screen are drawn,
	 * which a wall of cards can answer as well as the media grid: the same control on the same four
	 * notches, with each notch's column width from `cardWidthForStep`.
	 *
	 * Null while nobody has chosen: the wall keeps its own responsive floor (a rule in the
	 * stylesheet below, with a breakpoint) and the cards stretch to fill the window. Choosing a
	 * notch switches stretching off entirely; see `.wall.sized`, and `cardWidthForStep` for why.
	 */
	const chosenWidth = $derived(cardWidthForStep(gridSize.step));
</script>

<!--
	The one page shape, from the one component: a wall is a header, a body that scrolls and a pager,
	like every other screen (see `PageFrame`), so every wall starts at the same inset.

	The wall eases in when it arrives, and again whenever it has gone and come back (a page turn, a
	search, a re-read after a rename). Null while a read is in flight, so the fade lands on the
	answer. See `PageFrame.arrival`.

	The pager is handed straight to the frame's footer, where the asset wall's is too, so it sits in
	one place whatever the screen shows: neither sticky inside the scroller with cards passing under
	it, nor the last thing in the content, mid-screen on a short wall. (See `PageFrame` on why the
	footer track causes no request loop.) Passed as the prop rather than wrapped in a snippet, so a
	screen with nothing to page hands over nothing, the frame draws no footer, and the track
	collapses.
-->
{#snippet wall()}
	{#if failed}
		<Problem message={failed} />
	{:else if loading && drawn === 0}
		<!--
			The shape of the wall, before the wall.

			Not the word "Loading...", which tells somebody nothing they had not already worked out
			and then hands them a screen that jumps from one line of text to forty cards. These
			are the cards' own footprint, so the arrival is the pictures filling in rather than the
			page being rebuilt around them.

			One busy region for the whole wall. Each placeholder is hidden from a screen reader on
			purpose: twelve of them each announcing themselves is twelve interruptions describing
			one wait.
		-->
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
		<!-- The wall's own glyph, the rail's for this entity: under one shared tray an empty People
		     wall reads as every wall's empty state rather than as this one's. -->
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
			<PageHeader {title} {icon} count={counted} {beside} {titleHidden} {controls} {lede} />
		{/snippet}

		{@render wall()}
	</PageFrame>
{/if}

<style>
	/* The placeholder card: the picture's 2:3 box and two lines of words under it, which is exactly
	   what an EntityCard is. Written here rather than in Skeleton, because the SHAPE of the thing
	   being waited for belongs to whoever knows what is coming. */
	.ghost {
		display: grid;
		gap: var(--space-2);
		padding-block-end: var(--space-2);
	}

	.ghost-face {
		aspect-ratio: 2 / 3;
	}

	/* `auto-fill` rather than `auto-fit`: with `auto-fit` a single card stretches to the full width
	   of the window, which for a portrait card means one enormous face. `auto-fill` keeps the empty
	   columns, so the first card is the same size as it will be when there are twenty.

	   `min()` against the container so a chosen notch can never be wider than the wall itself: at
	   the largest notch on a phone a fixed floor would demand a column wider than the screen, and a
	   grid track that cannot fit does not shrink: it overflows, and the wall scrolls sideways. */
	.wall {
		/*
		 * Room for the hover lift, at the top and nowhere else.
		 *
		 * A card lifts `translateY(-2px)` with a 1px ring, the Lift register for anything
		 * picture-shaped. The scrolling body is `overflow: hidden scroll` and this wall begins
		 * exactly at its top edge, so the top row's lift would be clipped by a few pixels. The
		 * media grid's tiles scale the picture inside their own frame and never leave the box. The
		 * frame's other three sides already have the page padding; the top has none on purpose,
		 * because the header sits against it and the browser tests measure from there.
		 */
		padding-block-start: var(--space-1);
		--card-floor: 260px;
		/* Declared here as well as written inline by the script, and the declaration is what makes it
		   safe rather than tidy: a custom property that was never defined is not an error in CSS, the
		   whole declaration using it is discarded, and the track quietly falls back to `none`: one
		   column, every card the width of the wall. The inline value overrides this whenever a notch
		   has been chosen, and this is what stands if it ever does not arrive. */
		--card-width: var(--card-floor);
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(var(--card-floor), 100%), 1fr));
		gap: var(--space-4);
	}

	/*
	 * A notch was chosen, so the cards are at LEAST that wide and then fill the row.
	 *
	 * A fixed track (`repeat(auto-fill, var(--card-width))`) would make a notch mean one exact size
	 * at every window width, and would also leave a band of empty ground down the right of every
	 * entity wall, as wide as whatever the columns did not divide into: on a very wide window,
	 * hundreds of pixels of nothing. Browse never does that, because a justified row is scaled to
	 * span the container exactly.
	 *
	 * The two cannot both be had on a wrapping grid. `auto-fill` fits a whole number of columns, and
	 * the remainder either goes into the columns (they fill the width, and the size is approximate)
	 * or sits at the end of the row (the size is exact, and there is a hole). Filling the width is
	 * the one somebody can see from across the room, so the notch is a FLOOR here rather than a
	 * measurement. See `cardWidthForStep`, which says what that costs.
	 *
	 * `min()` against the container so a large notch on a small window still fits: a track that
	 * cannot fit does not shrink, it overflows, and the wall would scroll sideways.
	 */
	.wall.sized {
		grid-template-columns: repeat(auto-fill, minmax(min(var(--card-width), 100%), 1fr));
	}

	/* The wall while the cards are still coming. Quieter than the real one, so a page mid-load reads
	   as filling in rather than as finished and washed out. Without this rule the placeholders
	   would be drawn at full strength, and nothing would say they were placeholders except the
	   `aria-busy` nobody can see. */
	.wall.waiting {
		opacity: 0.6;
	}

	/* The last answer while the next is coming: as quiet as the placeholders, after one fast beat,
	   so an answer that lands at once swaps the cards with no dimming at all, and a slower one says
	   it is coming. Back to full strength on the quicker pace, as everything arriving is. */
	.wall {
		transition: opacity var(--dur-fast) var(--ease);
	}

	.wall.held {
		opacity: 0.6;
		transition: opacity var(--dur-base) var(--ease) var(--dur-fast);
	}

	/*
	 * The floor only, so a notch chosen on the slider still wins here: the inline property is on
	 * the element and beats this rule. What this changes is the UNCHOSEN default, which is the one
	 * the phone actually needs: three columns.
	 *
	 * Two columns make a card taller than half a phone's screen, so one row fills a screenful and
	 * the page, which is whole rows times four screenfuls (see `CardPaging.size`), holds only 8.
	 * The page is measured and stays measured; this changes what there is to measure. At three
	 * columns two rows fit a phone's screen and the page holds two or three times as many, and the
	 * name, the count and the heart and star still sit on a card about 107 wide.
	 */
	@media (max-width: 640px) {
		.wall {
			--card-floor: 104px;
			gap: var(--space-3);
		}
	}
</style>
