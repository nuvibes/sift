<script lang="ts">
	/*
	 * The shape every screen in Sift has: header, an optional strip of tools, and a body that
	 * scrolls, so nothing shifts between screens.
	 *
	 * ```
	 *   +--------------------------------------+
	 *   | trail       (auto, a phone's only)   |   the breadcrumbs; on a desk they are in the top bar
	 *   | header      (auto)                   |   PageHeader, the same on every screen
	 *   | tools       (auto, optional)         |   filters, tabs, a search box
	 *   | body        (1fr, scrolls)           |   the only thing that scrolls
	 *   | footer      (auto, optional)         |   the pager, in one place on every screen
	 *   +--------------------------------------+
	 * ```
	 *
	 * Every header starts on one line under the top bar, where Theater starts. The footer holds the
	 * pager in one place at every page length; it is `auto` over a pager of fixed height, so the
	 * body is still the window less four constants. One scroll region per view. A positioned
	 * container, so floating bars anchor to the screen, not the window the rail takes 208px from.
	 */
	import { type Snippet } from 'svelte';

	import Breadcrumbs, { type Crumb } from '$lib/components/common/Breadcrumbs.svelte';
	import Scroller from '$lib/components/common/Scroller.svelte';
	import { appears } from '$lib/shell/motion.svelte';
	import { ownsTheBackdrop } from './backdrop';
	import { scrollingBody } from './page-scroll';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { pageTrail } from './trail.svelte';

	interface Props {
		/**
		 * The way here: said to the top bar on a desk (`pageTrail`), drawn here as its own band on a
		 * phone. One crumb is where you are and is shown nowhere.
		 */
		crumbs?: Crumb[];
		/** The title row. Nearly always a `PageHeader`; a snippet so a screen can compose its own. */
		header?: Snippet;
		/** A strip under the header: filters, tabs, a search box. Takes the height of its contents. */
		tools?: Snippet;
		/** The screen itself. The only part that scrolls. */
		children: Snippet;
		/**
		 * A row under the body, in the same place on every screen: the pager. Outside the scrolling
		 * box but inside the frame, so it lines up with the title; absent, the track collapses.
		 */
		footer?: Snippet;
		/**
		 * Let the body run to the edges instead of padding it, for a screen whose content IS the edge:
		 * the justified grid.
		 */
		bleed?: boolean;
		/**
		 * Cap the body's width and centre it: reading has a maximum measure and media fills, so it is
		 * opt-in per screen.
		 */
		measure?: boolean;
		/**
		 * Let the body's content box be the whole scrolling region, even when there is less in it, for
		 * a screen whose GROUND is operable (the folder view's context menu on its empty space). It
		 * only grows the box to the height it already scrolls in.
		 */
		fillBody?: boolean;
		/** Marks the region for a screen reader, where the frame is not the whole page. */
		label?: string;
		/**
		 * Attached to the scrolling body, for a screen that has to measure it (the grid's screenful),
		 * so no screen nests a scroller of its own. Two elements: the viewport that scrolls, without
		 * padding, and the content box inside it with all of it; measuring the wrong one gives rows an
		 * inset too wide.
		 */
		onbody?: (parts: { scroller: HTMLElement; content: HTMLElement }) => (() => void) | void;
		/**
		 * What is on screen right now, so the body eases in each time that changes. A paging screen
		 * returns the page somebody ASKED for, null before the first; never a count or offset, which
		 * quiet re-reads move. See `motion.svelte.ts`.
		 */
		arrival?: () => unknown;
		/**
		 * Something that floats over the screen rather than sitting in it: the selection bar. Outside
		 * the box the arrival animates, since a transform would make that box the bar's containing
		 * block mid-animation. Only a screen that re-animates with a bar open (the wall) needs it.
		 */
		floating?: Snippet;
	}

	let {
		crumbs,
		header,
		tools,
		children,
		footer,
		bleed = false,
		measure = false,
		fillBody = false,
		label,
		onbody,
		arrival,
		floating
	}: Props = $props();

	/* A trail with somewhere to go: one crumb is where you are, not a way back. */
	const trailed = $derived(crumbs !== undefined && crumbs.length > 1);

	/* On a phone a band of this frame's; on a desk the top bar's, so no band pushes the header down. */
	const banded = $derived(trailed && phoneWidth.yes);
	const me = Symbol('frame');
	$effect(() => {
		if (!trailed || phoneWidth.yes || !crumbs) return;
		pageTrail.say(me, crumbs);
		return () => pageTrail.unsay(me);
	});

	let scroller = $state<HTMLElement | null>(null);
	let content = $state<HTMLElement | null>(null);

	/*
	 * The picture this screen stands on, said by whatever inside it knows one (`backdrop.ts`), or
	 * null. The frame draws it because it alone spans the trail, the header and the tools row.
	 */
	let standingOn = $state<string | null>(null);
	ownsTheBackdrop({ stand: (picture) => (standingOn = picture) });

	/* Handed over once both exist, and torn down the way the screen asked (a resize observer). */
	$effect(() => {
		if (!scroller || !content) return;
		return onbody?.({ scroller, content }) ?? undefined;
	});

	/* Said to be THE scrolling body, so a step back can put its position back (`page-scroll.ts`).
	   Its own effect, apart from `onbody`'s measuring. */
	$effect(() => {
		if (!scroller) return;
		return scrollingBody(scroller);
	});

	/*
	 * AT A PHONE'S WIDTH THE TRAIL, THE HEADER AND THE TOOLS SCROLL WITH THE BODY.
	 *
	 * On a phone what is above the body can exceed the window and leave the body no height at all,
	 * so the page scrolls as ONE and the title goes up and away. A different branch of the markup,
	 * never a hidden copy. Not for `fillBody`, whose ground is sized to the box. The grid still
	 * measures the content box, which the rows above are not part of.
	 */
	const leadScrolls = $derived(phoneWidth.yes && !fillBody);
</script>

<div
	class="frame"
	class:trailed={banded}
	class:on-a-picture={standingOn !== null}
	aria-label={label}
>
	<!--
		THE GROUND UNDER EVERYTHING ABOVE THE BODY.

		First in the markup, which is the whole of how it is ordered: the three rows over it are
		positioned and paint after it, with no z-index. Anything added above the body that is not
		positioned is drawn under this. A silent ground (`alt=""`, `aria-hidden`, no pointer) and
		static, so reduced motion has nothing to turn off.
	-->
	{#if standingOn !== null}
		<div class="frame-backdrop" aria-hidden="true">
			<img class="backdrop-picture" src={standingOn} alt="" draggable="false" decoding="async" />
		</div>
	{/if}

	<!-- The rows above the body: in the frame's own tracks on a desktop, at the top of the scrolling
	     box on a phone. See `leadScrolls`. -->
	{#snippet lead()}
		{#if banded && crumbs}
			<div class="frame-trail"><Breadcrumbs {crumbs} /></div>
		{/if}

		{#if header}
			<div class="frame-header">{@render header()}</div>
		{/if}

		{#if tools}
			<div class="frame-tools">{@render tools()}</div>
		{/if}
	{/snippet}

	{#if !leadScrolls}{@render lead()}{/if}

	<!--
		The body is the app's one scrolling region per screen (`Scroller`). `frame-body` stays the
		name of the SCROLLING element, which the grid measures and the browser tests read.
	-->
	<div class="frame-body-slot">
		<Scroller viewportClass="frame-body" onviewport={(element) => (scroller = element)}>
			{#if leadScrolls}{@render lead()}{/if}
			<!-- The arrival is on the content box: on the scroller it would fade the scrollbar and
			     move the region the wall measures. -->
			<div
				class="frame-body-inner"
				class:bleed
				class:measure
				class:fill={fillBody}
				bind:this={content}
				{@attach appears(arrival)}
			>
				{@render children()}
			</div>
		</Scroller>
	</div>

	{#if footer}
		<div class="frame-footer">{@render footer()}</div>
	{/if}

	<!-- Over the screen, and outside the box that moves. See `floating`. -->
	{@render floating?.()}
</div>

<style>
	.frame {
		display: grid;
		/*
		 * Header and tools take what they need, the body takes the rest. `minmax(0, 1fr)` because a
		 * `1fr` track will not shrink below its content (the whole page would scroll), and because
		 * the wall sizes its page from this height, so it must depend on the window alone or the
		 * page size feeds itself (`cards.svelte.ts`). The `auto` footer holds a fixed-height pager.
		 */
		grid-template-rows: auto auto auto minmax(0, 1fr) auto;
		/* The same sideways: an implicit `auto` column grows with the window and will not shrink
		   back, and the wall's rows would hold it open. */
		grid-template-columns: minmax(0, 1fr);
		grid-template-areas:
			'trail'
			'header'
			'tools'
			'body'
			'footer';
		block-size: 100%;
		min-block-size: 0;
		/* ...and fills a flex parent too (the entity detail screens), or the frame would take its
		   content's height and the footer would move with the content. */
		flex: 1;
		/* The ground everything floating over this screen is positioned against. See the header. */
		position: relative;
	}

	/*
	 * The backdrop: a picture behind the trail, the header and the tools, blurred past recognition.
	 *
	 * The whole page's width, gutters drawn over, since a ground inset with a rounded corner reads
	 * as a card. Rows one to three by the declared lines `trail-start` and `tools-end`, which exist
	 * whether or not those rows hold anything, so both entity page shapes are covered. The band in
	 * the header collapses over `--dur-base`, re-rasterising the blur; one layer, accepted.
	 */
	.frame-backdrop {
		grid-column: 1;
		/* The ground spans every row, since stopping at the tab strip would draw a line across the
		   page; the scrim keeps what is drawn over it legible. */
		grid-row: 1 / -1;
		position: relative;
		overflow: hidden;
		pointer-events: none;
	}

	/*
	 * The page's own ground, poured over the picture: opaque where it meets the top bar, thinned to
	 * `--band-scrim` from a fifth of the way down, so there is no hard edge at the top to read as a
	 * fault (as `--sift-scrim-none`). A share of the layer, so it lands alike with or without a
	 * trail, and the floor below holds the contrast the ink was solved against.
	 */
	.frame-backdrop::after {
		content: '';
		position: absolute;
		inset: 0;
		background: linear-gradient(to bottom, var(--sift-bg), var(--band-scrim) 20%);
	}

	/*
	 * The picture itself: the size of the layer, grown past it by twice the blur on every side, since
	 * a blur fades its own edges and twice the radius carries nearly all the light. The size is
	 * written, not only a negative `inset`, because an `<img>` with `width: auto` keeps its intrinsic
	 * width. `object-position: center` keeps a portrait cover's subject.
	 */
	.backdrop-picture {
		position: absolute;
		inset: calc(-2 * var(--band-blur));
		inline-size: calc(100% + 4 * var(--band-blur));
		block-size: calc(100% + 4 * var(--band-blur));
		object-fit: cover;
		object-position: center;
		filter: blur(var(--band-blur));
		/* A tenth less of the picture, before the scrim: see `--band-picture`. */
		opacity: var(--band-picture);
	}

	/*
	 * The caption ink is one step up over a picture, which is what lets the picture through.
	 *
	 * `--band-scrim` is the least opacity holding `--sift-ink-2` at 4.5:1 over a white picture
	 * (the arithmetic is beside it in `app.css`). The token is redefined here, so the shared
	 * components' captions follow without edits; only the three rows over the picture, never the
	 * body. `contrast.test.ts` holds this line.
	 */
	.frame.on-a-picture .frame-trail,
	.frame.on-a-picture .frame-header,
	.frame.on-a-picture .frame-tools {
		--sift-ink-3: var(--sift-ink-2);
	}

	/*
	 * The header's inset, from the frame rather than from the header, so every screen's title lands
	 * on one line (`e2e/page-alignment.spec.ts`).
	 */
	.frame-header {
		grid-area: header;
		padding: var(--page-pad) var(--page-pad) var(--page-gap);
		/* Positioned so it paints after the backdrop; see the backdrop's note. */
		position: relative;
	}

	/*
	 * A PHONE'S TRAIL BAND (on a desk the trail is in the top bar and there is no band).
	 *
	 * The band is the header's top inset, so titles with and without a trail stand level: one height,
	 * `--page-pad` plus `--space-3`, with the trail on its floor. A height, not padding over a line,
	 * which rounds; a FLOOR, so a trail wrapped onto two lines is not pushed under the top bar.
	 */
	.frame-trail {
		grid-area: trail;
		min-block-size: calc(var(--page-pad) + var(--space-3));
		padding-inline: var(--page-pad);
		display: grid;
		align-content: end;
		/* Above the backdrop, like the header. See `.frame-header`. */
		position: relative;
	}

	/*
	 * ONE LINE FOR EVERY SCREEN'S FIRST CONTENT ON A DESK: the page's inset under the top bar, the
	 * line Theater's title stands on.
	 */
	.frame-header {
		padding-block-start: var(--page-pad);
	}

	/*
	 * On a phone, under a trail the header keeps one step of its inset, and the untrailed inset
	 * carries the band and that step, so the two titles stay level.
	 */
	@media (max-width: 767px) {
		.frame-header {
			padding-block-start: calc(var(--page-pad) + var(--space-3) + var(--space-1));
		}

		.frame.trailed .frame-header {
			padding-block-start: var(--space-1);
		}
	}

	.frame-tools {
		grid-area: tools;
		padding-inline: var(--page-pad);
		padding-block-end: var(--page-gap);
		/* Above the backdrop, like the header. See `.frame-header`. */
		position: relative;
	}

	.frame-body-slot {
		grid-area: body;
		min-block-size: 0;
	}

	.frame-body-inner {
		padding-inline: var(--page-pad);
		padding-block-end: var(--page-pad);
	}

	/* See `fillBody`. `100%` of the scrolling box, a definite height, so this resolves. */
	.frame-body-inner.fill {
		min-block-size: 100%;
		/* A column, because a child's percentage minimum resolves against an `auto` height; flex
		   hands the leftover down to the child's `flex: 1`. */
		display: flex;
		flex-direction: column;
	}

	/* Whatever the screen put in a filled body takes the height of it. One child. */
	.frame-body-inner.fill > :global(*) {
		flex: 1;
	}

	.frame-body-inner.bleed {
		padding-inline: 0;
	}

	/*
	 * The footer's inset, taken from the frame like the header's, always the page's (`bleed` is
	 * about content). No block padding: the pager carries its own height (`--page-footer-height`).
	 */
	.frame-footer {
		grid-area: footer;
		padding-inline: var(--page-pad);
	}

	/*
	 * How much of the bottom of this screen the footer has taken, for anything floating over it, so
	 * the selection bar does not land on the pager. Published from the frame, since the bar cannot
	 * know whether its screen has a footer; without one the bar's fallback applies.
	 */
	.frame:has(> .frame-footer) {
		--frame-footer: var(--page-footer-height);
	}

	/*
	 * The maximum measure. Absolute, not a percentage of the window: the whole point is that a
	 * 32-inch monitor does not get a longer line than a laptop, it gets more margin.
	 */
	.frame-body-inner.measure > :global(*) {
		max-inline-size: var(--page-measure);
		margin-inline: auto;
	}
</style>
