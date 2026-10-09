<script lang="ts" module>
	import type { Column } from '$lib/components/common/DataRows.svelte';

	/* A username's row in declared columns: the still, the name, the count, its answers. */
	const FILENAME_COLUMNS: readonly Column[] = [
		{ id: 'still', width: '44px' },
		{ id: 'who', width: 'minmax(0, 1fr)' },
		{ id: 'count', width: '8rem', align: 'end' },
		{ id: 'answers', width: '14rem', align: 'end' }
	];

	/* At a phone's width, the count and answers on a line under the name. */
	const PHONE_COLUMNS: readonly Column[] = [
		{ id: 'still', width: '44px' },
		{ id: 'who', width: 'minmax(0, 1fr)' }
	];

	/* The files an opened row lists: the name, and its Undo at the end. */
	const FILE_COLUMNS: readonly Column[] = [
		{ id: 'file', width: 'minmax(0, 1fr)' },
		{ id: 'undo', width: '6rem', align: 'end' }
	];
</script>

<script lang="ts">
	import { filesSaid } from '$lib/entity/entity-counts';
	/* What a file's own name said about where it came from, grouped by the username it named: a row
	 * opens to stills and the files by name. Yes is Open; No takes every file the name filed back
	 * as
	 * one decision with its own Undo. */
	import { goto } from '$app/navigation';
	import { page as address } from '$app/state';
	import { onDestroy, untrack } from 'svelte';

	import { openAssetInstead } from '$lib/player/asset-view';
	import { reveal } from '$lib/shell/motion.svelte';
	import { Button, DataRow, DataRows, Empty, Problem, Skeleton } from '$lib/components/common';
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import Thumb from '$lib/components/organize/Thumb.svelte';
	import Answers from '$lib/components/organize/Answers.svelte';
	import type { OnPaging } from '$lib/components/common/Pager.svelte';
	import { anchorIn, rememberAnchor } from '$lib/grid/anchor';
	import { CardPaging } from '$lib/grid/cards.svelte';
	import { libraryChanges, whenChanged } from '$lib/library/changes.svelte';
	import { answered, decided, undo } from '$lib/organize/organize.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import type { ToastPiece } from '$lib/components/common/toast-pieces';
	import {
		filingsFromFilenames,
		takeBackUsername,
		type FiledFromName,
		type FilenameGroup
	} from '$lib/search/suggestions.svelte';

	/** How many usernames one page holds: a screenful of rows. */
	const PER_PAGE = 24;

	let { onpaging }: { onpaging?: OnPaging } = $props();

	let groups = $state<FilenameGroup[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let failed = $state(false);
	/* Paged as every Organize list, the page in the address (`from`, `near`). */
	const paging = new CardPaging(PER_PAGE);
	const path = address.url.pathname;
	let arriving = true;
	/** The file whose undo is in flight, so only its row spins. */
	let undoing = $state<string | null>(null);
	/** The username whose No is on its way. */
	let declining = $state<string | null>(null);
	/** Files whose still could not be drawn, left out of the strip. */
	let blank = $state<Record<string, boolean>>({});

	async function load() {
		failed = false;
		try {
			const page = await paging.fill(
				'',
				() => groups,
				(query) => {
					// Only when a request goes out: a landing asks nothing.
					loading = true;
					return filingsFromFilenames(query);
				},
				(answer) => ({ rows: answer.groups, total: answer.total, offset: answer.offset })
			);
			// Overtaken by a newer read, which finishes this one's work.
			if (page === null) return;
			groups = page.rows;
			total = page.total;
			// In the same synchronous turn as the rows. See `CardPaging.land`.
			paging.land(page.offset);
			rememberAnchor(address.url, path, groups[0]?.username_id, page.offset);
		} catch {
			failed = true;
		} finally {
			loading = false;
		}
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

	/* Read again at the same place on any library change or decision. */
	whenChanged(libraryChanges, () => void load());
	let seen = untrack(() => answered.stamp);
	$effect(() => {
		const now = answered.stamp;
		if (now === seen) return;
		seen = now;
		untrack(() => void load());
	});

	$effect(() => {
		onpaging?.(paging.asPager(groups.length, total, 'usernames'));
	});
	onDestroy(() => onpaging?.(null));

	/** Where a group's files live: the person behind the username, else the Files wall filtered by
	 * `?username=`, exactly the set the pass filed. One function for the button and the name. */
	function usernameHref(group: FilenameGroup): string {
		return group.person_id
			? `/people/${encodeURIComponent(group.person_id)}`
			: `/browse?username=${encodeURIComponent(group.username_id)}`;
	}

	/** Rows opened by hand, not remembered. */
	let unfolded = $state<Record<string, boolean>>({});

	/** Whether a row's files show; every row arrives folded. */
	function listOpen(group: FilenameGroup): boolean {
		return unfolded[group.username_id] ?? false;
	}

	function toggleList(group: FilenameGroup): void {
		unfolded[group.username_id] = !listOpen(group);
	}

	/** What the control points at, so what it opens is said rather than merely next to it. */
	function listId(group: FilenameGroup): string {
		return `filed-under-${group.username_id}`;
	}

	/** What a group is called, in a person's words. The username leads: it is what the pass read. */
	function nameOf(group: FilenameGroup): string {
		return group.site ? `${group.username} on ${group.site}` : group.username;
	}

	/** The username as the way to it: its person where somebody is said for it, else its files. */
	function usernameOf(group: FilenameGroup): ToastPiece {
		const href = group.person_id
			? `/people/${encodeURIComponent(group.person_id)}`
			: `/browse?username=${encodeURIComponent(group.username_id)}`;
		return { text: nameOf(group), kind: 'username', id: group.username_id, href };
	}

	/* No: every file the misread name filed comes off as one decision, with an Undo. */
	async function decline(group: FilenameGroup) {
		if (declining || undoing) return;
		declining = group.username_id;
		try {
			const taken = await takeBackUsername(group.username_id);
			if (taken.files === 0) {
				answered.changed();
				toasts.show('There was nothing left to undo');
			} else {
				decided(
					[`Removed ${filesSaid(taken.files)} from `, usernameOf(group)],
					taken.decision_id || null
				);
			}
		} catch {
			toasts.show("Those files couldn't be undone", { tone: 'error' });
		} finally {
			declining = null;
		}
	}

	async function takeBack(file: FiledFromName, group: FilenameGroup) {
		if (!file.decision_id || undoing) return;
		undoing = file.asset_id;
		try {
			await undo(file.decision_id);
			/* Announced; the effect above re-reads. */
			answered.changed();
			toasts.show(['Removed from ', usernameOf(group)]);
		} catch {
			toasts.show("That couldn't be undone", { tone: 'error' });
		} finally {
			undoing = null;
		}
	}
</script>

<section>
	{#if loading && groups.length === 0}
		<Skeleton lines={3} />
	{:else if failed}
		<Problem
			message="Files enriched from filenames couldn't be loaded. Refresh the page to try again."
		/>
	{:else if groups.length === 0}
		<Empty scope="page" icon="document_scanner" title="Nothing enriched from a filename">
			When a file's name carries a Site's username, post or ID, Sift adds the file to that username
			and lists it here.
		</Empty>
	{:else}
		<!-- Keyed on the width, because a list reads its declaration when it is made. -->
		{#key phoneWidth.yes}
			<DataRows
				items={groups}
				key={(group: FilenameGroup) => group.username_id}
				label="Usernames read from filenames"
				columns={phoneWidth.yes ? PHONE_COLUMNS : FILENAME_COLUMNS}
				folds
			>
				{#snippet row(group: FilenameGroup)}
					<DataRow
						cells={{ still, who, count, answers }}
						expanded={listOpen(group)}
						ontoggle={group.shown.length > 0 ? () => toggleList(group) : undefined}
						toggleLabel={`the files added to ${nameOf(group)}`}
					>
						{#snippet expansion()}
							{#if listOpen(group)}
								<div class="opened" id={listId(group)} transition:reveal>
									<!-- A few stills, to check the reading by eye. -->
									<div class="stills">
										{#each group.shown.filter((one) => !blank[one.asset_id]) as file (file.asset_id)}
											<Thumb
												kind="asset"
												id={file.asset_id}
												art={file.art}
												onmissing={() => (blank[file.asset_id] = true)}
											/>
										{/each}
									</div>
									<DataRows
										items={group.shown}
										key={(one: FiledFromName) => one.asset_id}
										label="Files added to {nameOf(group)}"
										columns={FILE_COLUMNS}
									>
										{#snippet row(one: FiledFromName)}
											<DataRow compact cells={{ file: fileName, undo: undoOne }} />
											{#snippet fileName()}
												<!--
												The popout over this page, not the /asset route; no
												`among`, so no next and previous.
												-->
												<a
													class="file"
													href="/asset/{one.asset_id}"
													onclick={(event) => openAssetInstead(event, one.asset_id)}
													>{one.filename}</a
												>
											{/snippet}
											{#snippet undoOne()}
												<!--
												No control where the server would refuse an Undo.
												-->
												{#if one.decision_id}
													<Button
														icon="undo"
														tone="ghost"
														size="small"
														busy={undoing === one.asset_id}
														disabled={undoing !== null}
														aria-label={`Undo adding ${one.filename} to ${nameOf(group)}`}
														onclick={() => void takeBack(one, group)}>Undo</Button
													>
												{/if}
											{/snippet}
										{/snippet}
									</DataRows>
								</div>
							{/if}
						{/snippet}
					</DataRow>
					<!-- The row's cells; a row with no still keeps its empty cell. -->
					{#snippet still()}
						{#if group.shown[0]}
							<!-- One of the files, decoration. -->
							<Thumb kind="asset" id={group.shown[0].asset_id} art={group.shown[0].art} />
						{/if}
					{/snippet}
					{#snippet who()}
						<!--
						The username is the link; `{' '}`, since Svelte trims a newline here.
						-->
						<span class="username"
							><a href={usernameHref(group)}>{group.username}</a>{#if group.site}{' '}<span
									class="on">on {group.site}</span
								>{/if}</span
						>
						{#if phoneWidth.yes}
							<!-- On a phone, the count and answers under the name. -->
							<span class="under">{@render count()}{@render answers()}</span>
						{/if}
					{/snippet}
					{#snippet count()}
						<span class="count">{filesSaid(group.files)}</span>
					{/snippet}
					{#snippet answers()}
						<!-- Open goes where the username goes; No takes it all back. -->
						<Answers
							yes={{ label: 'Open these files', run: () => void goto(usernameHref(group)) }}
							rest={[
								{
									label: 'No, take these files back',
									icon: 'undo',
									run: () => void decline(group)
								}
							]}
							about={nameOf(group)}
							busy={declining === group.username_id}
							disabled={declining !== null || undoing !== null}
						/>
					{/snippet}
				{/snippet}
			</DataRows>
		{/key}
	{/if}
</section>

<style>
	.username {
		font: var(--text-body);
		overflow-wrap: anywhere;
	}

	.count {
		color: var(--sift-ink-2);
	}

	/* The phone's second line: the count at the start, the answers at the end. */
	.under {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		margin-block-start: var(--space-2);
	}

	/* The Site quieter than the username. */
	.on {
		color: var(--sift-ink-3);
	}

	/* An opened row: stills, then files, across every column. */
	.opened {
		grid-column: 1 / -1;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding-block: var(--space-2);
		padding-inline-start: var(--space-3);
	}

	/* Wrapping, never a second scroll direction. */
	.stills {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-1);
	}

	.file {
		color: var(--sift-ink);
		text-decoration: none;
		overflow-wrap: anywhere;
	}

	.file:hover {
		text-decoration: underline;
	}
</style>
