<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'Breadcrumbs',
		category: 'composition',
		role: 'the trail of where this screen sits, each step a real address',
		basis: 'own',
		states: ['one step', 'several steps', 'folded', 'in the top bar', 'short of room']
	} satisfies DesignEntry;

	/** One step on the way here. The last one has no `href`: it is where you already are. */
	export interface Crumb {
		label: string;
		href?: string;
	}

	/** How far a trail is folded: every step shown, or the middle behind one press (the first and
	 * the last two stay). */
	export type Fold = 'none' | 'middle';

	/** A trail of this many steps or more has its middle folded, whatever the room. */
	export const FOLDS_AT = 4;

	/** Which steps a fold leaves standing ahead of the press, behind it, and after it. */
	export function folding(
		crumbs: Crumb[],
		fold: Fold
	): { before: Crumb[]; folded: Crumb[]; after: Crumb[] } {
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
	 * Where this screen sits, and one click back to each step above it. The last step is where you
	 * are, never a link (`aria-current="page"`); the chevrons are hidden from a screen reader.
	 *
	 * From `FOLDS_AT` steps the middle folds behind one press listing them, outermost first. Told
	 * to `fit` its box, it keeps to one line that ends in an ellipsis where the box is short, with
	 * the whole path as its tooltip.
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
	 * where you came from. A folded step follows the same rule from the list behind the press.
	 */
	import Icon from '$lib/components/Icon.svelte';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import MenuButton from '$lib/components/common/MenuButton.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { returnTo } from '$lib/shell/navigation.svelte';

	interface Props {
		/** The trail, outermost first. The last is where you are. */
		crumbs: Crumb[];
		/**
		 * Keep to one line of the box it is given, ending in an ellipsis when the line is short. The
		 * top bar's trail. Without it the trail wraps, as the first line of a page does on a phone.
		 */
		fit?: boolean;
	}

	let { crumbs, fit = false }: Props = $props();

	const parts = $derived(folding(crumbs, crumbs.length >= FOLDS_AT ? 'middle' : 'none'));

	/* The whole path, for the tooltip of a trail the ellipsis may have cut. */
	const whole = $derived(crumbs.map((one) => one.label).join(' \u203a '));
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

{#snippet trail()}
	<nav class="crumbs" class:fit aria-label="Breadcrumb">
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
							{:else if parts.after.length === 0 && at === parts.folded.length - 1}
								<!-- Where you are, last and not a link. -->
								<ContextMenuItem label={crumb.label} disabled onselect={() => {}} />
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
{/snippet}

{#if crumbs.length > 1}
	{#if fit}
		<Tooltip label={whole} placement="bottom" stretch shrinks staysOnPress>
			{@render trail()}
		</Tooltip>
	{:else}
		{@render trail()}
	{/if}
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

	/* In the top bar: one line, never wrapped, since the bar has one. What does not fit ends in an
	   ellipsis: the steps are inline, so the line, not each word, is what is cut. */
	.crumbs.fit {
		flex: 1 1 auto;
		min-inline-size: 0;
	}

	.crumbs.fit ol {
		display: block;
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		/* The ring of a focused step is drawn outside it; this keeps it inside the box that clips. */
		padding-block: var(--space-1);
		margin-inline-start: 0;
	}

	.crumbs.fit li {
		display: inline;
	}

	.crumbs.fit .chevron,
	.crumbs.fit li :global(.wrap) {
		vertical-align: middle;
	}

	.crumbs.fit .chevron {
		margin-inline: var(--space-1);
	}

	li {
		display: flex;
		align-items: center;
		gap: var(--space-1);
		/* A step keeps its whole word; in the top bar the line ends in an ellipsis instead. */
		flex: none;
		font: var(--text-body-sm);
		/* The separator, and only the separator: the labels set their own below.
		   NOT the decoration ink: that one is below the contrast floor and is reserved for things
		   that are not read at all, and a chevron between two words is read as punctuation. */
		color: var(--sift-ink-3);
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
