<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/* Files that look alike, gathered into groups with the one Sift would keep marked: the rule
	 * proposes, a person skims and confirms a page. The rule is a server setting; nothing is
	 * deleted
	 * without a confirm naming the count and the bytes. */
	import {
		Button,
		ConfirmDialog,
		Empty,
		Note,
		Pressable,
		Problem,
		Select,
		Skeleton
	} from '$lib/components/common';
	import NumberInput from '$lib/components/common/NumberInput.svelte';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import { onDestroy, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import Icon from '$lib/components/Icon.svelte';
	import PathText from '$lib/components/PathText.svelte';
	import FileName from '$lib/components/organize/FileName.svelte';
	import DecisionCard from '$lib/components/organize/DecisionCard.svelte';
	import PanelBar from '$lib/components/organize/PanelBar.svelte';
	import { page as address } from '$app/state';
	import { revealAnchored } from '$lib/organize/anchor';
	import { chainHref } from '$lib/organize/addresses';
	import { openAsset } from '$lib/player/asset-view';
	import Thumb from '$lib/components/organize/Thumb.svelte';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging, scrollParent } from '$lib/grid/cards.svelte';
	import { libraryChanges, settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { answered } from '$lib/organize/organize.svelte';
	import LastRun from '$lib/jobs/LastRun.svelte';
	import type { RunWords } from '$lib/jobs/last-run';
	import { saveSettings, refusalOf } from '$lib/settings-ui/settings';
	import {
		Maintenance,
		describeCloseness,
		keyOf,
		formatBytes,
		formatDimensions,
		formatDuration,
		type Group,
		type GroupFile
	} from '$lib/settings-ui/maintenance-state.svelte';

	const view = new Maintenance();

	/* The comparison's last run, from its row on Tasks. */
	const DUPLICATES_TASK = 'duplicates';
	const COMPARED: RunWords = {
		ran: (when) => `Sift last compared your files ${when}.`,
		failed: (when) => `The last comparison stopped with a problem ${when}.`,
		canceled: (when) => `The last comparison was stopped ${when}.`
	};

	/* The three dials this screen owns, read together with the pile they govern; still settings,
	 * saved through the same endpoint and heard from other windows. */
	const KEEP_SETTING = 'dedup.keep';
	const LEVEL_SETTING = 'dedup.level';
	const GAP_SETTING = 'dedup.max_duration_gap_seconds';

	let problem = $state<string | undefined>(undefined);

	/* The server's page size: a group's height varies, so there is no row to measure. */
	const PAGE = 24;

	/* Paged as every Organize list, the page in the address by a row (`from`, `near`), so a queue
	 * that empties still has a page to return to. */
	const paging = new CardPaging(PAGE);
	const path = address.url.pathname;
	let arriving = true;

	/*
	 * The filter to unsettled groups lives in the address, since it decides which list `from` is
	 * in.
	 */
	const NARROW = 'show';
	const NEEDS_YOU = 'needs-you';
	const needsYou = $derived(address.url.searchParams.get(NARROW) === NEEDS_YOU);
	let confirmOpen = $state(false);
	let dismissing = $state<Group | null>(null);
	let dismissOpen = $state(false);
	let carryOpen = $state(false);

	/* The panel's own element, so a page turn scrolls back to the top. */
	let panel = $state<HTMLElement | null>(null);

	/** Where the pager goes: the frame's foot, drawn by the route. See `PagerProps`. */
	let { onpaging }: { onpaging?: OnPaging } = $props();
	$effect(() => {
		onpaging?.(paging.asPager(view.groups.length, view.summary.total, 'groups'));
	});
	onDestroy(() => onpaging?.(null));

	$effect(() => {
		void paging.offset;
		if (!panel) return;
		const box = scrollParent(panel);
		// jsdom has no `scrollTo`.
		if (typeof box.scrollTo === 'function') box.scrollTo({ top: 0 });
	});

	/** The page on screen, its start written to the address only once it landed. */
	async function load(): Promise<void> {
		if (!(await view.fillGroups(paging, needsYou))) return;
		const first = view.groups[0];
		rememberAnchor(address.url, path, first ? keyOf(first) : null, paging.offset);
	}

	/* A page turned or the filter moved; the address's anchor is honoured once, a new filter starts at
	   the top. */
	let narrowedAt: boolean | null = null;
	$effect(() => {
		void paging.offset;
		void paging.size;
		const narrowed = needsYou;
		if (arriving) {
			arriving = false;
			narrowedAt = narrowed;
			// UNTRACKED: this effect's own answer writes the address. See `IdentifiedPanel`.
			paging.arrive(untrack(() => anchorIn(address.url)));
		} else if (narrowed !== narrowedAt) {
			narrowedAt = narrowed;
			// A page other than the first is moved to the first, and that move is what reads it.
			if (untrack(() => paging.restart())) return;
		}
		untrack(() => void load());
	});

	/* And on any library change or dial moved anywhere, through the settings endpoint's own bell, at
	   the same place. */
	whenChanged(libraryChanges, () => void load());
	whenChanged(settingChanges, () => void load());

	/* And what could be carried across copies, read once beside the list. */
	$effect(() => {
		void libraryChanges.generation;
		void settingChanges.generation;
		untrack(() => void view.loadCarry());
	});

	/** Read again after a verb: from the front when the question or page moved, else in place. */
	async function reread(fromTheFront: boolean): Promise<void> {
		// A move to the front is itself what reads the page.
		if (fromTheFront && paging.restart()) return;
		await load();
	}

	/** The queue, filtered to what no rule could settle or not: a new address, with no anchor. */
	function narrowTo(on: boolean): void {
		void goto(on ? `${path}?${NARROW}=${NEEDS_YOU}` : path, { keepFocus: true, noScroll: true });
	}

	/* Arrived pointing at one group from the board (`_group_anchor`); once per fragment. */
	let revealed = '';

	$effect(() => {
		const wanted = address.url.hash.slice(1);
		if (!wanted || wanted === revealed || view.groups.length === 0) return;
		if (revealAnchored(wanted)) revealed = wanted;
	});

	/* A closer look: `openAsset` over this screen, Next and Previous stepping through the group. */
	function look(group: Group, file: GroupFile) {
		openAsset(
			file.id,
			group.files.map((one) => ({ id: one.id, runs: one.media_type !== 'image' }))
		);
	}

	/** A file's name in a sentence: its original name, or a short id, never nothing. */
	function nameOf(file: GroupFile): string {
		return file.original_filename || `the file ending ${file.id.slice(-6)}`;
	}

	/** What one group would free; unknown sizes count as nothing. */
	function freedBy(group: Group): number {
		const keep = view.keeperOf(group);
		return group.files
			.filter((one) => one.id !== keep)
			.reduce((total, one) => total + (one.size_bytes ?? 0), 0);
	}

	/** The line over each group: how many files, how many would go, and what that frees. */
	function shapeOf(group: Group): string {
		const files = `${counted(group.files.length)} files`;
		if (group.too_big) return `${files} chained together`;
		const keep = view.keeperOf(group);
		if (keep === null) return `${files}, and no rule could choose between them`;
		const going = group.files.length - 1;
		return `${files}, ${going} would be deleted, frees ${formatBytes(freedBy(group))}`;
	}

	/* Move a dial: save it and read from the start, as a dial changes what a group is; announced so
	 * the board and the next tab recount. */
	async function moveDial(key: string, value: string | number) {
		try {
			await saveSettings({ [key]: value });
			problem = undefined;
			await reread(true);
			answered.changed();
		} catch (error) {
			problem = refusalOf(error);
		}
	}

	async function doConfirm() {
		const outcome = await view.confirmMarked(view.marked);
		problem = outcome.problem ?? refusedMessage(outcome.refused, outcome.unknown);
		/* From the front: every group on the page has left it. */
		await reread(true);
		answered.changed();
	}

	async function doDismiss() {
		if (!dismissing) return;
		const outcome = await view.dismissGroups([dismissing]);
		problem = outcome.problem;
		// The same page: one group left it and the rest are still somebody's decision.
		await reread(false);
		answered.changed();
	}

	/** Copy details from each duplicate's identical copy; nothing deleted, each write undoable. */
	async function doCarry() {
		const outcome = await view.carryEverywhere();
		problem = typeof outcome === 'string' ? outcome : undefined;
		if (typeof outcome !== 'string') answered.changed();
	}

	/** What to say when the disk kept a file or a group had moved: not silence. */
	function refusedMessage(refused: number, unknown: number): string | undefined {
		const parts: string[] = [];
		if (refused > 0) {
			parts.push(
				`${counted(refused)} ${refused === 1 ? 'file' : 'files'} couldn't be deleted. The folder may be read-only.`
			);
		}
		if (unknown > 0) {
			parts.push(
				`${counted(unknown)} ${unknown === 1 ? 'group had' : 'groups had'} already changed and were left alone.`
			);
		}
		return parts.length > 0 ? parts.join(' ') : undefined;
	}

	/** How many pairs the closeness setting is holding back, if any. */
	const hidden = $derived(Math.max(0, view.summary.pendingTotal - view.summary.matching));

	const chooser = $derived(view.summary.rules.map((one) => ({ value: one.key, label: one.label })));

	const closeness = $derived(
		view.summary.levels.map((one) => ({ value: one.key, label: one.label }))
	);

	/* The sentence over the list carries the counts; the buttons are verbs with none. */
	const standing = $derived.by(() => {
		if (!view.loaded) return '';
		const groups = `${counted(view.summary.total)} ${view.summary.total === 1 ? 'group' : 'groups'}`;
		const chose = view.summary.total - view.summary.needsYou;
		const needs =
			view.summary.needsYou > 0
				? ` ${counted(view.summary.needsYou)} ${view.summary.needsYou === 1 ? 'needs' : 'need'} your input.`
				: '';
		const carry =
			view.carry.files > 0
				? ` ${counted(view.carry.files)} ${view.carry.files === 1 ? 'duplicate' : 'duplicates'} can take the details of an identical copy.`
				: '';
		return `${groups}. Sift chose the copy to keep in ${counted(chose)} of them.${needs}${carry}`;
	});

	const consequence = $derived.by(() => {
		const going = view.wouldDelete;
		if (going.length === 0) return '';
		const bytes = going.reduce((total, one) => total + (one.size_bytes ?? 0), 0);
		return (
			`Across ${counted(view.marked.length)} ${view.marked.length === 1 ? 'group' : 'groups'}, Sift keeps ` +
			`the marked file and permanently deletes the other ${going.length} from your disk, ` +
			`freeing ${formatBytes(bytes)}. This can't be undone.`
		);
	});
</script>

<section bind:this={panel}>
	<Problem message={problem ?? view.problem} />

	<!-- The bar over the list: the rule, the queue and the one press that settles this page. -->
	<PanelBar>
		<!-- The dials together, as they are read together. -->
		<div class="dials">
			<div class="dial">
				<span class="dial-label">Keep</span>
				<Select
					value={view.summary.rule}
					options={chooser}
					label="Which copy to keep"
					disabled={view.busy || chooser.length === 0}
					onValueChange={(one) => moveDial(KEEP_SETTING, one)}
				/>
			</div>
			<div class="dial">
				<span class="dial-label">Alike</span>
				<Select
					value={view.summary.level}
					options={closeness}
					label="How similar duplicates must be"
					disabled={view.busy || closeness.length === 0}
					onValueChange={(one) => moveDial(LEVEL_SETTING, one)}
				/>
			</div>
			<div class="dial">
				<span class="dial-label">Length within</span>
				<!-- Zero means lengths are not compared, said in the setting's own word. -->
				<NumberInput
					value={Math.round((view.summary.maxDurationGapMs ?? 0) / 1000)}
					min={0}
					max={view.summary.maxDurationGapLimit}
					unit="sec"
					automatic={view.summary.maxDurationGapWord ?? undefined}
					label="Largest difference in length"
					disabled={view.busy}
					onchange={(seconds) => moveDial(GAP_SETTING, seconds)}
				/>
			</div>
		</div>
		<p class="standing">
			{standing}
			<LastRun task={DUPLICATES_TASK} words={COMPARED} />
		</p>
		<div class="presses">
			{#if view.summary.needsYou > 0}
				<!-- A filter, not a second screen; the words say which of the two it shows. -->
				<Button disabled={view.busy} onclick={() => narrowTo(!needsYou)}>
					{needsYou ? 'Show all groups' : 'Show the ones that need you'}
				</Button>
			{/if}
			{#if view.carry.files > 0}
				<!--
				Identical copies where one carries a person or a Site; the count is the whole
				library's.
				-->
				<Button icon="content_copy" disabled={view.busy} onclick={() => (carryOpen = true)}
					>Copy details</Button
				>
			{/if}
			<!-- How many groups the press is about is said in the question it asks first. -->
			<Button disabled={view.busy || view.marked.length === 0} onclick={() => (confirmOpen = true)}>
				Confirm this page
			</Button>
		</div>
	</PanelBar>

	<!--
	What the reader needs before reading an empty list, each sentence naming its population.
	-->
	<div class="standing-facts">
		{#if view.summary.awaitingFingerprint > 0}
			<Note>
				<strong class="data">{counted(view.summary.awaitingFingerprint)}</strong>
				{view.summary.awaitingFingerprint === 1 ? 'video has' : 'videos have'} not been fingerprinted
				yet, so
				{view.summary.awaitingFingerprint === 1 ? 'it is' : 'they are'} not in this list. That happens
				in the background, and you can follow it in Activity.
			</Note>
		{/if}
		<!--
		Files the decoder refused have no fingerprint and are counted apart; Generate retries them.
		-->

		{#if view.summary.cannotFingerprint > 0}
			<Note tone="caution">
				<strong class="data">{counted(view.summary.cannotFingerprint)}</strong>
				{view.summary.cannotFingerprint === 1 ? "file can't" : "files can't"} be compared at all &mdash;
				Sift couldn't read
				{view.summary.cannotFingerprint === 1 ? 'its frames' : 'their frames'}, so
				{view.summary.cannotFingerprint === 1 ? 'it has' : 'they have'} no fingerprint to match against.
			</Note>
		{/if}
		{#if hidden > 0}
			<Note>
				<strong class="data">{counted(hidden)}</strong>
				more {hidden === 1 ? 'pair is' : 'pairs are'} waiting that your closeness setting doesn't show.
				<!-- The dial is on this panel, not in Settings. -->
				Loosen Alike above to see them. It takes effect straight away, without comparing again.
			</Note>
		{/if}
		{#if view.summary.concealed > 0}
			<Note>
				<strong class="data">{counted(view.summary.concealed)}</strong>
				{view.summary.concealed === 1 ? 'group is' : 'groups are'} in a vault this session hasn't opened,
				and {view.summary.concealed === 1 ? 'is' : 'are'} not shown on this page.
			</Note>
		{/if}
	</div>

	{#if view.loading && view.groups.length === 0}
		<Skeleton lines={3} />
	{:else if !view.loaded}
		<!-- Not "nothing to review": a failed read says nothing about the library. -->
		<Problem message="Duplicates couldn't be loaded. Refresh the page to try again." />
	{:else if view.groups.length === 0}
		<Empty scope="block">
			{#if needsYou}
				Nothing on this page needs your input. Sift chose the copy to keep in every group.
			{:else if view.summary.pendingTotal === 0}
				Nothing to review. Sift hasn't found any duplicates it's unsure about.
			{:else}
				Nothing matches your current settings.
			{/if}
			<!-- For a library that existed before Sift, where the scan never ran by itself. -->
			{#snippet action()}
				<Button
					tone="ghost"
					icon="search"
					disabled={view.busy}
					onclick={async () => (problem = await view.scanNow())}
				>
					Run the comparison now
				</Button>
			{/snippet}
		</Empty>
	{:else}
		<ul class="groups">
			<!-- Keyed by method and smallest file (`keyOf`), since a GIF is grouped twice. -->
			{#each view.groups as group (keyOf(group))}
				{@const keep = view.keeperOf(group)}
				<!-- Named so a board still can point at this group (`_group_anchor`). -->
				<li id="group-{keyOf(group)}">
					<DecisionCard unsettled={group.too_big}>
						<header>
							<!-- Not Badge: closeness is neither a job nor a severity. -->
							<span class="closeness">{describeCloseness(group)}</span>
							<span class="shape data">{shapeOf(group)}</span>
						</header>

						{#if group.too_big}
							<!--
							A chain, not a group: its ends may look nothing alike; it opens on its
							own screen.
							-->
							<p class="chain-note">
								These are joined in a chain &mdash; each one looks like the next, and the two ends
								may look nothing alike.
								<a href={chainHref(keyOf(group))}>Open all {group.files.length} and decide</a>, or
								tighten Alike above and the chain breaks into groups small enough to decide about.
							</p>
						{/if}

						<div class="files" style:--cards={Math.min(group.files.length, group.too_big ? 6 : 8)}>
							{#each group.files.slice(0, group.too_big ? 6 : 8) as file (file.id)}
								<div class="file" class:keeping={file.id === keep}>
									<Pressable
										class="preview"
										feedback="none"
										radius="sm"
										onclick={() => look(group, file)}
										disabled={file.concealed}
										aria-label="Open {nameOf(file)}"
									>
										<!--
										`Thumb`, which leaves the ground for a missing still.
										-->
										<Thumb
											kind="asset"
											id={file.id}
											art={file.art}
											concealed={file.concealed}
											fit="whole"
										/>
									</Pressable>

									{#if file.concealed}
										<p class="unknown">Hidden. Unlock the vault to see this file.</p>
									{:else}
										<p class="name"><FileName name={nameOf(file)} /></p>
										<!--
										Where it is, often the only thing telling copies apart.
										-->
										{#if file.where}
											<p class="where"><PathText path={file.where} /></p>
										{/if}
										<p class="facts data">
											{formatBytes(file.size_bytes)}{formatDimensions(file.width, file.height)
												? ` \u00b7 ${formatDimensions(file.width, file.height)}`
												: ''}{file.duration_ms ? ` \u00b7 ${formatDuration(file.duration_ms)}` : ''}
										</p>
									{/if}

									{#if group.too_big}
										<!-- Nothing to mark: see the caution above. -->
									{:else if file.id === keep}
										<p class="kept"><Icon name="check_circle" /> Keeping this one</p>
									{:else}
										<!--
										The override writes nothing: mark a different file and
										confirm the page.
										-->
										<Button
											tone="ghost"
											disabled={view.busy || file.concealed}
											aria-label="Keep {nameOf(file)} instead"
											onclick={() => view.choose(group, file.id)}
										>
											Keep this instead
										</Button>
									{/if}
								</div>
							{/each}
							{#if group.files.length > (group.too_big ? 6 : 8)}
								<p class="more">
									{#if group.too_big}
										<a href={chainHref(keyOf(group))}>and {counted(group.files.length - 6)} more</a>
									{:else}
										and {counted(group.files.length - 8)} more
									{/if}
								</p>
							{/if}
						</div>

						{#if !group.too_big && !group.files.some((one) => one.concealed)}
							<!--
							Not on a chain or a group with a vaulted file: the server refuses both.
							-->
							<footer>
								<Button
									tone="ghost"
									disabled={view.busy}
									onclick={() => {
										dismissing = group;
										dismissOpen = true;
									}}
								>
									Keep all
								</Button>
							</footer>
						{/if}
					</DecisionCard>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete the other copies on this page?"
	{consequence}
	confirmLabel="Delete other copies"
	destructive
	onconfirm={doConfirm}
/>

<ConfirmDialog
	bind:open={carryOpen}
	title="Copy details to the duplicates?"
	consequence={`${counted(view.carry.files)} ${view.carry.files === 1 ? 'file' : 'files'} in ${counted(view.carry.groups)} ${view.carry.groups === 1 ? 'group' : 'groups'} gain the person or Site their identical copy already has. Nothing is deleted, and you can undo each file separately in History, under Decisions.`}
	confirmLabel="Copy details"
	onconfirm={doCarry}
/>

<ConfirmDialog
	bind:open={dismissOpen}
	title={dismissing ? `Keep all ${counted(dismissing.files.length)} files?` : 'Keep all files?'}
	consequence={dismissing
		? `All ${counted(dismissing.files.length)} files are kept, and Sift won't suggest them as duplicates again, even after another scan.`
		: ''}
	confirmLabel="Keep all"
	onconfirm={doDismiss}
/>

<style>
	/* The panel is what the closer look is positioned against, which is what keeps it inside. */
	section {
		position: relative;
	}

	.dials {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2) var(--space-4);
	}

	.dial {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.dial-label {
		font: var(--text-body);
		color: var(--sift-ink-2);
		white-space: nowrap;
	}

	.standing {
		margin: 0;
		flex: 1 1 20ch;
		min-inline-size: 0;
		font: var(--text-body);
		color: var(--sift-ink-2);
	}

	.presses {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
	}

	.standing-facts {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin-block-end: var(--space-4);
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
		gap: var(--space-3);
	}

	.groups header {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		flex-wrap: wrap;
	}

	.chain-note {
		margin: 0;
		max-width: var(--reading-measure);
		font: var(--text-body-sm);
		color: var(--sift-ink-2);
	}

	.chain-note a {
		color: var(--sift-accent-text);
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

	/* Every card of a group in one row where it fits at 120px, each at most 160px: `1fr` tracks
	 * under a `--cards` ceiling, `auto-fit` so empty tracks collapse. */
	.files {
		display: grid;
		/* One card's width, named once: the grid's ceiling and the tile's own height read it. */
		--card: 160px;
		grid-template-columns: repeat(auto-fit, minmax(min(120px, 100%), 1fr));
		max-inline-size: calc(var(--cards) * var(--card) + (var(--cards) - 1) * var(--space-3));
		gap: var(--space-3);
	}

	/* The "and N more" line is the group's foot, not a ninth card. */
	.files > .more {
		grid-column: 1 / -1;
	}

	.file {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		min-inline-size: 0;
	}

	/* The keeper is marked on the file, which is what somebody skims for. */
	.keeping {
		outline: 2px solid var(--sift-accent);
		outline-offset: var(--space-2);
		border-radius: var(--radius-sm);
	}

	/* The outline on the picture, reserved transparent so it steps in; global, as Pressable's. */
	.file :global(.preview img) {
		outline: 2px solid transparent;
		outline-offset: 2px;
		transition: outline-color var(--dur-instant) var(--ease);
	}

	.file :global(.preview:hover:not(:disabled) img) {
		outline: 2px solid var(--sift-line-strong);
		outline-offset: 2px;
	}

	/* Square and uncropped, as nothing may be hidden when choosing a copy to delete. */
	.file :global(img.whole) {
		max-block-size: 160px;
	}

	.name,
	.where,
	.facts,
	.unknown,
	.kept,
	.more {
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

	.unknown,
	.more {
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
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

	.groups footer {
		display: flex;
		justify-content: center;
	}

	/* The presses of a group on one line at the tiles' foot. */
	.file > :global(.btn),
	.kept {
		margin-block-start: auto;
	}
</style>
