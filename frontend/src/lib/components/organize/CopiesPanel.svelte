<script lang="ts">
	/* The same bytes in more than one place: which copies to keep is somebody's decision. Paged, with
	 * the total above; nothing is deleted without a confirm naming the path and what it frees. */
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
	 * Paged as every Organize list at the server's size, the page in the address (`from`, `near`).
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

	/** The page on screen, its start written once landed; a release reads the same page back. */
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

	/* Arrived pointing at one row from the board (`_copy_anchor`); once per fragment. */
	let revealed = '';

	$effect(() => {
		const wanted = address.url.hash.slice(1);
		if (!wanted || wanted === revealed || view.redundancies.length === 0) return;
		if (revealAnchored(wanted)) revealed = wanted;
	});

	/*
	 * A closer look through `openAsset`; a file's name is a short id, its paths tell copies apart.
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

	/* The copy being let go, never the total. */
	const releaseFrees = $derived(releasing ? formatBytes(releasing.copy.size_bytes) : '');

	/* The page in one press: keep each marked copy, let the rest go. */
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
	/* "the marked copy", which may be a hand's choice. */
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

	/** The line over each card, the near-duplicate queue's sentence. */
	function shapeOf(asset: Redundancy): string {
		const going = asset.copies.length - 1;
		return `${counted(asset.copies.length)} copies, ${counted(going)} would be deleted, frees ${formatBytes(asset.reclaimable_bytes)}`;
	}

	/* What the page is a part of, never a bare list. */
	const standing = $derived.by(() => {
		if (!view.loaded) return '';
		const files = view.totalRedundant === 1 ? 'file' : 'files';
		/* The pager says the position; no second readout. */
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
		<!-- The near-duplicate queue's strip. -->
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
				<!-- Named so a board still can point at this row (`_copy_anchor`). -->
				<li class="group" id="copy-{asset.asset_id}">
					<header>
						<!-- Always Identical: the same bytes. -->
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
									<!--
									The same picture on every tile; the path tells them apart.
									-->
									<img
										src={thumbUrl({ id: asset.asset_id, art: asset.art })}
										alt=""
										loading="lazy"
									/>
								</Pressable>

								<p class="name"><FileName name={copy.filename} /></p>
								<!--
								The whole path, never `rel_path`, which names a Hidden folder.
								-->
								<p class="where"><PathText path={copy.path ?? copy.filename} /></p>
								<!-- Shape and length off the asset, the same for every copy. -->
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
										The override writes nothing; the page press writes
										(`copyKeeperOf`).
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
	/* The near-duplicate queue's card and tiles, as two tabs of one job. */
	section {
		position: relative;
	}

	/* In the bar beside the press. */
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

	/* `auto-fit`, so tiles fill the row. */
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

	/* The copy's two presses at the tile's foot, wrapping rather than squeezing. */
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

	/* The kept copy marked on the file, which is what is skimmed for. */
	.keeping {
		outline: 2px solid var(--sift-accent);
		outline-offset: var(--space-2);
		border-radius: var(--radius-sm);
	}

	/* The outline on the picture, reserved transparent; global, as Pressable's. */
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
		/* Square and uncropped: nothing may be hidden when choosing a copy to delete. */
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

	/* Words beside the tick, at a control's height. */
	.kept {
		display: flex;
		align-items: center;
		min-block-size: var(--control-height);
		gap: var(--space-1);
		font: var(--text-label);
		color: var(--sift-accent-text);
	}
</style>
