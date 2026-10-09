<script lang="ts">
	/* One person, Site, tag, collection, set or song as a portrait card; the card knows no kind, so
	 * there is one hover, one focus ring and one heart. */
	import Pressable from '$lib/components/common/Pressable.svelte';
	import { pressOnCard } from '$lib/components/common/card-press';
	import type { Snippet } from 'svelte';
	import { coverUrl } from '$lib/entity/art';
	import type { Frame } from '$lib/entity/cover-frame';
	import { faceCoverUrl } from '$lib/people/faces.svelte';
	import { creatorArt } from '$lib/entity/creator-art.svelte';
	import { dropOffer, type AimedAt } from '$lib/library/aimed-drop.svelte';
	import { vaultPrompt } from '$lib/shell/vault.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import type { IconName } from '$lib/design/icons';
	import PickMark from '$lib/components/common/PickMark.svelte';
	import { swapMode, type SwapKind } from '$lib/swap/mode.svelte';
	import { cardSweep, SWAP_ID, SWAP_KIND, SWAP_NAME, SWAP_REFUSED } from '$lib/swap/sweep.svelte';
	import { sayRefused, type RefusedMark } from '$lib/swap/refused';
	import {
		Avatar,
		ContextMenu,
		Heart,
		RatingChip,
		SharingMark,
		TILE_ID
	} from '$lib/components/common';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Withheld from '$lib/components/common/Withheld.svelte';
	import EntityCounts from './EntityCounts.svelte';
	import NetworkMark from './NetworkMark.svelte';
	import type { CountCell } from './entity-counts';

	interface Props {
		href: string;
		name: string;
		/** The Sites this is on, as small marks over the cover's bottom scrim. */
		sites?: readonly { name: string; src: string }[];
		/** The still this is drawn as. Absent draws the monogram below rather than a broken image. */
		coverAssetId?: string | null;
		/** An uploaded cover, so a Site's logo is not drawn over it. */
		coverUploadId?: string | null;
		/** Which moment of `coverAssetId` the cover is, on the address (`names_its_cover`). */
		coverAtMs?: number | null;
		/** The window of the picture drawn, on the address too (`cover-frame.ts`). */
		coverFrame?: Frame | null;
		/** A person's name for a site picture when no cover is chosen. */
		creatorName?: string | null;
		/** A Site's name: with no chosen cover it wears the site's own mark. As `creatorName`. */
		siteName?: string | null;
		/** The icon pack's token for this Site's logo (`coverUrl`'s `icon`); `true` with none. */
		siteIcon?: string | boolean | null;
		/** A face out of that still, preferred; the server sends both or neither. */
		coverTrackId?: string | null;
		/** What ends the face-cover address, so the picture leaves the browser's store when hidden. */
		art?: string | null;
		/** Small facts under the name: a count of files, a count of handles, a site's kind. */
		detail?: string;
		/** What it has, as the hover card's cells; empty draws no row. */
		counts?: readonly CountCell[];
		/** A glyph instead of the letter where there is no picture (a song's). */
		glyph?: IconName;
		/** Whether anybody has been given this or refused it. Admin only; see the browse models. */
		shared?: boolean;
		restricted?: boolean;
		/** Where that was decided, for a Site under a network only (`SharingMark`). */
		shared_here?: boolean;
		restricted_here?: boolean;
		/** In the vault, and listed anyway, which only happens with the vault open. */
		hidden?: boolean;
		/** Open the sharing panel from the mark. Absent leaves the mark as plain text. */
		onsharing?: () => void;
		/* Open the HIDDEN panel from the crossed-out eye (`SharingMark.onhidden`). */
		onhidden?: () => void;
		/* The opinions: both absent draws no row; `false` or `null` is an answer. */
		favorite?: boolean;
		rating?: number | null;
		/** Pinned to the top of this wall: a mark, set from the menu and the bar. */
		pinned?: boolean;
		/** This person makes the edits: the mark in the bottom-right corner. */
		pmvCreator?: boolean;
		/**
		 * A locked tile: the server withheld the name, pictures and address; a press asks for the
		 * PIN.
		 */
		locked?: boolean;
		onfavorite?: (favorite: boolean) => void;
		onrate?: (rating: number | null) => void;
		/** Drawn as a drop target while something is over it. Owned by whoever owns the drag. */
		dropping?: boolean;
		/** What a dropped link would be aimed at, for the offer's words. */
		droppingKind?: AimedAt;
		/* The card's right-click menu. */
		menu?: Snippet;
		/** Picked, as media tiles are; these props arrive together or not at all. */
		selected?: boolean;
		/** This row's id on the DOM, for a sweep; absent where the wall does not pick. */
		id?: string;
		onpressstart?: (event: PointerEvent) => void;
		onpressend?: () => void;
		/** The click caught on the way down, before an anchor navigates. */
		onclickcapture?: (event: MouseEvent) => void;
		/**
		 * Picked as a filter on the page's files (not `selected`): the picture toggles, with
		 * PickMark.
		 */
		picked?: boolean;
		onpick?: () => void;
		/** What this card offers in swap mode; `refused` wears the pick in danger and says why. */
		swapAs?: { kind: SwapKind; id: string; refused?: RefusedMark | null };
		/** What the page knows about this card's thing, outside both anchors. */
		beneath?: Snippet;
		/** A line under the name (a song's credits), outside both anchors. */
		byline?: Snippet;
	}

	let {
		href,
		name,
		sites = [],
		coverAssetId = null,
		coverUploadId = null,
		coverAtMs = null,
		coverFrame = null,
		creatorName = null,
		siteName = null,
		siteIcon = false,
		coverTrackId = null,
		art = null,
		detail = '',
		counts = [],
		shared = false,
		restricted = false,
		shared_here = undefined,
		restricted_here = undefined,
		hidden = false,
		onsharing,
		onhidden,
		favorite = undefined,
		rating = undefined,
		pinned = false,
		pmvCreator = false,
		locked = false,
		onfavorite,
		onrate,
		dropping = false,
		droppingKind = undefined,
		menu,
		selected = false,
		id = undefined,
		onpressstart,
		onpressend,
		onclickcapture,
		picked: pickedHere = false,
		onpick: onpickHere = undefined,
		swapAs = undefined,
		beneath,
		byline,
		glyph = undefined
	}: Props = $props();

	/* In swap mode a card that says what it offers picks for the swap; otherwise as the wall said. */
	const swapping = $derived(Boolean(swapAs && swapMode.on));
	/* Will not go in the swap: never picked, and a press says why (a person, a Site, a tag only). */
	const refused = $derived(swapping && swapAs?.refused ? swapAs.refused : null);
	const picked = $derived(
		swapAs && swapping ? !refused && swapMode.has(swapAs.kind, swapAs.id) : pickedHere
	);
	const onpick = $derived(
		swapAs && swapping
			? refused
				? () => sayRefused(swapAs.kind as 'person' | 'site' | 'tag', swapAs.id, name, refused)
				: () => void swapMode.toggle({ kind: swapAs.kind, id: swapAs.id, name })
			: onpickHere
	);

	const within = $derived(counts.find((cell) => cell.id === 'sites_within')?.count ?? 0);
	const cells = $derived(counts.filter((cell) => cell.id !== 'sites_within'));

	/*
	 * The face cover if any, else the whole frame, else a letter; never the small recognizer crop.
	 */
	const picture = $derived(
		/* An UPLOAD first: the one cover with no asset behind it. */
		coverUploadId
			? coverUrl(href, art, { uploadId: coverUploadId, frame: coverFrame })
			: coverAssetId && coverTrackId
				? faceCoverUrl(coverTrackId, art)
				: coverAssetId
					? /* The ENTITY's address, not the file's, so a chosen moment is drawn (`coverUrl`). */
						coverUrl(href, art, { assetId: coverAssetId, atMs: coverAtMs, frame: coverFrame })
					: /* No chosen cover: the picture Sift keeps of how the site shows them (fetched once,
					     never linked), else the monogram. */
						creatorName && creatorArt.has(creatorName)
						? `/api/creator-art/${encodeURIComponent(creatorName)}`
						: /* The shipped pack; a Site it lacks shows its letter, never a fetched mark. */
							siteIcon
							? coverUrl(href, art, { icon: typeof siteIcon === 'string' ? siteIcon : null })
							: null
	);

	/* Whether the picture is the shipped logo, by the `siteIcon` branch's conditions. */
	const pictureIsTheShippedLogo = $derived(
		!coverUploadId &&
			!coverAssetId &&
			!(creatorName && creatorArt.has(creatorName)) &&
			Boolean(siteIcon)
	);

	/* The file's own still while a chosen moment renders. */
	const insteadOf = $derived(
		coverAssetId && !coverTrackId ? `/api/assets/${encodeURIComponent(coverAssetId)}/thumb` : null
	);

	/* A held opinion keeps its corner on show at rest. */
	const hearted = $derived(favorite === true);
	const rated = $derived(typeof rating === 'number' && rating > 0);

	/* The sweep's hook, spread first, only where the card can be picked. */
	const sweepable = $derived(onclickcapture && id ? { [TILE_ID]: id } : {});

	/* In swap mode the press and sweep are the swap's. */
	const offered = $derived(
		swapping && swapAs && id
			? {
					[SWAP_KIND]: swapAs.kind,
					[SWAP_ID]: swapAs.id,
					[SWAP_NAME]: name,
					...(refused ? { [SWAP_REFUSED]: refused } : {})
				}
			: {}
	);
	/* A card that will not go starts no sweep and is never a sweep's; its press says why. */
	const pressStarted = (event: PointerEvent) => {
		if (!(swapping && id)) return onpressstart?.(event);
		if (!refused) cardSweep.pressStart(id, event);
	};
	const pressEnded = () => (swapping ? cardSweep.pressEnd() : onpressend?.());
	const clickCaught = (event: MouseEvent) => {
		if (!(swapping && id)) return onclickcapture?.(event);
		if (refused) return;
		if (cardSweep.clicked(id, event)) event.stopPropagation();
	};

	/* A logo or a creator's site picture is contained, a frame cropped. */
	const pictureIsTheCreatorsOwn = $derived(
		!coverUploadId && !coverAssetId && Boolean(creatorName && creatorArt.has(creatorName))
	);
	const pictureIsAMark = $derived(
		siteName
			? Boolean(coverUploadId || coverAssetId || siteIcon)
			: pictureIsTheShippedLogo || pictureIsTheCreatorsOwn
	);
</script>

{#snippet cover()}
	<!-- No picture yet: the name's first letter. -->
	<Avatar
		src={picture}
		instead={insteadOf}
		{name}
		shape="portrait"
		mark={pictureIsAMark}
		{glyph}
		lazy
	/>
	<span
		class="scrim"
		class:holds={favorite !== undefined || rating !== undefined}
		class:held={hearted || rated}
	></span>
	{#if sites.length > 0}
		<!-- Over the scrim; hidden, since the names are said elsewhere. -->
		<span class="sites" aria-hidden="true">
			{#each sites as site (site.name)}
				<img class="site-mark" src={site.src} alt="" loading="lazy" decoding="async" />
			{/each}
		</span>
	{/if}
{/snippet}

{#snippet card()}
	<!-- svelte-ignore a11y_no_static_element_interactions: the card's own controls are still the
	focusable things here; these only observe or hand on a press. -->
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<div
		{...sweepable}
		{...offered}
		class="card"
		class:dropping
		class:selected
		aria-selected={onclickcapture ? selected : undefined}
		onpointerdown={pressStarted}
		onpointerup={pressEnded}
		onpointercancel={pressEnded}
		onpointerleave={pressEnded}
		onclickcapture={onclickcapture ? clickCaught : undefined}
		onclick={(event) => pressOnCard(event, onpick ? () => onpick?.() : href)}
	>
		<!-- What the card says while something is held over it, since a ring alone says nothing of what. -->
		{#if dropping}
			<span class="taking" aria-hidden="true"
				><span>{droppingKind ? dropOffer(droppingKind) : 'Drop to add'}</span></span
			>
		{/if}
		<!-- The link wraps the picture and words, never the controls. -->
		{#if onpick}
			<!-- The shared press surface; `aria-pressed` rides through attributes. -->
			<Pressable
				class="face"
				feedback="none"
				radius="md"
				aria-pressed={picked}
				aria-label={refused
					? `Why ${name} isn't offered in the swap`
					: swapping
						? `Offer ${name} in the swap`
						: `Filter the files to ${name}`}
				onclick={onpick}
			>
				{@render cover()}
				{#if refused}
					<!-- Will not go: the pick's own look in the danger colour, one state of it. -->
					<PickMark purpose="refused" />
				{:else if picked}
					<!-- The one pick look; the toggle's `aria-pressed` is what is heard. -->
					<PickMark purpose={swapping ? 'swap' : 'filter'} />
				{/if}
			</Pressable>
		{:else}
			<a {href} class="face">
				{@render cover()}
			</a>
		{/if}
		{#if selected}
			<!-- Selected said in shape as well as by the ring; `aria-selected` is what is heard. -->
			<span class="selected-check" aria-hidden="true"><Icon name="check" size={16} /></span>
		{/if}

		<div class="body">
			<!-- The full name in the app's tooltip, never a `title`. -->
			<Tooltip label={name} placement="top" stretch>
				<a {href} class="name">{name}</a>
			</Tooltip>
			{@render byline?.()}
			<span class="under">
				{#if detail}<span class="detail">{detail}</span>{/if}
				{#if within > 0}<span class="detail network"><NetworkMark count={within} /></span>{/if}
				<SharingMark
					{shared}
					{restricted}
					{shared_here}
					{restricted_here}
					{hidden}
					onopen={onsharing}
					{onhidden}
				/>
				{#if pinned}
					<Tooltip label="Pinned to the top" placement="top">
						<span class="pinned"><Icon name="keep" size={14} /></span>
					</Tooltip>
				{/if}
			</span>

			<!-- One line always, folding into "+n" (`fitCells`). -->
			<EntityCounts {name} {cells} fold={href} />
			{#if beneath}
				<!-- Unwrapped, so a card with nothing to say leaves no trace. -->
				{@render beneath()}
			{/if}
		</div>

		{#if pmvCreator}
			<!-- The creator mark, bottom-right, never over a face. -->
			<span class="creator-mark">
				<Tooltip label="PMV creator" placement="top">
					<Icon name="cinematic_blur" size={16} label="PMV creator" />
				</Tooltip>
			</span>
		{/if}

		{#if favorite !== undefined}
			<!-- Heart top-left, rating top-right, outside the anchor. -->
			<span class="judge heart-corner" class:set={hearted}>
				<Heart
					favorite={favorite ?? false}
					onchange={(next) => onfavorite?.(next)}
					size={18}
					grounded
				/>
			</span>
		{/if}
		{#if rating !== undefined}
			<span class="judge rating-corner" class:set={rated}>
				<RatingChip
					rating={rating ?? null}
					onchange={(next) => onrate?.(next)}
					size={18}
					label={name}
					grounded
				/>
			</span>
		{/if}
	</div>
{/snippet}

{#snippet lockedCard()}
	<!-- A locked tile, drawn apart so nothing added above reaches it. -->
	<div class="card locked">
		<Pressable
			class="face"
			feedback="none"
			radius="md"
			aria-label="Hidden. Unhide to show it"
			onclick={() => vaultPrompt.ask()}
		>
			<!-- The locked file tile's own picture, drawn by the one component that draws it. -->
			<span class="shut"><Withheld /></span>
		</Pressable>
		<div class="body">
			<span class="name withheld">Hidden</span>
			{#if detail}<span class="under"><span class="detail">{detail}</span></span>{/if}
			<!-- The counts, inert, kept even when empty for the height. -->
			<div inert><EntityCounts name="Hidden" {cells} /></div>
		</div>
	</div>
{/snippet}

{#if locked}
	{@render lockedCard()}
{:else if menu}
	<ContextMenu triggerClass="card-trigger" label={`Actions for ${name}`} items={menu}>
		{@render card()}
	</ContextMenu>
{:else}
	{@render card()}
{/if}

<style>
	/* The wrapper the menu puts around the card lays nothing out: the wall arranges the cards. */
	:global(.card-trigger) {
		display: contents;
	}

	.card {
		position: relative;
		display: flex;
		flex-direction: column;
		border-radius: var(--tile-radius);
		/* The card's light, its edge a layer under a transparent border. */
		background: var(--sift-card);
		border: 1px solid transparent;
		overflow: hidden;
		transition:
			transform var(--dur-fast) var(--ease),
			box-shadow var(--dur-fast) var(--ease);
	}

	/* A press anywhere on it opens it (`pressOnCard`). */
	.card:not(.locked) {
		cursor: pointer;
	}

	/* HOVER is the lift, and draws no ring: a ring on a card means it is selected. */
	.card:hover {
		transform: translateY(var(--lift-y-sm));
		box-shadow: var(--elev-2);
	}

	/* PRESSED: the card settles back onto the wall under the press. */
	.card:active:not(.locked) {
		transform: none;
		box-shadow: none;
	}

	/* The drop target: a 2px outline at -1px, not clipped like an inset shadow. */
	.card.dropping {
		border-color: transparent;
		/* DASHED, the window-wide offer at a card's size; solid is what a selected card wears. */
		outline: 2px dashed var(--sift-accent);
		outline-offset: -1px;
	}

	/* Selected: the ring, the picture pulled in, the tick. */
	.card.selected {
		border-color: transparent;
		outline: var(--selected-ring-width) solid var(--sift-accent-ring-line);
		outline-offset: calc(var(--selected-ring-width) * -1);
	}

	/* Pulled in by clipping, not a margin, so picking reflows nothing. */
	.card.selected :global(.face) {
		clip-path: inset(
			var(--selected-ring-width) round calc(var(--tile-radius) - var(--selected-ring-width))
		);
	}

	/* The tick takes the rating's corner; the rating is in the card's menu meanwhile. */
	.card.selected .rating-corner {
		visibility: hidden;
	}

	/* "Drop to add" on a light wash, passing pointer events through. */
	.taking {
		position: absolute;
		inset: 0;
		z-index: 2;
		display: grid;
		place-items: center;
		padding: var(--space-2);
		background: var(--sift-accent-wash);
		pointer-events: none;
	}

	/* Its own ground (`--sift-scrim`), so the letters hold up over any photograph. */
	.taking span {
		padding: var(--space-1) var(--space-3);
		border-radius: var(--radius-md);
		background: var(--sift-scrim);
		color: var(--foreground);
		font: var(--text-label);
		text-align: center;
	}

	/* Portrait, one shape on every wall: here the content is a name, so the wall must scan. */
	.card :global(.face) {
		position: relative;
		display: block;
		aspect-ratio: 2 / 3;
		/* Nothing inside may stretch the ratio. */
		overflow: hidden;
		background: var(--sift-surface-3);
		/* The monogram is a letter inside an anchor, which the browser would underline. */
		text-decoration: none;
	}

	/* Out of flow; `aspect-ratio: auto`, or Avatar's 3/4 would crop. */
	.card :global(.face) > :global(.avatar) {
		position: absolute;
		inset: 0;
		aspect-ratio: auto;
	}

	/* Site marks on the scrim, wrapping upward. */
	.sites {
		position: absolute;
		inset-inline: var(--space-2);
		inset-block-end: var(--space-2);
		display: flex;
		flex-wrap: wrap-reverse;
		gap: var(--space-1);
		pointer-events: none;
	}

	/* Straight on the scrim, no ground per mark. */
	.site-mark {
		inline-size: var(--space-5);
		block-size: var(--space-5);
		border-radius: var(--radius-sm);
		/* CONTAINED, never cropped: a mark, as `Avatar.mark`. */
		object-fit: contain;
	}

	.scrim {
		position: absolute;
		inset: 0;
		background: linear-gradient(to top, rgb(6 7 10 / 0.35), transparent 40%);
		pointer-events: none;
	}

	/* The same shade along the TOP edge, only while the heart or the rating is SHOWING (`.judge`). */
	.scrim.held,
	.card:hover .scrim.holds,
	.card:has(:focus-visible) .scrim.holds {
		background:
			linear-gradient(to bottom, rgb(6 7 10 / 0.35), transparent 25%),
			linear-gradient(to top, rgb(6 7 10 / 0.35), transparent 40%);
	}

	.body {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		padding: var(--space-3);
		min-width: 0;
	}

	/* `display: block` and a bound width, or an inline anchor would not ellipsise. */
	.name {
		position: relative;
		display: block;
		max-inline-size: 100%;
		color: var(--sift-ink);
		font: var(--text-body);
		text-decoration: none;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.name:hover {
		text-decoration: underline;
	}

	/* The word where a locked tile's name would be: the state, in the quiet ink, nothing to press. */
	.name.withheld {
		color: var(--sift-ink-3);
	}

	.name.withheld:hover {
		text-decoration: none;
	}

	/* Out of flow, so the face keeps the size its ratio gives it (see `.face`). */
	.shut {
		position: absolute;
		inset: 0;
	}

	.card.locked:hover {
		transform: none;
		box-shadow: none;
	}

	.detail {
		color: var(--sift-ink-3);
		font: var(--text-label);
	}

	/* The mark gives way before the files do on a narrow card. */
	.network {
		display: inline-flex;
		min-inline-size: 0;
	}

	/* The count and the sharing mark share a line, so a glyph most cards lack adds no row. */
	.under {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-block-size: 18px;
	}

	/* The pin, drawn like the sharing marks: a MARK, with no hover state or border of a control. */
	.pinned {
		display: inline-flex;
		align-items: center;
		color: var(--muted-foreground);
	}

	/* The creator mark in the corner, in the inherited ink: a fact, not a control. */
	.creator-mark {
		position: absolute;
		inset-block-end: var(--space-2);
		inset-inline-end: var(--space-2);
		display: inline-flex;
		align-items: center;
	}

	/* The two opinions, one in each top corner, above the scrim that darkens that edge for them. */
	.judge {
		position: absolute;
		inset-block-start: var(--space-2);
		z-index: 1;
		display: inline-flex;
		align-items: center;
		color: var(--foreground);
	}

	/* Hidden at rest unless held, shown on hover or focus by opacity; always on touch. */
	.judge {
		opacity: 0;
		transition: opacity var(--dur-fast) var(--ease);
	}

	.judge.set,
	.card:hover .judge,
	.card:has(:focus-visible) .judge {
		opacity: 1;
	}

	@media (hover: none) {
		.judge {
			opacity: 1;
		}
	}

	.heart-corner {
		inset-inline-start: var(--space-2);
	}

	.rating-corner {
		inset-inline-end: var(--space-2);
	}

	/* The ring a layer over the picture, which covers the face's own outline. */
	.card :global(.face:focus-visible) {
		box-shadow: none;
		outline: none;
	}

	.card :global(.face:focus-visible)::after {
		content: '';
		position: absolute;
		inset: 0;
		z-index: 1;
		border-radius: inherit;
		box-shadow: var(--focus-ring);
		pointer-events: none;
	}

	.name:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	:global(:root[data-motion='reduce']) .card {
		transition: none;
	}

	:global(:root[data-motion='reduce']) .card:hover {
		transform: none;
	}

	/* The corners appear immediately rather than fading in. */
	:global(:root[data-motion='reduce']) .judge {
		transition: none;
	}
	/* The name's reach on a phone, padding given back as margin. */
	@media (max-width: 767px) {
		a.name {
			padding-block: calc((var(--touch-target) - 1lh) / 2);
			margin-block: calc((1lh - var(--touch-target)) / 2);
		}
	}
</style>
