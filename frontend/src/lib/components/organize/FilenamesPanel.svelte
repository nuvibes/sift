<script lang="ts" module>
	import type { Column } from '$lib/components/common/DataRows.svelte';

	/*
	 * A username's row, in columns declared once so every row's name, count and answers line up
	 * whether or not it has a still: the still (the size `Thumb` draws), the username, how many files
	 * went under it, and its answers. The arrow that opens a row stands in the list's fold track.
	 */
	const FILENAME_COLUMNS: readonly Column[] = [
		{ id: 'still', width: '44px' },
		{ id: 'who', width: 'minmax(0, 1fr)' },
		{ id: 'count', width: '8rem', align: 'end' },
		{ id: 'answers', width: '14rem', align: 'end' }
	];

	/*
	 * The same row at a phone's width: the still and the username, with the count and the answers on
	 * a line of their own under the name. The four tracks above need about 400 px before the name has
	 * any, so on a 393 px phone the name would be squeezed to one character per line and Open would
	 * run off the right edge.
	 */
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
	/*
	 * What a file's own name said about where it came from, grouped by the username it named.
	 *
	 * A page rather than the browse wall: the pass decides a username once and applies it to
	 * everything matching, so a flat wall of files would repeat one conclusion thousands of times,
	 * and a misreading is visible in the group it made and invisible in a list.
	 * (`FiledFromFilenamesQueue` on the server records the other view.)
	 *
	 * So a row is a username: a still of it, the name the pass read, how many files went under it,
	 * and the way to them, at a list row's height. The row opens to a few stills and the files by
	 * name, to check the reading by eye. A group's files are one press further: the person behind
	 * the username where there is one, the username's own files where there is not (see
	 * `usernameHref`). The username is the same address, so a name here is a link like anywhere
	 * else in Sift.
	 *
	 * The pass applies itself and says so on every file it touched, so a row asks one thing only:
	 * whether the username was read right. Yes is Open (a filing already made needs no yes), and
	 * No, behind its chevron, takes every file the name filed there back as ONE decision with its
	 * own Undo. The group is named and its files are a press away, so the No is not a bulk undo over
	 * a count nobody can see. One file at a time is still the opened row's Undo.
	 */
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
	/*
	 * Which page: the same paging every list in Organize has (`CardPaging`), a dozen cards at a
	 * time, with the username the page starts at written into the address as `from` (and where it
	 * was, `near`), so Back from a person or a file lands on this page again. See
	 * `DuplicatesPanel`.
	 */
	const paging = new CardPaging(PER_PAGE);
	const path = address.url.pathname;
	let arriving = true;
	/** The file whose undo is in flight, by its own id. One at a time, and named rather than a
	 *  flag, because a spinner on every row would say the whole group is being taken back. */
	let undoing = $state<string | null>(null);
	/** The username whose No is on its way. */
	let declining = $state<string | null>(null);
	/** The files whose still could not be drawn, by id: the opened strip leaves them out, as the
	 *  board does with its own. A row's one picture keeps its place (`Thumb` draws the glyph). */
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

	/* Read again, at the same place, whenever the library moves under it and whenever anything is
	   decided: taking one filing back is a decision, and without this the row it came off stays on
	   screen. A page it emptied steps back to where the list now ends (`CardPaging.fill`). */
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

	/**
	 * Where one group's files live.
	 *
	 * A username with somebody behind it opens that person: every file under the username counts
	 * under them, and their page is where the username is drawn (under the Site's card on their
	 * Sites tab). A username nobody has claimed opens the Files wall filtered with `?username=`,
	 * which is the set the server filed these under (`query={{ username: id }}`), passed as a
	 * parameter so nothing is typed into the search box and no chip appears.
	 *
	 * Not a typed text query: `file_name:` matches text, so another username whose files carry the
	 * same word would be included and a renamed file excluded, which is not the set the pass drew
	 * its conclusion from. Nor the facet parameters alone: `?sites=...&enriched=filename` is every
	 * username the pass filed on that Site, not this one.
	 *
	 * One function for the button and for the username beside it, deliberately: two ways into the
	 * same place on one card must not be two answers to one question.
	 */
	function usernameHref(group: FilenameGroup): string {
		return group.person_id
			? `/people/${encodeURIComponent(group.person_id)}`
			: `/browse?username=${encodeURIComponent(group.username_id)}`;
	}

	/** Which rows somebody opened BY HAND, by username id. Held here and not remembered: this is
	 *  one screen's arrangement while it is being worked through, not a preference. */
	let unfolded = $state<Record<string, boolean>>({});

	/** Whether this row's files are showing. Every row arrives folded, at a row's height: the
	 *  server sends up to two dozen files per username, and a page of open rows would be hundreds
	 *  of filenames deep. */
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

	/*
	 * No: the name was misread for this whole username. Every file it filed there comes off as one
	 * decision, and the toast carries that decision's Undo, which puts each back as it was.
	 */
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
			/* Announced rather than re-read here. Taking one filing back changes this list, the
			   count on the card behind it and the queue's own tab, and the effect above is already
			   listening for exactly that. */
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
									<!-- A few of them, to check the reading by eye. Each carries the token its
								     still is addressed by, so the row costs the browser nothing on a
								     second visit (see `thumbUrl`). -->
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
												<!-- The popout, over this page, rather than the `/asset/[id]`
											     route: an anchor alone runs that route, which tears this
											     screen down and lands on the library when the panel is
											     closed. No `among`: a row here carries a name, a still and
											     a decision, not what kind of file it is, so there is
											     honestly no next and previous. -->
												<a
													class="file"
													href="/asset/{one.asset_id}"
													onclick={(event) => openAssetInstead(event, one.asset_id)}
													>{one.filename}</a
												>
											{/snippet}
											{#snippet undoOne()}
												<!-- No control at all where the record cannot offer one. An
											     Undo the server would refuse is worse than none. -->
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
					<!-- The row's cells, in the columns `FILENAME_COLUMNS` declares. A row with no still
				     keeps its empty cell, so its name, count and answers stand where every other
				     row's do. -->
					{#snippet still()}
						{#if group.shown[0]}
							<!-- One of the files, to know the username by. Decoration: the row names it
						     in words. -->
							<Thumb kind="asset" id={group.shown[0].asset_id} art={group.shown[0].art} />
						{/if}
					{/snippet}
					{#snippet who()}
						<!-- The username is the link and the site beside it is not, because only the
					     username has an address here. A plain anchor, so every modifier works.
					     `{' '}` and not a newline between the two: Svelte trims the whitespace at the
					     front of a block's contents, so a newline would render "quillmosson Instagram". -->
						<span class="username"
							><a href={usernameHref(group)}>{group.username}</a>{#if group.site}{' '}<span
									class="on">on {group.site}</span
								>{/if}</span
						>
						{#if phoneWidth.yes}
							<!-- A phone's row has no track for these two: the count starts where the name
						     starts and the answers end at the row's end, as the desktop's columns do. -->
							<span class="under">{@render count()}{@render answers()}</span>
						{/if}
					{/snippet}
					{#snippet count()}
						<span class="count">{filesSaid(group.files)}</span>
					{/snippet}
					{#snippet answers()}
						<!-- The row's answers: Yes is Open, which goes where the username goes (both read
					     `usernameHref`, so they cannot drift), and No takes the whole username back. A
					     button and not a link: Sift's buttons are never links. -->
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

	/* The site is quieter than the username it follows: "on Instagram" is context FOR the name rather
	   than part of it, and drawn at the same weight the two read as one long proper noun. */
	.on {
		color: var(--sift-ink-3);
	}

	/* What an opened row shows under it: the stills, then the files by name. Across every column
	   of the row, whose tracks it stands in. */
	.opened {
		grid-column: 1 / -1;
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding-block: var(--space-2);
		padding-inline-start: var(--space-3);
	}

	/* A strip that wraps rather than a scroller: there are at most a couple of dozen, and a row that
	   scrolls sideways is a second scroll direction on a page that already has one. */
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
