<script lang="ts">
	/*
	 * The same file, byte for byte, in more than one place. Which copies do you want?
	 *
	 * Next door to Near Duplicates and asking a different question. That one asks whether two files
	 * ARE the same thing, which no threshold settles. This one already knows they are: identical
	 * bytes were resolved into one asset with several locations the moment they were imported, so
	 * there is nothing to judge. What is left is still a decision and still nobody else's: a second
	 * copy on a second disk may be exactly what somebody wanted, and no rule can know that.
	 *
	 * ## Why it is paged
	 *
	 * A library can hold thousands of these. Drawing every one with every path under it, and asking
	 * again on a timer to see whether the number had grown, is a library-sized read for nothing. A
	 * page at a time, with the whole-library total said above it, is the same information.
	 *
	 * Nothing here deletes anything without a confirm naming the path that goes and what it frees.
	 */
	import {
		Button,
		ConfirmDialog,
		Empty,
		Pressable,
		Problem,
		Skeleton
	} from '$lib/components/common';
	import PanelBar from '$lib/components/organize/PanelBar.svelte';
	import { page as address } from '$app/state';
	import { revealAnchored } from '$lib/organize/anchor';
	import { toasts } from '$lib/shell/toasts.svelte';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import { onDestroy, untrack } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import PathText from '$lib/components/PathText.svelte';
	import FileName from '$lib/components/organize/FileName.svelte';
	import { openAsset } from '$lib/player/asset-view';
	import { thumbUrl } from '$lib/entity/art';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import { answered } from '$lib/organize/organize.svelte';
	import {
		Maintenance,
		formatBytes,
		formatDimensions,
		formatDuration,
		type Copy,
		type Redundancy
	} from '$lib/settings-ui/maintenance-state.svelte';
	import { counted } from '$lib/entity/entity-counts';

	const view = new Maintenance();

	let problem = $state<string | undefined>(undefined);
	let releasing = $state<{ asset: Redundancy; copy: Copy } | null>(null);
	let releaseOpen = $state(false);

	/*
	 * Its own half only: this card has no use for the review queue's list, and reading it would be
	 * a request for something nothing on this screen draws.
	 *
	 * Which page: the same paging every list in Organize has (`CardPaging`), at the server's own
	 * page size, with the file the page starts at written into the address as `from` (and where it
	 * was, `near`), so the way back lands here again. The address names a row, and a row that has
	 * gone is answered with where the page was. See `DuplicatesPanel`.
	 */
	const PAGE = 24;
	const paging = new CardPaging(PAGE);
	const path = address.url.pathname;
	let arriving = true;

	/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. */
	let { onpaging }: { onpaging?: OnPaging } = $props();
	$effect(() => {
		onpaging?.(paging.asPager(view.redundancies.length, view.totalRedundant, 'files'));
	});
	onDestroy(() => onpaging?.(null));

	/** The page on screen, through the paging, and where it starts written into the address once it
	 *  has landed. Also what a release reads back: the SAME page, which one copy leaving does not
	 *  move, and a page it emptied steps back to where the list now ends (`CardPaging.fill`). */
	async function load(): Promise<void> {
		if (!(await view.fillReclaim(paging))) return;
		rememberAnchor(address.url, path, view.redundancies[0]?.asset_id, paging.offset);
	}

	/* A page turned. The anchor in the address is honoured once, on arrival. */
	$effect(() => {
		void paging.offset;
		void paging.size;
		if (arriving) {
			arriving = false;
			paging.arrive(untrack(() => anchorIn(address.url)));
		}
		untrack(() => void load());
	});
	whenChanged(libraryChanges, () => void load());

	/* Arrived here pointed at one row: a still on the board's Duplicates card. See
	   `$lib/organize/anchor` for why the browser cannot do this by itself, and `_copy_anchor` on the
	   server for the other half of the name. Once per fragment: the list is re-read whenever the
	   library moves, and scrolling somebody back every time would take the page off whatever they
	   had moved on to. */
	let revealed = '';

	$effect(() => {
		const wanted = address.url.hash.slice(1);
		if (!wanted || wanted === revealed || view.redundancies.length === 0) return;
		if (revealAnchored(wanted)) revealed = wanted;
	});

	/*
	 * A closer look: the player, opened over this screen through `openAsset`, as every other wall
	 * in Sift does. The list is empty because a row here is one file in several places, so there is
	 * nothing to step to.
	 *
	 * What to call a file in a sentence: a short form of its id, because every row here is one
	 * asset in several places, and what tells the copies apart is the path under each, drawn in
	 * full.
	 */
	function nameOf(assetId: string): string {
		return `the file ending ${assetId.slice(-6)}`;
	}

	function askToRelease(asset: Redundancy, copy: Copy) {
		releasing = { asset, copy };
		releaseOpen = true;
	}

	async function doRelease() {
		if (!releasing) return;
		problem = await view.release(releasing.asset.asset_id, releasing.copy.location_id);
		if (problem === undefined) await load();
		// The board's counts and the record behind it both changed. Nothing else announces it.
		answered.changed();
	}

	/* The size the confirm quotes. The copy being let go, never the total, which would promise
	   more than this one press delivers. */
	const releaseFrees = $derived(releasing ? formatBytes(releasing.copy.size_bytes) : '');

	/*
	 * The page in one press: for every file on it, keep the copy the rule marks (the biggest; see
	 * `biggest`) and let the rest go, as the near-duplicate queue does.
	 */
	let pageOpen = $state(false);
	const pageGoing = $derived(
		view.redundancies.flatMap((asset) => {
			const keeper = view.copyKeeperOf(asset);
			return asset.copies
				.filter((copy) => copy.location_id !== keeper)
				.map((copy) => ({ asset_id: asset.asset_id, location_id: copy.location_id, copy }));
		})
	);
	const pageFrees = $derived(
		formatBytes(pageGoing.reduce((sum, one) => sum + (one.copy.size_bytes ?? 0), 0))
	);
	/* "the marked copy", not "the biggest": a copy somebody marked by hand is what this press keeps
	   now, and a sentence naming the rule would be describing a choice it may not be making. */
	const pageConsequence = $derived(
		`Across ${counted(view.redundancies.length)} ${view.redundancies.length === 1 ? 'file' : 'files'}, Sift keeps the marked copy of each and permanently deletes the other ${pageGoing.length} from your disk, freeing ${pageFrees}. Every file keeps its tags and rating. This can't be undone.`
	);

	async function releasePage() {
		const done = await view.releaseMany(
			pageGoing.map(({ asset_id, location_id }) => ({ asset_id, location_id }))
		);
		if (typeof done === 'string') {
			problem = done;
			return;
		}
		// The same page read back: what was on it moved on, and what followed has closed up.
		await load();
		problem = undefined;
		toasts.show(
			done.refused === 0
				? `${counted(done.released)} ${done.released === 1 ? 'copy' : 'copies'} deleted`
				: `${counted(done.released)} deleted. ${counted(done.refused)} couldn't be deleted.`,
			{ tone: done.refused === 0 ? 'success' : undefined }
		);
	}

	/** The line over each card: how many copies, how many would go, and what that frees. The same
	    sentence the near-duplicate queue writes, because it is the same fact. */
	function shapeOf(asset: Redundancy): string {
		const going = asset.copies.length - 1;
		return `${counted(asset.copies.length)} copies, ${counted(going)} would be deleted, frees ${formatBytes(asset.reclaimable_bytes)}`;
	}

	/* What the page is a part of. Never a bare list: a page of twenty-four out of several
	   thousand, drawn with no total, reads as a library with twenty-four duplicates in it. */
	const standing = $derived.by(() => {
		if (!view.loaded) return '';
		const files = view.totalRedundant === 1 ? 'file' : 'files';
		/*
		 * The pager under the list says where this page is, so there is no second readout of the
		 * same position to disagree with it.
		 */
		const shown = '';
		const hidden = view.concealed > 0 ? ' Some are hidden in the vault.' : '';
		return `${formatBytes(view.totalReclaimable)} could be freed across ${view.totalRedundant} ${files}.${shown}${hidden}`;
	});
</script>

<section>
	<Problem message={problem ?? view.problem} />

	{#if view.loading && !view.loaded}
		<Skeleton lines={3} />
	{:else if !view.loaded}
		<Problem message="Exact duplicates couldn't be loaded. Refresh the page to try again." />
	{:else if view.totalRedundant === 0}
		<Empty scope="page" icon="file_copy" title="No exact duplicates">
			No file is stored more than once, so there's nothing to delete.
		</Empty>
	{:else}
		<!-- The same strip the near-duplicate queue wears: the sentence about the whole queue and
		     the one press that settles the page. -->
		<PanelBar>
			<p class="total">{standing}</p>
			<Button
				tone="primary"
				icon="delete"
				disabled={view.busy || pageGoing.length === 0}
				onclick={() => (pageOpen = true)}
			>
				Delete the {pageGoing.length} extra {pageGoing.length === 1 ? 'copy' : 'copies'} on this page
			</Button>
		</PanelBar>

		<ul class="groups">
			{#each view.redundancies as asset (asset.asset_id)}
				{@const keeper = view.copyKeeperOf(asset)}
				<!-- Named so a still on the board can point at THIS row. A group here has no address of
				     its own (it is one asset in several places, on a paged queue) so the anchor is
				     the asset's id and `slices/dedup/queue._copy_anchor` is what writes the other half
				     of it. See `revealed` above for why arriving is not enough on its own. -->
				<li class="group" id="copy-{asset.asset_id}">
					<header>
						<!-- The same chip the near-duplicate queue wears, and it always says the same thing
						     here: these are the same bytes, which is what makes them a different question
						     from the one next door. -->
						<span class="closeness">Identical</span>
						<span class="shape data">{shapeOf(asset)}</span>
					</header>

					<div class="files">
						{#each asset.copies as copy (copy.location_id)}
							<div class="file" class:keeping={copy.location_id === keeper}>
								<Pressable
									class="preview"
									feedback="none"
									radius="sm"
									onclick={() => openAsset(asset.asset_id, [])}
									aria-label="Open {copy.filename}"
								>
									<!-- Every copy is the same bytes, so every tile draws the same picture,
									     which is the point: what tells them apart is underneath. -->
									<img
										src={thumbUrl({ id: asset.asset_id, art: asset.art })}
										alt=""
										loading="lazy"
									/>
								</Pressable>

								<p class="name"><FileName name={copy.filename} /></p>
								<!-- The whole path, as the server says it: two copies of one file usually differ
								     only in which library folder they are in. Never `rel_path`, which names a
								     folder Hidden hides. -->
								<p class="where"><PathText path={copy.path ?? copy.filename} /></p>
								<!-- The same three facts a near-duplicate tile prints. The shape and the length
								     come off the ASSET, because every copy is the same bytes: printing them per
								     copy would be one number twice with a hint that it might differ. -->
								<p class="facts data">
									{formatBytes(copy.size_bytes)}{formatDimensions(asset.width, asset.height)
										? ` \u00b7 ${formatDimensions(asset.width, asset.height)}`
										: ''}{asset.duration_ms ? ` \u00b7 ${formatDuration(asset.duration_ms)}` : ''}
								</p>

								{#if copy.location_id === keeper}
									<p class="kept"><Icon name="check_circle" /> Keeping this one</p>
								{:else}
									<div class="acts">
										<!--
											The override, and it writes nothing: the page press is
											what writes.

											This tab lights the biggest copy and works the same
											figure out again for its page press, so without an
											override there is no way to say "keep that one instead",
											and clicking anything in a group would change nothing.
											Its sibling tab has the same, and the two are tabs of
											one screen. See `copyKeeperOf`.
										-->
										<Button
											tone="ghost"
											disabled={view.busy}
											aria-label="Keep {copy.filename} instead"
											onclick={() => view.chooseCopy(asset, copy.location_id)}
										>
											Keep this instead
										</Button>
										<Button
											tone="ghost"
											icon="delete"
											onclick={() => askToRelease(asset, copy)}
											disabled={view.busy || asset.copies.length < 2}
										>
											Delete this copy
										</Button>
									</div>
								{/if}
							</div>
						{/each}
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<ConfirmDialog
	bind:open={pageOpen}
	title="Delete every extra copy on this page?"
	consequence={pageConsequence}
	confirmLabel="Delete {counted(pageGoing.length)} {pageGoing.length === 1 ? 'copy' : 'copies'}"
	destructive
	onconfirm={() => void releasePage()}
/>

<ConfirmDialog
	bind:open={releaseOpen}
	title="Delete this copy?"
	consequence={releasing
		? `${releasing.copy.path ?? releasing.copy.filename} is deleted from your disk, freeing ${releaseFrees}. The file stays in its other ${releasing.asset.copies.length - 1 === 1 ? 'place' : 'places'} and keeps its tags and rating. This can't be undone.`
		: ''}
	confirmLabel="Delete copy"
	destructive
	onconfirm={doRelease}
/>

<style>
	/*
	 * The same card and the same tiles as the near-duplicate queue next door, deliberately.
	 *
	 * They are two tabs of one screen. Two shapes for "several files, one of them stays" (a
	 * thumbnail beside a list of paths here, a card of file tiles with a keeper marked there) would
	 * be two things to learn for one job, and the tab strip between them says they are one job.
	 *
	 * What stays different is what the two questions actually differ on: the chip always reads
	 * *Identical* here because these are the same bytes, and there is no rule to choose a keeper by,
	 * only the largest copy, which is what the reclaimable figure has always counted against.
	 */
	section {
		position: relative;
	}

	/* In the bar, beside the press: it takes the room and the button sits at the end. Its
	   measure is the bar's (`PanelBar`). */
	.total {
		flex: 1 1 24ch;
		margin: 0;
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	/* Machine facts line up in a column, so a stack of sizes can be read down rather than across. */
	.data {
		font: var(--text-data);
		font-variant-numeric: tabular-nums;
	}

	.groups {
		list-style: none;
		margin: 0 0 var(--space-4);
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
	}

	.group {
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		padding: var(--space-4);
		background: var(--sift-surface-1);
	}

	.group header {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		flex-wrap: wrap;
		margin-block-end: var(--space-4);
	}

	.closeness {
		padding: var(--space-1) var(--space-3);
		border-radius: var(--radius-full);
		background: var(--sift-surface-3);
		color: var(--sift-ink);
		font: var(--text-body-sm);
	}

	.shape {
		color: var(--sift-ink-3);
		font: var(--text-micro);
	}

	/* `auto-fit`, so the tiles FILL the row rather than leaving empty tracks beside them. See the
	   near-duplicate panel, where `auto-fill` would draw two small tiles and seven columns of nothing. */
	.files {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(200px, 100%), 300px));
		justify-content: start;
		gap: var(--space-4);
	}

	.file {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* The two things that can be done to a copy that is not the keeper, at the end of its tile:
	   actions right, everything that is read left. They wrap rather than squeeze: a tile here is
	   between 200 and 300 pixels wide and two buttons do not fit across it. */
	/* At the tile's foot, so the presses of a group stand on one line whatever the name above took. */
	.acts,
	.kept {
		margin-block-start: auto;
	}

	.acts {
		display: flex;
		flex-wrap: wrap;
		justify-content: flex-end;
		gap: var(--space-2);
	}

	/* The copy that stays if nothing is pressed. Marked on the FILE rather than only in its words,
	   because a page of these is read by looking for what is lit. */
	.keeping {
		outline: 2px solid var(--sift-accent);
		outline-offset: var(--space-2);
		border-radius: var(--radius-sm);
	}

	/* Something happens under the pointer, on the PICTURE rather than on the button around it: the
	   thumbnail fills the control, so an outline on the control would be drawn under the image and
	   invisible. `:global` because the class is handed to `Pressable`. */
	/* Reserved transparent at rest, so the outline STEPS in rather than appearing between two
	   frames. An outline is drawn outside the box, so reserving it moves nothing. */
	.file :global(.preview img) {
		outline: 2px solid transparent;
		outline-offset: 2px;
		transition: outline-color var(--dur-instant) var(--ease);
	}

	.file :global(.preview:hover:not(:disabled) img) {
		outline: 2px solid var(--sift-line-strong);
		outline-offset: 2px;
	}

	.file img {
		display: block;
		inline-size: 100%;
		block-size: auto;
		/* Square rather than 4:3, and that is about what is IN these queues. A picture is drawn
		   `contain`, never cropped: you are choosing which copy to delete, so nothing may be
		   hidden. And much of what these queues hold is portrait. In a wide 4:3 box a portrait file
		   renders short and narrow with grey either side; a square box of the same width gives it a third
		   more height and the same care about not cropping. */
		aspect-ratio: 1 / 1;
		max-block-size: 300px;
		object-fit: contain;
		background: var(--sift-surface-3);
		border-radius: var(--radius-sm);
	}

	.name,
	.where,
	.facts,
	.kept {
		margin: 0;
		overflow-wrap: anywhere;
	}

	.name {
		font: var(--text-body-sm);
		color: var(--sift-ink);
	}

	.where {
		font: var(--text-micro);
		color: var(--sift-ink-3);
	}

	.facts {
		font: var(--text-micro);
		color: var(--sift-ink-2);
	}

	/* The words beside the tick, so the mark is not colour alone. A control's height, so its words
	   stand on the line of the keep press beside it. */
	.kept {
		display: flex;
		align-items: center;
		min-block-size: var(--control-height);
		gap: var(--space-1);
		font: var(--text-label);
		color: var(--sift-accent-text);
	}
</style>
