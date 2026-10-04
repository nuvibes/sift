<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Breadcrumbs',
		category: 'composition',
		role: 'the trail of where this screen sits, each step a real address',
		basis: 'own',
		states: ['one step', 'several steps', 'folded', 'in the top bar']
	} satisfies DesignEntry;

	/** One step on the way here. The last one has no `href`: it is where you already are. */
	export interface Crumb {
		label: string;
		href?: string;
	}

	/**
	 * How far a trail is folded: every step shown; the middle folded behind one press (the first
	 * and the last two stay); the front folded (the last two stay); or everything but where you are.
	 */
	export type Fold = 'none' | 'middle' | 'front' | 'all';

	/** A trail of this many steps or more has its middle folded, whatever the room. */
	export const FOLDS_AT = 4;

	/**
	 * Which steps a fold leaves standing and which it puts behind the press, by position.
	 *
	 * `before` is drawn ahead of the press, `folded` is listed behind it, `after` follows it. With
	 * nothing folded there is no press at all, and every step is `before`.
	 */
	export function folding(
		crumbs: Crumb[],
		fold: Fold
	): { before: Crumb[]; folded: Crumb[]; after: Crumb[] } {
		if (fold === 'front' && crumbs.length > 2) {
			return { before: [], folded: crumbs.slice(0, -2), after: crumbs.slice(-2) };
		}
		if (fold === 'all' && crumbs.length > 1) {
			return { before: [], folded: crumbs.slice(0, -1), after: crumbs.slice(-1) };
		}
		if (fold === 'middle' && crumbs.length >= FOLDS_AT) {
			return { before: crumbs.slice(0, 1), folded: crumbs.slice(1, -2), after: crumbs.slice(-2) };
		}
		return { before: crumbs, folded: [], after: [] };
	}
</script>

<script lang="ts">
	/* WHY NOT BITS-UI: there is no breadcrumb in it, and there is nothing for one to do. This is an
	   ordered list of links with a separator between them; the site's own <nav>, <ol> and <a>
	   carry every bit of the semantics, and aria-current names the page. The folded steps open in
	   the app's own menu (`MenuButton`), which is the library's. */

	/*
	 * Where this screen sits, and one click back to each step above it.
	 *
	 * The last crumb is not a link: it is the page you are on, and an inert link among real ones is
	 * a small lie. It is drawn in the ordinary ink with `aria-current="page"`.
	 *
	 * The separator is not text: a typed chevron is read aloud ("People greater than Ada greater
	 * than Appearances"), so it is hidden and the ordered list carries the nesting.
	 *
	 * A LONG TRAIL FOLDS ITS MIDDLE. From `FOLDS_AT` steps on, the steps between the first and the
	 * last two go behind one press drawn where they stood, which opens them as a list of links, the
	 * outermost first. Where the trail is told to `fit` its box (the top bar, which has one line and
	 * a share of it), it folds further when that line is still too short: everything but where you
	 * are goes behind the press, the last two steps standing first and then the last alone, and that
	 * step gives way to an ellipsis. The fold is measured
	 * against the box, never against the window, and the box's width does not depend on what is
	 * drawn in it, so folding cannot change the room it was decided on.
	 *
	 * A plain click on the step above steps back to it. A crumb names a bare address (`/people`),
	 * and a wall writes which rows it shows into the address it is left at, so following the link
	 * would start a new navigation at the front of the wall. Going to the remembered address
	 * restores the page but not the scroll, since a new entry has no scroll snapshot. So a plain
	 * left-click takes the browser's own step whenever the crumb's target is the previous screen in
	 * this tab's history (counted over the entries a person page's tabs push) and goes to the
	 * remembered address only where the browser cannot say where that screen sits. A modifier, a
	 * middle-click, or a page opened directly follows the href. The rule is `returnTo`, shared with
	 * `BackButton`. In effect only the crumb above the last can match, since only that one can be
	 * where you came from. A folded step follows the same rule from the list behind the press, so a
	 * trail folded down to where you are still steps back to the wall as Back does.
	 */
	import { tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { returnTo } from '$lib/shell/navigation.svelte';

	interface Props {
		/** The trail, outermost first. The last is where you are. */
		crumbs: Crumb[];
		/**
		 * Keep to one line of the box it is given, folding further when the line is short. The top
		 * bar's trail. Without it the trail wraps, as the first line of a page does on a phone.
		 */
		fit?: boolean;
	}

	let { crumbs, fit = false }: Props = $props();

	/** The least a trail of this length is folded, before any measuring. */
	const least = $derived<Fold>(crumbs.length >= FOLDS_AT ? 'middle' : 'none');

	/* What the measuring settled on; `least` until the box has been read. */
	let measured = $state<Fold | null>(null);
	const fold = $derived<Fold>(fit ? (measured ?? least) : least);
	const parts = $derived(folding(crumbs, fold));

	let nav = $state<HTMLElement | null>(null);

	/* The next fold, when this one is too wide for the box. */
	const FURTHER: Record<Fold, Fold | null> = {
		none: 'middle',
		middle: 'front',
		front: 'all',
		all: null
	};

	/** Whether what is drawn runs past the box. The steps never shrink until the last fold, so a
	    trail too long for its box shows as an overflow rather than as squeezed words. */
	function overflows(box: HTMLElement): boolean {
		const list = box.firstElementChild as HTMLElement | null;
		return list !== null && list.scrollWidth > box.clientWidth + 0.5;
	}

	/* Start from the least fold and go further while the line is too short. Each step redraws and
	   reads again; four steps at most. A fold that leaves a short trail as it was (the middle of
	   three steps) changes nothing and is passed straight through. */
	async function settle(): Promise<void> {
		if (!fit || !nav) return;
		measured = least;
		await tick();
		let next = FURTHER[measured];
		while (nav && next !== null && overflows(nav)) {
			measured = next;
			await tick();
			next = FURTHER[measured];
		}
	}

	/* Read again when the box changes width, and when the trail changes. */
	$effect(() => {
		if (!fit || !nav) return;
		void crumbs;
		void settle();
		if (typeof ResizeObserver === 'undefined') return;
		const box = nav;
		let width = box.clientWidth;
		const watch = new ResizeObserver(() => {
			if (box.clientWidth === width) return;
			width = box.clientWidth;
			void settle();
		});
		watch.observe(box);
		return () => watch.disconnect();
	});
</script>

{#snippet step(crumb: Crumb, here: boolean)}
	{#if here || !crumb.href}
		<span class="here" aria-current={here ? 'page' : undefined}>{crumb.label}</span>
	{:else}
		<a href={crumb.href} onclick={(event) => returnTo(event, crumb.href ?? '')}>
			{crumb.label}
		</a>
	{/if}
{/snippet}

{#snippet separator()}
	<span class="chevron" aria-hidden="true"><Icon name="chevron_right" size={16} /></span>
{/snippet}

{#if crumbs.length > 1}
	<nav
		class="crumbs"
		class:fit
		class:squeezed={fit && fold === 'all'}
		aria-label="Breadcrumb"
		bind:this={nav}
	>
		<ol>
			{#each parts.before as crumb, at (`b-${at}-${crumb.label}`)}
				<li>
					{#if at > 0}{@render separator()}{/if}
					{@render step(crumb, parts.folded.length === 0 && at === crumbs.length - 1)}
				</li>
			{/each}
			{#if parts.folded.length > 0}
				<li>
					{#if parts.before.length > 0}{@render separator()}{/if}
					<MenuButton label="Show the folded steps">
						{#snippet trigger({ props })}
							<Tooltip label="Show the folded steps" placement="bottom">
								<button {...props} type="button" class="fold" aria-label="Show the folded steps">
									<Icon name="more_horiz" size={16} />
								</button>
							</Tooltip>
						{/snippet}
						{#each parts.folded as crumb, at (`f-${at}-${crumb.label}`)}
							{#if crumb.href}
								<ContextMenuItem
									label={crumb.label}
									href={crumb.href}
									onfollow={(event) => returnTo(event, crumb.href ?? '')}
								/>
							{/if}
						{/each}
					</MenuButton>
				</li>
				{#each parts.after as crumb, at (`a-${at}-${crumb.label}`)}
					<li>
						{@render separator()}
						{@render step(crumb, at === parts.after.length - 1)}
					</li>
				{/each}
			{/if}
		</ol>
	</nav>
{/if}

<style>
	/*
	 * No gap of its own: on a phone the frame's band is the only place a trail is drawn, and the
	 * band is the header's top inset; on a desk the trail stands in the top bar's row, centred on it.
	 */

	.crumbs ol {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: var(--space-1);
		/*
		 * The first crumb's inline padding would pull its first letter in from the edge it lines up
		 * with, so the list steps back by that padding: the word starts on the edge and the ring
		 * keeps its room.
		 */
		margin: 0 0 0 calc(-1 * var(--space-1));
		padding: 0;
		list-style: none;
	}

	/* In the top bar: one line, never wrapped, since the bar has one. What does not fit is folded
	   (see the script), not wrapped and not clipped. */
	.crumbs.fit {
		min-inline-size: 0;
		overflow: hidden;
	}

	.crumbs.fit ol {
		flex-wrap: nowrap;
		/* The ring of a focused step is drawn outside it; this keeps it inside the box that clips. */
		padding-block: var(--space-1);
		margin-inline-start: 0;
	}

	li {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		/* A step keeps its whole word until the last fold: an overflow is what tells the trail to
		   fold further, and a step that shrank would hide it. */
		flex: none;
		font: var(--text-body-sm);
		/* The separator, and only the separator: the labels set their own below.
		   NOT the decoration ink: that one is below the contrast floor and is reserved for things
		   that are not read at all, and a chevron between two words is read as punctuation. */
		color: var(--sift-ink-3);
	}

	/* At the last fold the step where you are is the one thing standing, and it gives way to an
	   ellipsis rather than running past the box. */
	.squeezed li:last-child {
		flex: 0 1 auto;
		min-inline-size: 0;
	}

	.chevron {
		display: inline-flex;
	}

	a {
		color: var(--sift-ink-3);
		text-decoration: none;
		border-radius: var(--radius-sm);
		padding-inline: var(--space-1);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		transition: color var(--dur-instant) var(--ease);
	}

	a:hover {
		color: var(--sift-ink);
		text-decoration: underline;
	}

	/* A finger's height on a phone without the band growing: the link's own box reaches a finger's
	   height (padding) and gives the same back (margin), so the trail keeps its line and the title
	   under it stays where every title stands. Not a ring: the link clips its own overflow for the
	   ellipsis, which would clip a ring too. */
	@media (max-width: 767px) {
		a,
		.fold {
			padding-block: calc((var(--touch-target) - 1lh) / 2);
			margin-block: calc((1lh - var(--touch-target)) / 2);
		}
	}

	a:focus-visible,
	.fold:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/*
	 * The press standing for the folded steps: the separator's ink, a step's own size, and the
	 * hover every press has. A glyph and no words, so its name is its tooltip and its label.
	 */
	.fold {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		padding: 0 var(--space-1);
		border: 0;
		border-radius: var(--radius-sm);
		background: none;
		color: var(--sift-ink-3);
		font: inherit;
		cursor: pointer;
		transition:
			color var(--dur-instant) var(--ease),
			background var(--dur-instant) var(--ease);
	}

	.fold:hover,
	.fold[data-state='open'] {
		color: var(--hover-ink);
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
	}

	/* Where you are: the full ink, so the end of the trail is the readable part of it. */
	.here {
		color: var(--sift-ink);
		padding-inline: var(--space-1);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
</style>
