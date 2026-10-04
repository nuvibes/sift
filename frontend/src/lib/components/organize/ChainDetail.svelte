<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * One chain of look-alike files, on a screen of its own.
	 *
	 * A chain is a group past the cap: every pair inside it is under the closeness dial and its two
	 * ends may look nothing alike, so no rule marks a keeper and the queue offers nothing on it.
	 * Here every file can be looked at: pressing one opens the player with the chain as what it
	 * steps through.
	 *
	 * Reached by the only name a group has, its method and its smallest file (the key the queue
	 * pages by), so the address survives a reload and a dial that has moved answers with a plain
	 * sentence rather than a stale page.
	 *
	 * Every file carries the one decision a chain has: keep this one, and the rest go, through the
	 * same delete dialog every file list uses, with its two tiers, and the same delete path
	 * (`AssetActions.remove`). Nothing is chosen for you; what is offered is the press.
	 */
	import { page } from '$app/state';
	import { isMissing } from '$lib/api/client';
	import { openAsset } from '$lib/player/asset-view';
	import { thumbUrl } from '$lib/entity/art';
	import {
		Button,
		Empty,
		Pressable,
		Problem,
		Selection,
		SettingLink,
		Skeleton
	} from '$lib/components/common';
	import DeleteDialog, { type DeleteMode } from '$lib/components/DeleteDialog.svelte';
	import { AssetActions, type Actionable } from '$lib/grid/actions.svelte';
	import { session } from '$lib/shell/session.svelte';
	import OrganizeHeader from '$lib/components/organize/OrganizeHeader.svelte';
	import PathText from '$lib/components/PathText.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import { organizeCrumbs } from '$lib/organize/bands';
	import { heldBoard } from '$lib/organize/organize.svelte';
	import {
		describeCloseness,
		fetchGroup,
		formatBytes,
		formatDimensions,
		formatDuration,
		type Group,
		type GroupFile
	} from '$lib/settings-ui/maintenance-state.svelte';

	/** The address carries `method:first`, which is `keyOf` on the queue. */
	const key = $derived(page.params.id ?? '');
	const method = $derived(key.split(':')[0] ?? '');
	const first = $derived(key.split(':')[1] ?? '');

	let group = $state<Group | null>(null);
	let loading = $state(true);
	let missing = $state(false);
	let failed = $state(false);

	async function load() {
		if (!method || !first) return;
		loading = true;
		failed = false;
		missing = false;
		try {
			group = await fetchGroup(method, first);
		} catch (error) {
			missing = isMissing(error);
			failed = !missing;
			group = null;
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void key;
		void load();
	});

	function nameOf(file: GroupFile): string {
		return file.original_filename || `the file ending ${file.id.slice(-6)}`;
	}

	function look(chain: Group, file: GroupFile) {
		openAsset(
			file.id,
			chain.files.map((one) => ({ id: one.id, runs: one.media_type !== 'image' }))
		);
	}

	const title = $derived(
		group ? `${counted(group.files.length)} files chained together` : 'A chain'
	);

	/*
	 * Deleting goes through the file list's own action, not a request written here. It needs
	 * nothing this screen holds per file (there is no heart to move and no row to forget) so
	 * the surroundings are the smallest they can be: re-read the chain when it is done, which
	 * answers "gone" once the chain has fewer than two files, and the queue is where that leads.
	 */
	const actions = new AssetActions<Actionable>({
		lookup: () => undefined,
		selection: new Selection(),
		refresh: () => void load()
	});
	let deleteOpen = $state(false);
	let deleting = $state<string[]>([]);

	/** Keep this one: everything else in the chain is what goes. */
	function keepOnly(chain: Group, file: GroupFile) {
		deleting = chain.files
			.filter((one) => one.id !== file.id && !one.concealed)
			.map((one) => one.id);
		deleteOpen = deleting.length > 0;
	}
</script>

<svelte:head><title>{title}</title></svelte:head>

<PageFrame crumbs={organizeCrumbs(heldBoard.found?.queues ?? [], 'duplicates', title)}>
	{#snippet header()}
		<OrganizeHeader queue="duplicates" here={title}></OrganizeHeader>
	{/snippet}

	{#if loading && group === null}
		<Skeleton lines={3} />
	{:else if missing}
		<Empty scope="page" icon="content_copy" title="That chain is no longer here">
			The closeness setting changed, or it was decided in another window. What is left is back in
			Duplicates.
		</Empty>
	{:else if failed}
		<Problem message="That chain couldn't be loaded. Try again in a moment." />
	{:else if group}
		{@const chain = group}
		<p class="closeness">{describeCloseness(chain)}</p>
		<ul class="files">
			{#each chain.files as file (file.id)}
				<li class="file">
					<Pressable
						class="preview"
						feedback="none"
						radius="sm"
						onclick={() => look(chain, file)}
						disabled={file.concealed}
						aria-label="Open {nameOf(file)}"
					>
						<img src={thumbUrl(file)} alt="" loading="lazy" />
					</Pressable>
					{#if file.concealed}
						<p class="unknown">Hidden. Unlock the vault to see this file.</p>
					{:else}
						<p class="name">{nameOf(file)}</p>
						{#if file.where}
							<p class="where"><PathText path={file.where} /></p>
						{/if}
						<p class="facts">
							{formatBytes(file.size_bytes)}{formatDimensions(file.width, file.height)
								? ` \u00b7 ${formatDimensions(file.width, file.height)}`
								: ''}{file.duration_ms ? ` \u00b7 ${formatDuration(file.duration_ms)}` : ''}
						</p>
						{#if session.isAdmin && chain.files.length > 1}
							<Button
								tone="ghost"
								size="small"
								icon="check_circle"
								aria-label="Keep {nameOf(file)} and delete the other {chain.files.length - 1}"
								onclick={() => keepOnly(chain, file)}
							>
								Keep this one
							</Button>
						{/if}
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</PageFrame>

<!-- `canDeleteFromDisk` is the half only an admin has and nothing else, because nothing else can
     be known here (`DeleteDialog`'s head says why). Defence in depth: the Delete verb is only pushed for an admin
     (`grid/verbs.ts`, held by `verbs.test.ts`), so the reason below appears only if that stops
     being true. -->
<DeleteDialog
	bind:open={deleteOpen}
	count={deleting.length}
	ids={deleting}
	canDeleteFromDisk={session.isAdmin}
	unavailableReason="Only an admin can delete files from disk."
	onconfirm={(mode: DeleteMode) => void actions.remove(deleting, mode)}
/>

<style>
	.closeness {
		margin: 0 0 var(--space-3);
		color: var(--sift-ink-3);
		font: var(--text-body);
	}

	/* The same tile the queue draws, at the same size, so a chain opened up reads as the queue's
	   card unfolded rather than as a different screen. */
	.files {
		list-style: none;
		margin: 0;
		padding: 0;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(min(140px, 100%), 1fr));
		gap: var(--space-3);
	}

	.file {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

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
		aspect-ratio: 1 / 1;
		object-fit: contain;
		background: var(--sift-surface-3);
		border-radius: var(--radius-sm);
	}

	.name,
	.where,
	.facts,
	.unknown {
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
		font-variant-numeric: tabular-nums;
	}

	.unknown {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}
</style>
