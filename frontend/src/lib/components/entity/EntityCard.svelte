<script lang="ts">
	/*
	 * One person, or one site (or a tag, a collection, a set, a song), as a card.
	 *
	 * The kinds look alike only here: a picture, a name, a count and the opinions anything can hold.
	 * So the card knows nothing about any of them; the screens decide what the cover, the lines and
	 * the callbacks mean, and there is one hover, one focus ring and one place for a heart. Not a
	 * media tile: a portrait card in a wrapping grid, `2 / 3` because most covers are frames of
	 * vertical clips, and large enough to be RECOGNISED among forty.
	 */
	import Pressable from '$lib/components/common/Pressable.svelte';
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
		/**
		 * The sites this thing is on, as small marks over the bottom of the cover: the row under the
		 * name already runs out of room, and the cover's bottom scrim reads against any picture and is
		 * never where a face is. A name and an address each, so this learns nothing about site pictures.
		 */
		sites?: readonly { name: string; src: string }[];
		/** The still this is drawn as. Absent draws the monogram below rather than a broken image. */
		coverAssetId?: string | null;
		/** An UPLOADED cover: how this knows there IS a cover when no asset is named, or the site's own
		 *  logo would be drawn over it. */
		coverUploadId?: string | null;
		/** WHICH MOMENT of `coverAssetId` the cover is: on the address, since the browser may keep only
		 *  an address that names its cover (`kernel/covers.py names_its_cover`). */
		coverAtMs?: number | null;
		/** The window of the chosen picture drawn, or null for the whole: on the address too, so a moved
		 *  window is a new picture (`lib/entity/cover-frame.ts`). */
		coverFrame?: Frame | null;
		/**
		 * The name to ask for a site picture under, with no chosen cover: a person's own name, which is
		 * the handle a download filed it under. Absent for anything else.
		 */
		creatorName?: string | null;
		/** A Site's name: with no chosen cover it wears the site's own mark. As `creatorName`. */
		siteName?: string | null;
		/** Sift's icon pack's picture of this site (the logo's token), so the card asks the cover
		 *  address, which falls through to the pack, on an address the browser may keep (`coverUrl`'s
		 *  `icon`). `true` means a logo with no token, on the re-checked address. */
		siteIcon?: string | boolean | null;
		/**
		 * A face out of that still, preferred when both are present, so somebody named from a face
		 * looks like it. Never without the still: the server withholds them together.
		 */
		coverTrackId?: string | null;
		/** What ends the face-cover address, so the picture leaves the browser's store when hidden. */
		art?: string | null;
		/** Small facts under the name: a count of files, a count of handles, a site's kind. */
		detail?: string;
		/**
		 * What this thing HAS, as the hover card's row of glyphs and numbers (`cardCells`): cells, so the
		 * card still knows nothing of its kind. Empty draws no row.
		 */
		counts?: readonly CountCell[];
		/**
		 * What to draw when there is no picture, instead of the letter: a song is the music glyph, since
		 * a wall of songs is music and a letter reads as an initial. Absent is the monogram.
		 */
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
		/*
		 * What somebody thinks of this, where an opinion can be held. Both ABSENT draws no row (a tag is
		 * a word); `false`/`null` are answers (an empty heart), absence is no question.
		 */
		favorite?: boolean;
		rating?: number | null;
		/**
		 * Kept at the top of this wall by whoever is looking: a MARK, not a control (the pin is set from
		 * the menu and the bar, where row verbs live), answering "why is this one first". Beside the
		 * sharing marks, not folded in: where it sits is not who may see it.
		 */
		pinned?: boolean;
		/**
		 * Whether this person makes the edits: the mark in the card's bottom-right corner, a fact about
		 * the person rather than the card's place. Handed in, like `creatorName`.
		 */
		pmvCreator?: boolean;
		/**
		 * A LOCKED TILE: everything under this row that the viewer may see is Hidden, Hidden is shut,
		 * and "Show a locked tile" is on. The server withheld name, pictures and address (`_LOCKED_TILE`
		 * in `kernel/access/repository/entities.py`); the card stays because others count it, drawn
		 * as the locked file tile (`Tile.svelte`), "Hidden" for a name, with no link, menu, selection
		 * or heart. Pressing it asks for the PIN (`vaultPrompt`).
		 */
		locked?: boolean;
		onfavorite?: (favorite: boolean) => void;
		onrate?: (rating: number | null) => void;
		/** Drawn as a drop target while something is over it. Owned by whoever owns the drag. */
		dropping?: boolean;
		/**
		 * What a link dropped here would be aimed AT, for the words the offer says: about the drop, not
		 * the card. A Site's offer has a clause of its own (`dropOffer`).
		 */
		droppingKind?: AimedAt;
		/*
		 * The card's right-click menu, given here so the gesture learned on a file works on every kind;
		 * deleting is there and in the selection bar, the corners being the heart's and the rating's.
		 */
		menu?: Snippet;
		/**
		 * Picking several of these at once, the same way the media tiles are picked, so the two walls
		 * are one program. All of these arrive together or none do.
		 */
		selected?: boolean;
		/**
		 * This row's own id, readable off the DOM, since a sweep's pointer is elsewhere by the time it
		 * matters. Absent on a wall that does not pick.
		 */
		id?: string;
		onpressstart?: (event: PointerEvent) => void;
		onpressend?: () => void;
		/**
		 * The click, caught on the way DOWN: the name and the picture are anchors, and a bubbling
		 * handler would run after the browser began navigating.
		 */
		onclickcapture?: (event: MouseEvent) => void;
		/**
		 * Picking this card to filter the page's files by (an entity page's tabs), not `selected`, the
		 * verb selection; a card can be both. With `onpick` the picture is a toggle button
		 * (`aria-pressed`) and the name the one link; the pick wears `PickMark`: an accent wash and a
		 * filled funnel.
		 */
		picked?: boolean;
		onpick?: () => void;
		/**
		 * What this card offers in a swap, when the wall is in swap mode: its kind and id. The picture
		 * then picks for the swap, worn as `PickMark` with the swap's mark; the card reads the mode
		 * itself. `refused` (Kept local or "Don't swap", `refusedMark`) wears the pick in the danger
		 * colour, picks nothing, and says why (`sayRefused`).
		 */
		swapAs?: { kind: SwapKind; id: string; refused?: RefusedMark | null };
		/**
		 * What the page this wall is on knows about the thing on this card (a person's handles on a
		 * Site), as a snippet, inside the body but outside both anchors, since it may be pressed.
		 */
		beneath?: Snippet;
		/**
		 * A line directly under the name: who a song credits, outside both anchors. The snippet keeps
		 * the card's shape when nobody is credited.
		 */
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

	/* A Site's Sites within is drawn as its network mark, not a cell: it says what the Site IS. */
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

	/* The cover address with the face if there is one, else the whole frame; the server sends both or
	 * neither. Never the 112px recognizer crop, which would be blown up here. A card with no picture
	 * draws a letter on a colour fixed by the name, never a blank box that looks like a failed load. */
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

	/* Whether the picture above is the SHIPPED logo, always drawn as a mark: the `siteIcon` branch's
	   conditions, in its order. */
	const pictureIsTheShippedLogo = $derived(
		!coverUploadId &&
			!coverAssetId &&
			!(creatorName && creatorArt.has(creatorName)) &&
			Boolean(siteIcon)
	);

	/* The FILE's own still, while a cover chosen at a moment is still being rendered by its job: the
	 * wall's half of `EntityHeader`'s `insteadOf`. */
	const insteadOf = $derived(
		coverAssetId && !coverTrackId ? `/api/assets/${encodeURIComponent(coverAssetId)}/thumb` : null
	);

	/*
	 * Whether an opinion is actually held, which keeps its corner on show at rest; empty controls
	 * wait for the hover.
	 */
	const hearted = $derived(favorite === true);
	const rated = $derived(typeof rating === 'number' && rating > 0);

	/* The sweep's hook, spread FIRST so it cannot replace the class list; only where the card can be
	   picked, or the id would advertise a gesture that does nothing. */
	const sweepable = $derived(onclickcapture && id ? { [TILE_ID]: id } : {});

	/* In swap mode the press, the hold and the sweep are the swap's (`cardSweep`), and the card says
	   what it offers on itself for the sweep to read. */
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

	/*
	 * Whether the picture is a logo rather than a frame out of a clip: a frame is cropped to the wall's
	 * shape, a mark is contained. A Site's picture is a mark whatever its source, so its wall has one
	 * tile shape; so is a creator's small site profile picture, which filled would be enlarged and cut.
	 */
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
	<!-- No picture yet: the first letter of the name, since a kind's glyph says only what the wall
	     already says. -->
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
		<!-- Over the scrim, along the bottom edge; `aria-hidden`, since the menu and the page say the
		     names, and logos read aloud would come before the one thing the card is for. -->
		<span class="sites" aria-hidden="true">
			{#each sites as site (site.name)}
				<img class="site-mark" src={site.src} alt="" loading="lazy" decoding="async" />
			{/each}
		</span>
	{/if}
{/snippet}

{#snippet card()}
	<!-- svelte-ignore a11y_no_static_element_interactions: the card's own controls are still the
	     focusable things here; these three only observe a press that is already going to an anchor
	     or a button inside, and every one of them is reachable by keyboard on its own. -->
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
	>
		<!-- What the card says while something is held over it, since a ring alone says nothing of what. -->
		{#if dropping}
			<span class="taking" aria-hidden="true"
				><span>{droppingKind ? dropOffer(droppingKind) : 'Drop to add'}</span></span
			>
		{/if}
		<!-- The link wraps the picture and the words, NOT the controls: a heart inside an anchor would
		     navigate on the way to the button, as on the media tile. -->
		{#if onpick}
			<!-- The shared press surface, its look `PickMark`'s. `aria-pressed` rides through attributes,
			     not its `picked`, whose ring means selected for an action. `:global` reaches it below. -->
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
			<!-- The full name for a truncated one, in the app's tooltip, never a `title` (wrong typeface,
			     never on focus or touch). -->
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
					<!-- The app's own tooltip, as for the name above. -->
					<Tooltip label="Pinned to the top" placement="top">
						<span class="pinned"><Icon name="keep" size={14} /></span>
					</Tooltip>
				{/if}
			</span>

			<!-- What it has, the hover card's own row. One line ALWAYS, counts or none, so a wall stays a
			     grid of equal cards; what does not fit folds into "+n" (`fitCells`). -->
			<EntityCounts {name} {cells} fold={href} />
			{#if beneath}
				<!-- Unwrapped, so a card with nothing to say leaves no trace. -->
				{@render beneath()}
			{/if}
		</div>

		{#if pmvCreator}
			<!-- The mark, in the card's bottom-right corner, over the body, never over a face. Not a
			     control. -->
			<span class="creator-mark">
				<Tooltip label="PMV creator" placement="top">
					<Icon name="cinematic_blur" size={16} label="PMV creator" />
				</Tooltip>
			</span>
		{/if}

		{#if favorite !== undefined}
			<!-- The heart top-left and the rating top-right, outside the anchor; on hover, and always
			     where an opinion is held (`.judge`). -->
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
	<!-- A locked tile (`locked`): a separate drawing, so nothing later added to the card above can
	     reach a row whose name was withheld. -->
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
			<!-- The counts are why the tile is here; `inert`, since each links to the row's page. Drawn
			     with none too, to keep the height. -->
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
		/* The card's light, its edge a layer of the ground under this transparent border (see
		   `--sift-card`): it shows along the foot, under the name. */
		background: var(--sift-card);
		border: 1px solid transparent;
		overflow: hidden;
		transition:
			transform var(--dur-fast) var(--ease),
			box-shadow var(--dur-fast) var(--ease);
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

	/*
	 * The drop target, while something is over it: one 2px ring at the window-wide offer's weight, so
	 * it reads as "this will take it" rather than selected. An `outline` at -1px, since an inset
	 * shadow is painted under the picture and an outline is not clipped by `overflow: hidden`.
	 */
	.card.dropping {
		border-color: transparent;
		/* DASHED, the window-wide offer at a card's size; solid is what a selected card wears. */
		outline: 2px dashed var(--sift-accent);
		outline-offset: -1px;
	}

	/*
	 * SELECTED: `--selected-ring`'s three parts, as on a tile: the ring (an outline, for the drop
	 * ring's reasons), the picture pulled in, the tick in the top corner.
	 */
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

	/*
	 * What the card says while something is being held over it: "Drop to add", the window's sentence
	 * at the scale of what it will be filed under, on a LIGHT wash so the picture aimed at still
	 * shows. `pointer-events: none`, so the drop lands underneath.
	 */
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
		/* Nothing inside may make this taller: `aspect-ratio` is a preferred size, and a tall child
		   would stretch it. The children are out of flow below; this is the backstop. */
		overflow: hidden;
		background: var(--sift-surface-3);
		/* The monogram is a letter inside an anchor, which the browser would underline. */
		text-decoration: none;
	}

	/*
	 * Out of flow, so the box is sized by the ratio above alone; `:global`, Avatar's class.
	 * `aspect-ratio: auto`, or the avatar's own 3/4 would crop a 9:16 still more than the box does.
	 */
	.card :global(.face) > :global(.avatar) {
		position: absolute;
		inset: 0;
		aspect-ratio: auto;
	}

	/* The site marks, along the bottom of the cover on its scrim, wrapping upward rather than
	   squeezing. */
	.sites {
		position: absolute;
		inset-inline: var(--space-2);
		inset-block-end: var(--space-2);
		display: flex;
		flex-wrap: wrap-reverse;
		gap: var(--space-1);
		pointer-events: none;
	}

	/* Each mark at the row's size, straight on the scrim: a ground per mark would be a box drawn by
	   hand, which the fence counts. */
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

	/*
	 * The creator mark, pinned to the card's bottom-right corner, positioned on this file's own span
	 * outside the tooltip, whose wrapper is relative. The inherited ink, since a coloured corner
	 * would outshout the faces. A fact, not a control.
	 */
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

	/*
	 * Hidden at rest, shown on the card's hover or focus, unless the opinion is held (`.set`, per
	 * corner), so forty faces carry no forty idle controls. `:has(:focus-visible)`, since
	 * `focus-within` keeps the star showing after the heart is pressed. `opacity`, so the control
	 * stays in the tab order; a touch screen always shows both.
	 */
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

	/* The picture fills the face and is positioned, so it is painted over anything the face draws on
	   itself, an outline included: the ring is a layer of its own laid over the picture. */
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

	/* The corners appear at once rather than fading in. */
	:global(:root[data-motion='reduce']) .judge {
		transition: none;
	}
	/* On a phone the name grows its reach to the touch target above and below, as padding given back
	   as margin: it clips its overflow for the ellipsis, which would clip a ring too. */
	@media (max-width: 767px) {
		a.name {
			padding-block: calc((var(--touch-target) - 1lh) / 2);
			margin-block: calc((1lh - var(--touch-target)) / 2);
		}
	}
</style>
