<script lang="ts">
	/* The top of a person's page, or a site's: picture, name, counts and opinions, one component
	   for every kind with the fields handed in as snippets. The cover stands to one side, not as a
	   banner, which would crop the subject out of a portrait frame. */
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { coverUrl, wholeCoverUrl } from '$lib/entity/art';
	import { reveal } from '$lib/shell/motion.svelte';
	import {
		COVER_RATIO,
		isCentred,
		sameFrame,
		type CoverMore,
		type Frame
	} from '$lib/entity/cover-frame';
	import CoverFramer from '$lib/components/entity/CoverFramer.svelte';
	import EntityAbout from '$lib/components/entity/EntityAbout.svelte';
	import EntityCover from '$lib/components/entity/EntityCover.svelte';
	import { faceCoverUrl } from '$lib/people/faces.svelte';
	import { creatorArt } from '$lib/entity/creator-art.svelte';
	import { Button, ConfirmDialog, PictureViewer, Scroller } from '$lib/components/common';
	import RowMenu from '$lib/components/common/RowMenu.svelte';
	import NetworkMark from '$lib/components/entity/NetworkMark.svelte';
	import type { Maker } from '$lib/entity/enrich.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { theBackdrop } from '$lib/components/shell/backdrop';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import type { Verb } from '$lib/components/common/verbs';
	import { withDelete } from '$lib/components/entity/options';
	import type { PickChoice } from '$lib/components/common/verbs';
	import { rememberedFlag } from '$lib/shell/remembered.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import type { components } from '$lib/api/schema';
	import type { IconName } from '$lib/design/icons';

	interface Props {
		name: string;
		/** The kind's own glyph left of the name, the rail's mark for its wall (`iconOf`). */
		icon?: IconName;
		/* This thing's own page address (`/people/<id>`), which its picture comes from, since a
		   cover may name a video's moment (`coverUrl`). Absent falls back to the file's. */
		coverHref?: string;
		coverAssetId?: string | null;
		/** An uploaded cover: a picture put on the row rather than a file in the library. */
		coverUploadId?: string | null;
		/** Which moment of `coverAssetId` the cover is. It goes on the address, because the server
		 *  lets the browser keep only an address naming the cover (`kernel/covers.py`). */
		coverAtMs?: number | null;
		/** The window of the picture the cover is drawn as (`lib/entity/cover-frame.ts`), or null
		 *  for the whole of it. On the address for the same reason; Reframe opens on it. */
		coverFrame?: Frame | null;
		/** The name to ask for a site picture under, when nobody has chosen a cover. A person's own
		 *  name, and only a person's. See the card. */
		creatorName?: string | null;
		/** A Site's name. With no chosen cover it wears the site's own mark, which a download
		 *  already fetched. The same rule as `creatorName` above, one entity along. */
		siteName?: string | null;
		/** Sift's icon pack's picture of this site: its token, which names the logo on the cover
		 *  address so the browser may keep it; see `EntityCard.siteIcon`. */
		siteIcon?: string | boolean | null;
		/** A face out of that still. Preferred when present; see the card, which does the same. */
		coverTrackId?: string | null;
		/** What to put on the end of the face-cover address. Without it that picture stays
		 *  reachable from the browser's own store after the person is hidden. */
		art?: string | null;
		/** The facts under the name. Written by the screen: "12 items" means different things. */
		counts?: string;
		/** This account's O tally over the subject, after the counts; nought or absent draws
		   nothing. */
		oCount?: number | null;
		/** The heart and the stars, absent for a kind with no opinion of its own (a handle). The
		   row is drawn only when it can be changed or carries `enrichedBy` marks. */
		favorite?: boolean;
		rating?: number | null;
		onfavorite?: (favorite: boolean) => void;
		onrate?: (rating: number | null) => void;
		/** Which stash-boxes know this thing, newest agreement first, handed in from the links the
		   record panel fetched, so the two agree. */
		enrichedBy?: readonly { name: string; box: string | null }[];
		/** What made this thing (a box, Sift and its pass, or the account that asked); null draws
		   nothing. */
		madeBy?: Maker | null;
		/** The tags on this entity, and a way to take one off. Absent for a viewer who may not. */
		tags?: components['schemas']['TagOnEntity'][];
		onuntag?: (tagId: string) => void;
		/** Put a tag on: the row that was picked. Absent leaves the chips as labels. */
		ontag?: (tag: PickChoice) => void | Promise<void>;
		/** What this thing is, read-only: a `Record`, behind the band's More because the band does
		   not scroll. */
		record?: Snippet;
		/** The facts on the record that belong against the heading: a `RecordSummary`. */
		summary?: Snippet;
		/** The labelled facts, in the column beside the name where they have room: a `RecordFacts`. */
		facts?: Snippet;
		/** Whether an admin may edit at all. Without it the record is a readout. */
		mayEdit?: boolean;
		/** Change the picture: a pencil on the cover; what it may be chosen FROM is the page's to
		   know. */
		onpicture?: () => void;
		/** Write the chosen cover (a file and a moment, or `null`) and take the page's copy from
		   the reply. The control, its question and its Undo are here; taking it off brings the
		   automatic one back. */
		oncover?: (assetId: string | null, atMs: number | null, more?: CoverMore) => Promise<void>;
		/** A band of the page's own in the cover's column, short lines only; it waits for the
		   band's first open. */
		underCover?: Snippet;
		/** Whether the form is up, bound so the page can give it the screen. */
		editing?: boolean;
		/** What kind of thing this is, so the band is remembered per kind. */
		kind?: string;
		/** What the cover draws where nothing was chosen, in place of the letter. */
		glyph?: IconName;
		/** Whether this PERSON makes the edits: the mark right of the name. */
		pmvCreator?: boolean;
		/** How many Sites are part of this one, drawn as its network mark; nought draws nothing. */
		sitesWithin?: number;
		/** Everything that can be done to this thing, behind one worded door (`RowMenu`); `Delete`
		   is appended last here so no page forgets it. */
		options?: readonly Verb[];
		/** What the verbs act on. One id, from a page about one thing. */
		optionIds?: string[];
		/** The id of the form being edited, so Save stands up here beside Cancel through `<button
		   form>`. Absent where a page has no form. */
		saveForm?: string;
		/** Delete this thing after the question; each deletes a record and none touches a file,
		   which the question says. */
		ondelete?: () => Promise<void>;
		/** What this thing is, in the question. "person", "site", "tag". */
		deleteWord?: string;
	}

	let {
		name,
		icon,
		coverAssetId = null,
		coverUploadId = null,
		coverAtMs = null,
		coverFrame = null,
		coverHref,
		creatorName = null,
		siteName = null,
		siteIcon = false,
		coverTrackId = null,
		art = null,
		counts = '',
		oCount = null,
		favorite = false,
		rating = null,
		onfavorite,
		onrate,
		enrichedBy = [],
		madeBy = null,
		tags = [],
		onuntag,
		ontag,
		record,
		summary,
		facts,
		mayEdit = false,
		underCover,
		kind = 'entity',
		pmvCreator = false,
		sitesWithin = 0,
		editing = $bindable(false),
		onpicture,
		oncover,
		options,
		optionIds = [],
		saveForm,
		ondelete,
		deleteWord = 'record',
		glyph = undefined
	}: Props = $props();

	const listed = $derived(
		withDelete(options, Boolean(ondelete && mayEdit && !editing), () => (confirmDelete = true))
	);

	/** Whether the picture is open full size. Nothing outside this component needs to know. */
	let showingPicture = $state(false);

	/* Whether a cover has been chosen (a file or an upload), the only thing there is to take off. */
	const chosenCover = $derived(Boolean(coverAssetId || coverUploadId));

	/** Whether the question before an uploaded picture is removed is up, and whether it is going. */
	let confirmRemove = $state(false);
	let removing = $state(false);

	/* Take the chosen cover off. A file stays in the library, so no question and the toast carries
	   Undo; an uploaded picture's bytes go with it (`kernel/covers.py`), so that is asked. Read
	   before the write, which replaces these props. */
	async function removeCover(): Promise<void> {
		if (!oncover || removing) return;
		const was = { assetId: coverAssetId, atMs: coverAtMs, frame: coverFrame };
		removing = true;
		try {
			await oncover(null, null);
		} catch {
			toasts.show("The cover couldn't be removed", { tone: 'error' });
			return;
		} finally {
			removing = false;
			confirmRemove = false;
		}
		const assetId = was.assetId;
		toasts.show(
			'Cover removed',
			assetId
				? {
						tone: 'success',
						action: {
							label: 'Undo',
							run: () => {
								oncover(assetId, was.atMs, { frame: was.frame }).catch(() =>
									toasts.show("The cover couldn't be put back", { tone: 'error' })
								);
							}
						}
					}
				: { tone: 'success' }
		);
	}

	function askToRemoveCover(): void {
		if (coverUploadId) confirmRemove = true;
		else void removeCover();
	}

	/* Reframing the chosen cover, saved through the same write; a window centred at its largest is
	   saved as no frame, so an untouched Save writes nothing new. */
	let reframing = $state(false);
	let framing = $state<Frame | null>(null);
	let framingAspect = $state<number | null>(null);
	let savingFrame = $state(false);
	/** The whole picture the window is moved over: the cover's own, without its frame. */
	const wholePicture = $derived(
		coverHref && (coverUploadId || coverAssetId)
			? wholeCoverUrl(
					coverHref,
					art,
					coverUploadId ? { uploadId: coverUploadId } : { assetId: coverAssetId, atMs: coverAtMs }
				)
			: null
	);

	function openReframe(): void {
		framing = coverFrame;
		framingAspect = null;
		reframing = true;
	}

	async function saveFrame(): Promise<void> {
		if (!oncover || savingFrame || !framing) return;
		const aspect = framingAspect;
		const frame = aspect && isCentred(framing, aspect, COVER_RATIO) ? null : framing;
		/* Nothing moved: nothing is written. A write of the same window would be recorded as the
		   cover being chosen again, which nobody did. */
		if (sameFrame(frame, coverFrame)) {
			reframing = false;
			return;
		}
		savingFrame = true;
		try {
			if (coverUploadId) await oncover(null, null, { uploadId: coverUploadId, frame });
			else await oncover(coverAssetId, coverAtMs, { frame });
			reframing = false;
		} catch {
			toasts.show("The cover couldn't be reframed", { tone: 'error' });
		} finally {
			savingFrame = false;
		}
	}

	/** Whether the delete question is up, and whether the answer is being carried out. */
	let confirmDelete = $state(false);
	let deleting = $state(false);

	async function reallyDelete(): Promise<void> {
		if (!ondelete || deleting) return;
		deleting = true;
		try {
			await ondelete();
			confirmDelete = false;
		} finally {
			deleting = false;
		}
	}

	/* Open or shut, remembered per browser and per kind; shut by default, because somebody arriving
	   came for the files. It hides and shows the facts and nothing else. */
	const band = $derived(rememberedFlag(`sift.record.${kind}`, false));

	/* Whether the band is put away so the tabs start at the top: its height comes off the wall
	   while the page is open, so it is asked, and remembered per kind. The controls stay put while
	   it is open. */
	const shut = $derived(rememberedFlag(`sift.entity.compact.${kind}`, false));

	/* Whether the band has been open since this page was drawn: `underCover` asks the server once,
	   then stays. */
	let bandHasOpened = $state(false);
	$effect(() => {
		if (!shut.on) bandHasOpened = true;
	});

	/* The cover address rather than the crop: this draws a face larger than the card does. */
	const picture = $derived(
		/* An upload first: it is the only cover with no asset behind it. */
		coverUploadId && coverHref
			? coverUrl(coverHref, art, { uploadId: coverUploadId, frame: coverFrame })
			: coverAssetId && coverTrackId
				? faceCoverUrl(coverTrackId, art)
				: coverAssetId
					? /* The entity's own address, so a chosen moment is drawn. See `coverUrl`. */
						coverHref
						? coverUrl(coverHref, art, {
								assetId: coverAssetId,
								atMs: coverAtMs,
								frame: coverFrame
							})
						: `/api/assets/${encodeURIComponent(coverAssetId)}/thumb`
					: /* No chosen cover: the picture the site shows them with stands in. */
						creatorName && creatorArt.has(creatorName)
						? `/api/creator-art/${encodeURIComponent(creatorName)}`
						: siteIcon && coverHref
							? coverUrl(coverHref, art, {
									icon: typeof siteIcon === 'string' ? siteIcon : null
								})
							: null
	);

	/* The file's own still until the cover at a chosen moment, rendered by a queued job, stops
	   answering 404. Only for a file. */
	const insteadOf = $derived(
		coverAssetId && !coverTrackId ? `/api/assets/${encodeURIComponent(coverAssetId)}/thumb` : null
	);

	/* Whether the result is a logo rather than a frame; the card's rule, so a site is drawn one way. */
	const pictureIsAMark = $derived(
		!coverUploadId &&
			!coverAssetId &&
			!(creatorName && creatorArt.has(creatorName)) &&
			Boolean(siteIcon)
	);

	/* The same picture handed to the frame's backdrop, at the same address so no second request;
	   cleared on the way out (`shell/backdrop.ts`). */
	const backdrop = theBackdrop();
	$effect(() => {
		backdrop.stand(picture);
		return () => backdrop.stand(null);
	});
</script>

<!-- The title row: `PageHeader`, as every wall draws it, so titles share a height. Always drawn: it
     holds the control that puts the band away. -->
{#snippet pageControls()}
	{#if record && mayEdit}
		<!-- One button, two states: the way out of editing is the control that got you in. The other
		     actions stay put while the form is up, so the row does not change width. -->
		{#if editing}
			<Button onclick={() => (editing = false)}>Cancel</Button>
			<!-- Save beside the way out of editing: the same act as the form's own Save at its foot. -->
			{#if saveForm}
				<Button type="submit" form={saveForm} icon="save" tone="primary">Save</Button>
			{/if}
		{:else}
			<Button icon="edit" onclick={() => (editing = true)}>Edit</Button>
		{/if}
	{/if}
	{#if listed.length > 0}
		<!-- One door, worded, exactly as a file's own screen wears it. It stays put while the form
			     is up rather than vanishing, for the reason above, but Delete is not in it then, so
			     Save and Delete are still never side by side. -->
		<RowMenu verbs={listed} ids={optionIds} label="Options for {name}" words="Options" />
	{/if}

	<!-- Put this band away so the list starts at the top of the screen (not fullscreen: the window
	     is left as it is). The glyph shows the act; never while the form is up. -->
	{#if !editing}
		<Tooltip label={shut.on ? 'Show the details' : 'Hide the details'}>
			<Button
				icon={shut.on ? 'close_fullscreen' : 'open_in_full'}
				pressed={shut.on}
				onclick={() => (shut.on = !shut.on)}
				aria-label={shut.on ? 'Show the details' : 'Hide the details'}
			/>
		</Tooltip>
	{/if}
{/snippet}

<!-- Beside the name: it says whether this thing's facts are showing. -->
{#snippet disclosure()}
	<Button
		icon={band.on ? 'expand_less' : 'expand_more'}
		tone="ghost"
		size="small"
		onclick={() => (band.on = !band.on)}
		aria-expanded={band.on}
	>
		{band.on ? 'Less' : 'More'}
	</Button>
{/snippet}

<!-- The mark belongs to the name, so it comes before the disclosure; both ride in `beside`, the one
     slot a title row gives. -->
{#snippet besideName()}
	{#if sitesWithin > 0}
		<span class="network-mark"><NetworkMark count={sitesWithin} /></span>
	{/if}
	{#if pmvCreator}
		<Tooltip label="PMV creator" placement="top">
			<span class="verified"><Icon name="cinematic_blur" size={20} label="PMV creator" /></span>
		</Tooltip>
	{/if}
	{#if record}{@render disclosure()}{/if}
{/snippet}

<!-- Everything above the tabs stands on this thing's cover, blurred, on `PageFrame`'s layer
     (`.frame-backdrop`); `theBackdrop()` names the picture. -->
<div class="top">
	<!-- A box of this file's own, because a scoped rule cannot reach `PageHeader`'s root to stand it
	     above the backdrop. -->
	<div class="title-row">
		<PageHeader
			{icon}
			title={name}
			beside={record || pmvCreator || sitesWithin > 0 ? besideName : undefined}
			controls={pageControls}
		/>
	</div>

	<!-- The band opens and closes over `--dur-base` by the filter drawer's arrangement, staying in
	     the DOM while shut, kept out of reach by `aria-hidden` and a delayed `visibility`. -->
	<div class="band" class:open={!shut.on} aria-hidden={shut.on}>
		<div class="held">
			<header class="hero">
				<!-- The picture and whatever the page keeps under it, in a box of its own so a row added
				     under the cover does not resize the hero's other areas. -->
				<div class="cover-column">
					<EntityCover
						{name}
						{picture}
						{insteadOf}
						mark={pictureIsAMark}
						{glyph}
						{editing}
						{mayEdit}
						{onpicture}
						onremove={mayEdit && oncover && chosenCover ? askToRemoveCover : undefined}
						onreframe={mayEdit && oncover && chosenCover && wholePicture ? openReframe : undefined}
						{removing}
						onenlarge={() => (showingPicture = true)}
					/>
					<!-- Under the picture, and it waits for the first open for the same reason the band
					     under the tags does. See `bandHasOpened`. -->
					{#if bandHasOpened}
						{@render underCover?.()}
					{/if}
				</div>

				<EntityAbout
					{name}
					{summary}
					{counts}
					{oCount}
					{favorite}
					{rating}
					{onfavorite}
					{onrate}
					{enrichedBy}
					{madeBy}
					{tags}
					{onuntag}
					{ontag}
					{editing}
				/>

				{#if facts || (record && band.on)}
					<!-- The facts beside the name: the labelled facts always, the rest once asked
					     for. What waits on the record is in the History tab
					     (`EntityHistory.waiting`). -->
					<div class="facts">
						<Scroller>
							<!-- The layout goes on a box inside the scroller, which the library renders unscoped. -->
							<div class="stack">
								{@render facts?.()}
								<!-- More and Less open the record over the app's disclosure (`reveal`), one step
								     quicker than a disclosure because it is a column of short facts. `|local`, so
								     the page arriving does not slide an open record in. -->
								{#if record && band.on}
									<div class="record-fold" transition:reveal|local={{ pace: 'base' }}>
										{@render record()}
									</div>
								{/if}
							</div>
						</Scroller>
					</div>
				{/if}
			</header>
		</div>
	</div>
</div>

<!-- The picture, full size and with nothing else on it, in the viewer the player's still uses. -->
{#if picture}
	<PictureViewer bind:open={showingPicture} src={picture} {name} />
{/if}

{#if oncover}
	<!-- Only for an UPLOADED picture, which is gone once it stops being the cover. A file taken off
	     is still in the library and the toast after it carries Undo instead. -->
	<ConfirmDialog
		bind:open={confirmRemove}
		title="Delete this cover?"
		consequence="This picture was uploaded rather than chosen from your files, so it's deleted with the cover and can't be put back. The {deleteWord} goes back to the picture Sift picks for it."
		confirmLabel="Delete"
		destructive
		confirmDisabled={removing}
		onconfirm={() => void removeCover()}
	/>
{/if}

{#if oncover && wholePicture}
	<!-- The picture editor's sheet: the same dialog, crop control (`CoverFramer` on `CropStage`)
	     and foot as Modify; the act closes it before the write lands, and a failure toasts. -->
	<ConfirmDialog
		bind:open={reframing}
		title="Reframe the cover"
		consequence="Drag the frame, or a corner of it to zoom. Every card shows what is inside it."
		confirmLabel="Save"
		destructive={false}
		confirmDisabled={savingFrame || !framing}
		onconfirm={() => void saveFrame()}
	>
		{#snippet extra()}
			{#if reframing}
				{#key wholePicture}
					<CoverFramer
						src={wholePicture}
						bind:frame={framing}
						ratio={COVER_RATIO}
						onmeasured={(aspect) => (framingAspect = aspect)}
					/>
				{/key}
			{/if}
		{/snippet}
	</ConfirmDialog>
{/if}

{#if ondelete}
	<!-- Every one of these deletes a record and none touches a file, so the sentence says so. -->
	<ConfirmDialog
		bind:open={confirmDelete}
		title="Delete {name}?"
		consequence="This removes the {deleteWord} and everything recorded about it. Your files aren't touched. They stay exactly where they are, and stop counting under this {deleteWord}."
		confirmLabel="Delete"
		destructive
		confirmDisabled={deleting}
		onconfirm={() => void reallyDelete()}
	/>
{/if}

<style>
	/* The creator mark right of the name, in the inherited ink so it does not read as a status
	   granted. `inline-flex` sits the 20px glyph on the name's box rather than its baseline. */
	.verified {
		display: inline-flex;
		align-items: center;
	}

	/* The network mark in the quiet ink and type of the counts under the name: a fact about the
	   Site, read after its name. */
	.network-mark {
		display: inline-flex;
		align-items: center;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}

	/* The wrapper around the title row and the band. It moves nothing (a person's title is on the line
	   every wall's title is on); it is positioned because the band positions things inside it. */
	.top {
		position: relative;
	}

	/* Positioned so it paints after the frame's backdrop, and for no other reason. See the markup. */
	.title-row {
		position: relative;
	}

	/* Three columns and two rows; the second row is `minmax(0, 1fr)` so the record cannot lengthen
	   the header. The middle column is capped so the name wraps first. */
	.hero {
		display: grid;
		grid-template-columns: var(--entity-cover-width) minmax(14rem, 24rem) minmax(0, 1fr);
		grid-template-rows: auto minmax(0, 1fr);
		grid-template-areas:
			'cover about .'
			'cover about facts';
		gap: var(--space-4) var(--space-6);
		padding-block-start: var(--space-3);
	}

	/* Only the panel stretches: it fits a box rather than setting one. */
	.cover-column {
		align-self: start;
	}

	/* The picture and the page's band under it, as one column. */
	.cover-column {
		grid-area: cover;
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		min-inline-size: 0;
	}

	/* The band opening and closing: one row whose fraction animates (`FilterBar`'s `.drawer`), shut
	   in the stylesheet and opened by the class, so a band remembered shut never flashes open. */
	.band {
		display: grid;
		grid-template-rows: 0fr;
		transition: grid-template-rows var(--dur-base) var(--ease);
		/* Positioned so it paints after the frame's backdrop behind it, like the title row above.
		   See the markup: nothing here is ordered by a z-index. */
		position: relative;
	}

	.band.open {
		grid-template-rows: 1fr;
	}

	/* `min-block-size: 0` is what makes the closed state closed: a bare `0fr` track is
	   `minmax(auto, 0fr)`, floored at the content's height. */
	.held {
		overflow: hidden;
		min-block-size: 0;
	}

	/* Out of reach while shut: a clipped row's contents can still be focused and read out.
	   `visibility` waits for the close to finish; opening is reachable at once. */
	.band[aria-hidden='true'] .held {
		visibility: hidden;
		transition: visibility var(--dur-base) linear;
	}

	.band.open .held {
		visibility: visible;
		transition: none;
	}

	/* The record beside the name scrolls inside the header's height, because this band does not
	   scroll with the page. `min-block-size: 0` keeps the box from laying out at its content. */
	.facts {
		grid-area: facts;
		min-block-size: 0;
		min-inline-size: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	/* Inside the one scrolling region: what waits on the record, then the record; the gap is here
	   since both are inside the scroller. */
	.stack {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	/* The record under More, in one box so it opens and closes as one block; its parts keep the
	   stack's own gap between them. */
	.record-fold {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	/* Two columns on a phone: the picture keeps its place beside the name and the facts take the
	   width under both. The rows size to their content, so no scroller scrolls inside the page. */
	@media (max-width: 767px) {
		.hero {
			grid-template-columns: var(--entity-cover-width) minmax(0, 1fr);
			grid-template-rows: auto;
			grid-template-areas:
				'cover about'
				'facts facts';
			gap: var(--space-4);
		}
	}
</style>
