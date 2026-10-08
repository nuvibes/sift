<script lang="ts">
	import { counted } from '$lib/entity/entity-counts';
	/*
	 * Files that look alike, gathered into groups, with the one Sift would keep already marked.
	 *
	 * Groups, not pairs, so three copies of one clip are one question rather than three that could
	 * contradict each other. The machine proposes on every group, the person skims a page and
	 * confirms it, and the only groups costing a real decision are the ones no rule could separate:
	 * an automatic choice is a starting point, and the reviewer's job is to skim what the rule
	 * marked. The queue is paged, so it is clear how many are left.
	 *
	 * The rule is a setting, and it lives on the server: applied to the whole library rather than
	 * to what the browser has loaded. Changing it here saves the preference and re-reads; it writes
	 * nothing to any file.
	 *
	 * Nothing here deletes anything without a confirm naming the count and the bytes.
	 */
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

	/* The comparison's last run, from its row on Tasks: a reload still says when the files were
	   last compared, and a run pressed from here moves the line when it ends. */
	const DUPLICATES_TASK = 'duplicates';
	const COMPARED: RunWords = {
		ran: (when) => `Sift last compared your files ${when}.`,
		failed: (when) => `The last comparison stopped with a problem ${when}.`,
		canceled: (when) => `The last comparison was stopped ${when}.`
	};

	/*
	 * The three dials this screen owns, by the keys they are stored under.
	 *
	 * A dial and the pile it governs are read together (how close is close enough, how far apart
	 * two may run, and which copy to keep all decide what the list below is), and a control on a
	 * different screen from its result is one nobody dares move. So they live here, not on the
	 * Maintenance pane.
	 *
	 * They are still settings: stored in the registry like every other preference, saved through
	 * the same endpoint, and a change made in another window reaches this one (see the effect
	 * below).
	 */
	const KEEP_SETTING = 'dedup.keep';
	const LEVEL_SETTING = 'dedup.level';
	const GAP_SETTING = 'dedup.max_duration_gap_seconds';

	let problem = $state<string | undefined>(undefined);

	/* How many groups a page holds: the server's own page size. Not measured off the screen the way
	   a wall of cards is: a group is as tall as its files, so there is no row to count in. */
	const PAGE = 24;

	/*
	 * Which page: the same paging every list in Organize has (`CardPaging`), with the group the
	 * page starts at written into the address as `from` (and `near`, where it was), so Back from a
	 * chain's own screen, the crumbs or anywhere else lands on this page again.
	 *
	 * The address names a row, not an offset, so a queue that empties as you work still has a page
	 * worth returning to: a row decided away is answered with where the page was (`near`), which
	 * after a decision is the same page with the gap closed.
	 */
	const paging = new CardPaging(PAGE);
	const path = address.url.pathname;
	let arriving = true;

	/* Whether the page is filtered to the groups a rule could not settle: in the address, as the
	   tagger's state is, because it decides which list the row in `from` is a place in. Held in
	   memory it would be lost on the way back, and the anchor looked for in the other list. */
	const NARROW = 'show';
	const NEEDS_YOU = 'needs-you';
	const needsYou = $derived(address.url.searchParams.get(NARROW) === NEEDS_YOU);
	let confirmOpen = $state(false);
	let dismissing = $state<Group | null>(null);
	let dismissOpen = $state(false);
	let carryOpen = $state(false);

	/*
	 * Loaded on arrival, and again whenever the library's shape changes underneath.
	 *
	 * This list is ABOUT which files exist. Deleting one anywhere else in the application settles
	 * the groups it was in, and a screen that loaded once would go on offering a decision about a
	 * file that was already gone, with a Delete button pointed at it.
	 */
	/* The panel's own element, so a page turn can put the box that scrolls back at the top. Left
	   wherever the pager was pressed (at the foot of the last group), the next page would open on
	   its last card and read as nothing having changed. */
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
		// jsdom reports a missing method as `undefined` rather than throwing on the call, and a
		// test that draws the panel is not a test of where it scrolls.
		if (typeof box.scrollTo === 'function') box.scrollTo({ top: 0 });
	});

	/** The page on screen, read through the paging, and where it now starts written into the
	 *  address, only once it has landed, so an overtaken read cannot write a place it never drew. */
	async function load(): Promise<void> {
		if (!(await view.fillGroups(paging, needsYou))) return;
		const first = view.groups[0];
		rememberAnchor(address.url, path, first ? keyOf(first) : null, paging.offset);
	}

	/* A page turned, or the filtering moved. The anchor in the address is honoured once, on
	   arrival: after that the anchor there is one this panel wrote. Filtering is a different list,
	   so it starts at its top with no anchor carried across. */
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

	/* And again whenever the library's shape changes underneath, and when a dial moves ANYWHERE:
	   this window, another tab, another admin's browser. The bell is rung by the settings
	   endpoint's own announcement, so what reaches this screen is the same event whichever of them
	   did it, and there is one way the queue re-reads rather than a local path and a remote one
	   that can disagree. At the same place: a page emptied by it steps back in `CardPaging.fill`. */
	whenChanged(libraryChanges, () => void load());
	whenChanged(settingChanges, () => void load());

	/* And what could be carried across the copies, which is about the whole library rather than
	   about this page, so it is read beside the list, and not again when a page turns. */
	$effect(() => {
		void libraryChanges.generation;
		void settingChanges.generation;
		untrack(() => void view.loadCarry());
	});

	/** Read the queue again after a verb: from the FRONT when the whole question or the whole page
	 *  moved, at the same place when one group did. */
	async function reread(fromTheFront: boolean): Promise<void> {
		// A move to the front is itself what reads the page; at the front already, read it here.
		if (fromTheFront && paging.restart()) return;
		await load();
	}

	/** The queue, filtered to what no rule could settle or not: a new address, with no anchor. */
	function narrowTo(on: boolean): void {
		void goto(on ? `${path}?${NARROW}=${NEEDS_YOU}` : path, { keepFocus: true, noScroll: true });
	}

	/* Arrived here pointed at one group: a still on the board's Duplicates card. See
	   `$lib/organize/anchor`, and `_group_anchor` on the server for the other half of the name. Once
	   per fragment, for the reason the copies tab next door gives. */
	let revealed = '';

	$effect(() => {
		const wanted = address.url.hash.slice(1);
		if (!wanted || wanted === revealed || view.groups.length === 0) return;
		if (revealAnchored(wanted)) revealed = wanted;
	});

	/*
	 * A closer look: the player, opened over this screen, with the group as what it can step
	 * through.
	 *
	 * `openAsset` is what every other wall uses, so the picture opens full size and plays video;
	 * handing it the group makes Next and Previous step between the files being compared, which is
	 * the whole question this screen asks.
	 */
	function look(group: Group, file: GroupFile) {
		openAsset(
			file.id,
			group.files.map((one) => ({ id: one.id, runs: one.media_type !== 'image' }))
		);
	}

	/** What to call a file in a sentence. The original name where there is one, and a short form of
	    its id where there is not: never nothing, because these sentences are the ones naming which
	    file is about to be thrown away. */
	function nameOf(file: GroupFile): string {
		return file.original_filename || `the file ending ${file.id.slice(-6)}`;
	}

	/** What one group would free: every file in it but the keeper. Unknown sizes count as nothing
	    rather than being estimated, which is the same rule the reclaim screen states. */
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

	/*
	 * Move a dial: save it, then read the queue back from the start, because every dial changes
	 * what a group is (widening the closeness makes new groups out of pairs already on file, and
	 * the rule re-marks all of them), so the page somebody was on is a position in a list that no
	 * longer exists.
	 *
	 * `answered.changed()` carries it off this screen: the board's card and the tab beside this one
	 * both count groups, and must not keep showing the count from before the dial moved.
	 */
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
		/* From the FRONT: every group on the page has just left it, and the closest groups are
		   the ones worth showing next. */
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

	/** Copy details to every duplicate from its identical copy. Nothing is deleted and every file it
	    writes on can be undone on its own, which is why the count is the whole of the warning. */
	async function doCarry() {
		const outcome = await view.carryEverywhere();
		problem = typeof outcome === 'string' ? outcome : undefined;
		if (typeof outcome !== 'string') answered.changed();
	}

	/** What to say when the disk would not let a file go, or a group had moved on. Not a failure:
	    the press did what it could, but silence here would claim a clean sweep it did not have. */
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

	/* The sentence over the list, which is where the numbers are said. "1,204 groups" and "24 of
	   them need you" are different facts, and a screen that says only the first hides where the
	   work actually is. The buttons beside it are verbs and carry no counts: a count in a label
	   changes the button's width with every answer and says the number a second time. */
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

	<!--
		The bar over the list: what the rule is, what the queue is, and the one press that settles
		this page. Here rather than in the page's own header because the header is shared by every
		Organize screen and knows nothing about what it is drawing. See `OrganizeHeader`.
	-->
	<PanelBar>
		<!--
			The dials, together, because they are read together: what counts as alike, how far apart
			two may run, and which copy a rule would keep. Each writes its setting and re-reads.
		-->
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
				<!-- Zero means lengths are not compared at all, which is why it has a word of its own
				     rather than reading as "they must run for exactly the same no time": the word the
				     setting declares, so this dial and the settings pane say the same thing. -->
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
				<!-- A filter rather than a second screen. The groups a rule could not settle are the
				     same groups; what changes is that these are all that is showing. -->
				<!-- The same tone as the two beside it: the words say which of the two it shows. -->
				<Button disabled={view.busy} onclick={() => narrowTo(!needsYou)}>
					{needsYou ? 'Show all groups' : 'Show the ones that need you'}
				</Button>
			{/if}
			{#if view.carry.files > 0}
				<!-- A file whose bit-for-bit twin carries a person or a site while it carries nothing.
				     Offered here because this is the screen about files that look alike; the count is
				     the whole library's, so the press is the same whichever page is showing. -->
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
		What the reader has to know before they can read an empty list. Each sentence is about a
		different population and says which, because "nothing to review" is the same words whether
		the library is clean, the dial is tight, or half of it has never been looked at.
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
			WORK THAT WILL NOT HAPPEN, said apart from the work in flight above it.

			A file whose frames the decoder refused has no fingerprint and never will, so it is not
			in this queue and no amount of waiting puts it there. Counted rather than left out: an
			empty queue with fifty such files in the library reads as a clean library, which is the
			one thing it is not. The way back is Generate, which offers to forget these verdicts and
			try again: a file replaced on disk since is a real reason to.
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
				<!-- The dial is above, on this panel: Settings does not draw it, so a link there
				     would land on a pane without it. -->
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
		<!-- Deliberately not "nothing to review". A request that failed says nothing about the
		     library, and the reassuring reading of silence here is the wrong one. -->
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
			<!-- The scan runs by itself once importing settles, which never happens on a library that
			     was already there when Sift was installed. This is the control for that case. -->
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
			<!-- Keyed by the group's METHOD and its smallest file, from the one place that names a
			     group. See `keyOf`. The file alone is not unique: a GIF is fingerprinted twice, so
			     the same two files are a group under each method, and two groups on one page can
			     share a first file. Svelte answers a duplicate key by throwing, so the screen
			     would draw nothing at all. -->
			{#each view.groups as group (keyOf(group))}
				{@const keep = view.keeperOf(group)}
				<!-- Named so a still on the board can point at THIS group. A group has no address of its
				     own and cannot be given one (it is computed from the pair table at the dials in
				     force) so the anchor is the page and the only stable name a group has, which is
				     the same key this list is drawn by. `_group_anchor` writes the other half. -->
				<li id="group-{keyOf(group)}">
					<DecisionCard unsettled={group.too_big}>
						<header>
							<!--
							Not the Badge component. That one carries job state (queued, running, failed)
							and its colours mean severity. How alike two files look is neither a job nor
							a severity, and borrowing the vocabulary would make amber mean "needs a person"
							in one place and "very similar" in another.
						-->
							<span class="closeness">{describeCloseness(group)}</span>
							<span class="shape data">{shapeOf(group)}</span>
						</header>

						{#if group.too_big}
							<!-- A chain, not a group: every pair in it is under the threshold and its two ends
						     may look nothing alike. Nothing is marked here and nothing is deleted from
						     here; it opens on a screen of its own where every file in it can be looked
						     at, and the honest fix is a tighter dial. -->
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
										<!-- `Thumb` for the still a file may not have yet: it leaves the
									     picture's ground rather than the browser's broken glyph. -->
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
										<!-- Where it is, which on this screen is often the only thing that tells the
									     files apart: the pictures look the same, that is why they are here, and
									     two copies of one clip in two folders have the same name and size. -->
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
										The override, and it writes nothing. Somebody who disagrees with the
										rule on one group marks a different file and presses the page's own
										confirm; the alternative (a per-file Delete) would have every group
										chosen from scratch.
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
							Not on a chain or a group holding a vaulted file: the server refuses both, and
							a button that does nothing is worse than none. "These eleven are not
							duplicates of each other" is a claim about ten pairs nobody can check by
							looking, and that is exactly what makes it a chain. The answer is the dial.
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

	/*
	 * Every card of a group in one row wherever the row can hold them at 120 pixels or more, each
	 * at most 160 pixels wide.
	 *
	 * The tracks are `1fr` over a grid no wider than `--cards` tiles of 160 pixels. A track with a
	 * fixed maximum makes the browser count columns at that maximum, so eight cards in a row a
	 * little short of eight times 160 would drop the eighth onto a line of its own. Counted at the
	 * 120-pixel minimum, they all stay in the row and share it. The cap is what keeps two files
	 * from stretching across the window: a group is skimmed, not studied, and the closer look is a
	 * press away in the player.
	 *
	 * `auto-fit`, not `auto-fill`, so empty tracks collapse; `min(120px, 100%)` so a track is never
	 * wider than a narrow window.
	 */
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

	/* The keeper is marked on the FILE rather than only on its button, because the mark is what
	   somebody skims for: a page of twenty-four groups is read by looking for what is lit. */
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

	/* Square, and it fills the tile: `Thumb`'s whole fit, drawn `contain` and never cropped, because
	   you are choosing which copy to delete and nothing may be hidden. Much of what these queues
	   hold is portrait, and a square box wastes the least on both shapes. No taller than the track is
	   wide at its widest. */
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

	.groups footer {
		display: flex;
		justify-content: center;
	}

	/* The keep press and the keeping mark at the tile's foot. The tiles of one group share a row's
	   height, so the presses of a group stand on one line whether a name took one line or two. */
	.file > :global(.btn),
	.kept {
		margin-block-start: auto;
	}
</style>
