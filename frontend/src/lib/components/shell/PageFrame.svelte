<script lang="ts">
	/*
	 * The shape every screen has: trail (a phone's only), header, optional tools, a body that is
	 * the only thing that scrolls, and an optional footer holding the pager. Positioned, so
	 * floating bars anchor to the screen rather than the window.
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
		/** The way here: the top bar's on a desk (`pageTrail`), a band here on a phone. */
		crumbs?: Crumb[];
		/** The title row: nearly always a `PageHeader`, a snippet so a screen can compose one. */
		header?: Snippet;
		/** A strip under the header: filters, tabs, a search box. */
		tools?: Snippet;
		/** The screen itself. The only part that scrolls. */
		children: Snippet;
		/** The pager row, outside the scrolling box; absent, the track collapses. */
		footer?: Snippet;
		/** Run the body to the edges, for a screen whose content is the edge (the grid). */
		bleed?: boolean;
		/** Cap the body's width and centre it; opt-in, since media fills. */
		measure?: boolean;
		/** Grow the content box to the scrolling region, for a screen whose ground is operable. */
		fillBody?: boolean;
		/** Marks the region for a screen reader, where the frame is not the whole page. */
		label?: string;
		/** Hands over the scrolling viewport and the padded content box; measure the right one. */
		onbody?: (parts: { scroller: HTMLElement; content: HTMLElement }) => (() => void) | void;
		/** What is on screen, so the body eases in on change: the page asked for, never a count. */
		arrival?: () => unknown;
		/** Floats over the screen (the selection bar), outside the box the arrival transforms. */
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

	/* A band here on a phone; on a desk the top bar's, so no band pushes the header down. */
	const banded = $derived(trailed && phoneWidth.yes);
	const me = Symbol('frame');
	$effect(() => {
		if (!trailed || phoneWidth.yes || !crumbs) return;
		pageTrail.say(me, crumbs);
		return () => pageTrail.unsay(me);
	});

	let scroller = $state<HTMLElement | null>(null);
	let content = $state<HTMLElement | null>(null);

	// The picture this screen stands on (`backdrop.ts`); the frame alone spans the rows above.
	let standingOn = $state<string | null>(null);
	ownsTheBackdrop({ stand: (picture) => (standingOn = picture) });

	/* Handed over once both exist, and torn down the way the screen asked (a resize observer). */
	$effect(() => {
		if (!scroller || !content) return;
		return onbody?.({ scroller, content }) ?? undefined;
	});

	/* The scrolling body, so a step back can restore its position (`page-scroll.ts`). */
	$effect(() => {
		if (!scroller) return;
		return scrollingBody(scroller);
	});

	// On a phone the rows above scroll with the body, or they could leave it no height.
	const leadScrolls = $derived(phoneWidth.yes && !fillBody);
</script>

<div
	class="frame"
	class:trailed={banded}
	class:on-a-picture={standingOn !== null}
	aria-label={label}
>
	<!-- The ground: first in the markup so the positioned rows paint over it, no z-index. -->
	{#if standingOn !== null}
		<div class="frame-backdrop" aria-hidden="true">
			<img class="backdrop-picture" src={standingOn} alt="" draggable="false" decoding="async" />
		</div>
	{/if}

	<!-- The rows above the body; see `leadScrolls`. -->
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

	<!-- `frame-body` names the scrolling element, which the grid and the browser tests read. -->
	<div class="frame-body-slot">
		<Scroller viewportClass="frame-body" onviewport={(element) => (scroller = element)}>
			{#if leadScrolls}{@render lead()}{/if}
			<!-- On the content box: on the scroller it would fade the scrollbar. -->
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
		/* `minmax(0, 1fr)`: a `1fr` track never shrinks, and the wall sizes its page from this. */
		grid-template-rows: auto auto auto minmax(0, 1fr) auto;
		/* An implicit `auto` column would grow and not shrink back. */
		grid-template-columns: minmax(0, 1fr);
		grid-template-areas:
			'trail'
			'header'
			'tools'
			'body'
			'footer';
		block-size: 100%;
		min-block-size: 0;
		/* Fills a flex parent too, or the footer would move with the content. */
		flex: 1;
		/* The ground floating bars position against. */
		position: relative;
	}

	/* The backdrop: the whole page's width, rows one to three by their named lines, blurred. */
	.frame-backdrop {
		grid-column: 1;
		/* Every row: stopping at the tab strip would draw a line; the scrim keeps it legible. */
		grid-row: 1 / -1;
		position: relative;
		overflow: hidden;
		pointer-events: none;
	}

	/* The page's ground over the picture, opaque at the top bar, thinned to `--band-scrim`. */
	.frame-backdrop::after {
		content: '';
		position: absolute;
		inset: 0;
		background: linear-gradient(to bottom, var(--sift-bg), var(--band-scrim) 20%);
	}

	/* Grown past the layer by twice the blur, as a blur fades its own edges. */
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

	/* One ink step up over a picture; `--band-scrim` holds it at 4.5:1 (`contrast.test.ts`). */
	.frame.on-a-picture .frame-trail,
	.frame.on-a-picture .frame-header,
	.frame.on-a-picture .frame-tools {
		--sift-ink-3: var(--sift-ink-2);
	}

	/* From the frame, so every title lands on one line (`e2e/page-alignment.spec.ts`). */
	.frame-header {
		grid-area: header;
		padding: var(--page-pad) var(--page-pad) var(--page-gap);
		/* Positioned so it paints after the backdrop; see the backdrop's note. */
		position: relative;
	}

	/* A phone's trail band: a floor, not padding, so titles stand level and a wrap still fits. */
	.frame-trail {
		grid-area: trail;
		min-block-size: calc(var(--page-pad) + var(--space-3));
		padding-inline: var(--page-pad);
		display: grid;
		align-content: end;
		/* Above the backdrop, like the header. See `.frame-header`. */
		position: relative;
	}

	/* Every screen's first content on one line on a desk, where the Theater title stands. */
	.frame-header {
		padding-block-start: var(--page-pad);
	}

	/* On a phone the titles with and without a trail stay level. */
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
		/* A column, so the child's `flex: 1` takes the leftover. */
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

	/* Always the page's inset; the pager carries its own height. */
	.frame-footer {
		grid-area: footer;
		padding-inline: var(--page-pad);
	}

	/* Published so the selection bar clears the pager; it cannot know a footer is there. */
	.frame:has(> .frame-footer) {
		--frame-footer: var(--page-footer-height);
	}

	/* Absolute, so a large monitor gets more margin rather than a longer line. */
	.frame-body-inner.measure > :global(*) {
		max-inline-size: var(--page-measure);
		margin-inline: auto;
	}
</style>
