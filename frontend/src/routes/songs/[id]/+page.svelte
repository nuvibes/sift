<script lang="ts">
	/* One song: its cover and name, who it credits, the files that carry it, and what else those
	 * files reach. */
	import type { Crumb } from '$lib/components/common';
	import { coverBody, type Frame } from '$lib/entity/cover-frame';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import {
		ConfirmDialog,
		ContextMenuGroup,
		ContextMenuItem,
		Empty,
		Problem,
		Skeleton
	} from '$lib/components/common';
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import PickPicture from '$lib/components/entity/PickPicture.svelte';
	import MergeEntities from '$lib/components/entity/MergeEntities.svelte';
	import { makerOf, type Maker } from '$lib/entity/enrich.svelte';
	import EntityHeader from '$lib/components/entity/EntityHeader.svelte';
	import type { Verb } from '$lib/components/common/verbs';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageAbove from '$lib/components/shell/PageAbove.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import RecordForm from '$lib/components/record/RecordForm.svelte';
	import RecordSummary from '$lib/components/record/RecordSummary.svelte';
	import RecordFacts from '$lib/components/entity/RecordFacts.svelte';
	import RecordView from '$lib/components/record/RecordView.svelte';
	import Tabs from '$lib/components/common/Tabs.svelte';
	import PickedFilter from '$lib/components/entity/PickedFilter.svelte';
	import { picksOf, tabsCarryingPicks, narrowedFilesTotal } from '$lib/components/entity/picks';
	import EntityHistory from '$lib/components/entity/EntityHistory.svelte';
	import RelatedWall from '$lib/components/entity/RelatedWall.svelte';
	import TabHold, { wallOfTab } from '$lib/components/entity/TabHold.svelte';
	import {
		showing as chosenTab,
		iconOf,
		tabsFor,
		type RelatedKind,
		TabCounts,
		TabWords
	} from '$lib/entity/related.svelte';
	import WallControls from '$lib/components/entity/WallControls.svelte';
	import { emptyWallSays } from '$lib/components/shell/wall-words';
	import { api } from '$lib/api/client';
	import { recorded, reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { EntitySubject } from '$lib/entity/subject.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { songs } from '$lib/entity/songs.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import type { components } from '$lib/api/schema';
	import { filesSized, sizeOf } from '$lib/entity/entity-counts';
	import { glyphOf } from '$lib/entity/entity-picture';
	import SongArtists from '$lib/components/entity/SongArtists.svelte';
	import ArtistRenameDialog from '$lib/components/entity/ArtistRenameDialog.svelte';
	import ShareDialog from '$lib/components/ShareDialog.svelte';
	import VisibilityDialog from '$lib/components/VisibilityDialog.svelte';
	import type { ShareTarget } from '$lib/library/sharing';
	import { setHidden } from '$lib/library/hiding';
	import EntityDropZone from '$lib/components/entity/EntityDropZone.svelte';
	import { vault } from '$lib/shell/vault.svelte';

	type Song = Pick<
		components['schemas']['SongSummary'],
		| 'id'
		| 'name'
		| 'cover_asset_id'
		| 'cover_upload_id'
		| 'cover_at_ms'
		| 'cover_frame'
		| 'art'
		| 'item_count'
		| 'size_bytes'
		| 'notes'
		| 'favorite'
		| 'rating'
		| 'o_count'
		| 'artists'
		| 'vault'
	>;

	/** What a song with no cover is drawn as, here and on its card: the Music page's own glyph. */
	const MUSIC = iconOf('song');

	const songId = $derived(page.params.id ?? '');
	/* History has no wall behind it, so it is kept out of `tabsFor`. */
	const HISTORY = 'history';

	const asked = $derived(page.url.searchParams.get('show'));
	const showingHistory = $derived(asked === HISTORY);
	const shown = $derived<RelatedKind>(chosenTab('song', asked));
	const fileWords = new TabWords();

	/* The subject, shared with every other entity page: a re-read leaves this song on screen,
	   and `follow` re-reads a name, a heart or a cover that has moved. */
	const subject = new EntitySubject<Song>((id) => api.get<Song>(`/songs/${id}`));
	subject.follow(() => songId);
	const song = $derived(subject.value);

	/* Everything the record is made of, in one place: the summary under the name, the panel
	   beside it and the form are three surfaces over the same facts. */
	const recordValues = $derived({
		name: song?.name ?? '',
		details: song?.notes ?? '',
		artists: (song?.artists ?? []).map((one) => one.name)
	});
	let confirmDelete = $state(false);
	let mergeOpen = $state(false);
	let confirmVault = $state(false);

	/* Sharing a song shares every file carrying it, and keeps doing so as more files are set to
	   it: the point of sharing a subject rather than a folder. */
	let shareOpen = $state(false);
	let reachOpen = $state(false);
	const shareTarget = $derived<ShareTarget | null>(
		song ? { type: 'song', id: song.id, label: song.name } : null
	);

	/* The numbers beside the tab words, asked for all together when the page settles. */
	const counts = new TabCounts();
	let editing = $state(false);

	/* What this page can do to this song, behind the one door every entity page wears. */
	const options = $derived<Verb[]>([
		...(session.isAdmin
			? [
					{
						id: 'share',
						label: 'Sharing',
						icon: 'group' as const,
						run: () => (shareOpen = true)
					},
					/* What the sharing above it comes to: who can actually reach this song,
					   however the reach was arranged. */
					{
						id: 'visibility',
						label: 'Visibility',
						icon: 'policy' as const,
						run: () => (reachOpen = true)
					},
					{
						id: 'merge',
						label: 'Merge into\u2026',
						icon: 'merge' as const,
						run: () => (mergeOpen = true)
					},
					{
						id: 'delete',
						label: 'Delete',
						icon: 'delete' as const,
						destructive: true,
						run: () => (confirmDelete = true)
					}
				]
			: []),
		/* Only reachable with Hidden open: with it shut, a hidden song's page answers that it is
		   not here. */
		song?.vault
			? {
					id: 'unhide',
					label: 'Stop hiding it',
					icon: 'visibility' as const,
					run: () => void reveal()
				}
			: {
					id: 'hide',
					label: 'Hide it',
					icon: 'visibility_off' as const,
					run: () => (confirmVault = true)
				}
	]);

	/* Hiding the song and bringing it back, through the ONE mechanism every wall and page uses
	   (`setHidden`: the Privacy wording, the refusal with no PIN set, the Undo). */
	const hiddenAs = $derived({
		noun: 'song',
		stays: vault.unlocked,
		set: (one: string, flag: boolean) =>
			api.put<void>(`/songs/${one}/vault`, { body: { vault: flag } })
	});

	async function conceal() {
		if (!song) return;
		const moved = await setHidden([song.id], true, hiddenAs);
		// Away from a page that is about to stop answering: hidden, it is not here for you.
		if (moved.length > 0) await leaveFor('/songs');
	}

	async function reveal() {
		if (!song) return;
		const moved = await setHidden([song.id], false, hiddenAs);
		if (moved.length > 0) subject.value = { ...song, vault: false };
	}
	/** Whether the picture chooser is open. Opened by the pencil on the cover. */
	let pickingPicture = $state(false);

	/* WHO MADE IT, for the line under the name: by hand, from AcoustID, or from a download's
	   page. */
	let madeBy = $state<Maker | null>(null);
	let madeByFor = $state('');
	$effect(() => {
		const one = songId;
		if (!one || madeByFor === one) return;
		madeByFor = one;
		void makerOf('song', one).then((held) => {
			if (madeByFor === one) madeBy = held;
		});
	});

	/** The whole record, saved once. The name, the details and the artists are three routes; what
	 * changed is what is sent. */
	async function saveRecord(draft: Record<string, unknown>) {
		if (!song) return;
		const wanted = String(draft.name ?? '').trim() || song.name;
		if (wanted !== song.name) {
			subject.value = await api.put<Song>(`/songs/${song.id}`, { body: { name: wanted } });
		}
		const notes = String(draft.details ?? '').trim() || null;
		if (notes !== (song.notes ?? null)) {
			subject.value = await api.put<Song>(`/songs/${song.id}/notes`, { body: { notes } });
		}
		const names = artistsOf(draft.artists);
		if (names.join('\n') !== (song.artists ?? []).map((one) => one.name).join('\n')) {
			subject.value = await api.put<Song>(`/songs/${song.id}/artists`, { body: { names } });
		}
		editing = false;
		toasts.show('Saved', { tone: 'success' });
	}

	/* The form's list as names: trimmed, blanks dropped, each once (the first spelling kept), in
	   the order they were put. */
	function artistsOf(value: unknown): string[] {
		const listed = Array.isArray(value) ? value.map((one) => String(one).trim()) : [];
		const seen = new Set<string>();
		return listed.filter((one) => {
			const key = one.toLowerCase();
			if (!one || seen.has(key)) return false;
			seen.add(key);
			return true;
		});
	}

	$effect(() => counts.follow('song', songId));
	/* The strip's numbers follow the library as its walls do: History has no wall to report one. */
	reloadOnLibraryChange(() => counts.refresh());

	/* How many files the Files tab holds while picks filter it (null: nothing picked). */
	let narrowedFiles = $state<number | null>(null);
	let narrowedAsk = 0;
	$effect(() => {
		const url = page.url;
		const held = song;
		const ask = (narrowedAsk += 1);
		if (!held || picksOf(url).length === 0) {
			narrowedFiles = null;
			return;
		}
		void narrowedFilesTotal(url, { songs: songId }).then((total) => {
			if (ask === narrowedAsk) narrowedFiles = total;
		});
	});

	const tabs = $derived([
		...tabsFor('song', songId, `/songs/${songId}`, {
			...counts.current,
			files: narrowedFiles ?? song?.item_count
		}),
		{
			id: HISTORY,
			label: 'History',
			icon: 'history' as const,
			href: `/songs/${songId}?show=${HISTORY}`,
			count: counts.current.history
		}
	]);

	/* This account's own opinion of the song, not the song's. */
	async function heart(favorite: boolean) {
		if (!song) return;
		try {
			await api.put(`/songs/${song.id}/favorite`, { body: { favorite } });
			subject.value = { ...song, favorite };
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(rating: number | null) {
		if (!song) return;
		try {
			await api.put(`/songs/${song.id}/rating`, { body: { rating } });
			subject.value = { ...song, rating };
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/* The cover, chosen from the song's own files: a still of a video set to this music. */
	async function useAsCover(
		assetId: string,
		atMs: number | null = null,
		frame: Frame | null = null
	) {
		if (!song) return;
		try {
			subject.value = await api.put<Song>(`/songs/${song.id}/cover`, {
				body: coverBody(assetId, atMs, { frame })
			});
			toasts.show("That's the cover now", { tone: 'success' });
		} catch {
			toasts.show("That couldn't be made the cover", { tone: 'error' });
		}
	}

	/* A picture from outside the library, an album's sleeve say. */
	async function uploadCover(file: File) {
		if (!song) return;
		const form = new FormData();
		form.set('file', file);
		subject.value = await api.post<Song>(`/songs/${song.id}/cover-picture`, { body: form });
		toasts.show("That's the cover now", { tone: 'success' });
	}

	/* Taking a file off the song. The file stays exactly where it is on disk and keeps its
	   fingerprints; only its Music field is emptied, which the wording says. */
	async function takeOff(assetId: string, forget: (id: string) => void) {
		if (!song) return;
		try {
			await songs.remove(song.id, [assetId]);
			forget(assetId);
			recorded.changed();
			subject.value = { ...song, item_count: Math.max(0, song.item_count - 1) };
			toasts.show('Off the song. The file is where it was.', { tone: 'success' });
		} catch {
			toasts.show("Couldn't remove that", { tone: 'error' });
		}
	}

	async function remove() {
		if (!song) return;
		try {
			await api.del(`/songs/${song.id}`);
			toasts.show('The song is gone. Its files are where they were.', { tone: 'success' });
			await leaveFor('/songs');
		} catch {
			toasts.show("That couldn't be deleted", { tone: 'error' });
		}
	}

	const crumbs = $derived<Crumb[]>([
		{ label: 'Music', href: '/songs' },
		{ label: song?.name ?? '' }
	]);
</script>

<svelte:head><title>{song?.name ?? 'Song'}</title></svelte:head>

<!-- A link dropped anywhere on this page is fetched and put on this song, as one dropped on its
     card on the Music wall is. Only while there IS one: a page still settling has no id to aim
     at. See `EntityDropZone`. -->
{#if song}
	<EntityDropZone kind="song" id={song.id} name={song.name} />
{/if}

{#if subject.settling}
	<Skeleton lines={3} />
{:else if subject.unreadable}
	<Problem
		message={"That song couldn't be loaded. It's still there \u2014 try again in a moment."}
	/>
{:else if !song}
	<Empty scope="page" icon={MUSIC} title="That song isn't here">It may have been deleted.</Empty>
{:else}
	{@const held = song}
	{#snippet tabStrip()}
		<Tabs
			tabs={tabsCarryingPicks(tabs, page.url)}
			current={showingHistory ? HISTORY : shown}
			label="What to show for {held.name}"
		/>
		<PickedFilter />
	{/snippet}

	{#snippet identity()}
		<EntityHeader
			{options}
			optionIds={[songId]}
			bind:editing
			icon={MUSIC}
			glyph={glyphOf('song')}
			kind="song"
			mayEdit={session.isAdmin}
			name={held.name}
			onpicture={() => (pickingPicture = true)}
			oncover={async (assetId, atMs, more) => {
				subject.value = await api.put<Song>(`/songs/${songId}/cover`, {
					body: coverBody(assetId, atMs, more)
				});
			}}
			coverHref={`/songs/${songId}`}
			coverAssetId={held.cover_asset_id}
			coverUploadId={held.cover_upload_id}
			coverAtMs={held.cover_at_ms}
			coverFrame={held.cover_frame}
			art={held.art}
			counts={`On ${filesSized(held.item_count, sizeOf(held))}`}
			oCount={held.o_count}
			favorite={held.favorite}
			rating={held.rating}
			onfavorite={(next) => heart(next)}
			onrate={(next) => rate(next)}
			{madeBy}
		>
			{#snippet summary()}
				<SongArtists artists={held.artists ?? []} size="page" />
				<RecordSummary subject="song" label="About {held.name}" values={recordValues} />
			{/snippet}

			{#snippet facts()}
				<RecordFacts subject="song" label="Facts about {held.name}" values={recordValues} />
			{/snippet}

			{#snippet record()}
				<RecordView subject="song" label="More about {held.name}" values={recordValues} />
			{/snippet}
		</EntityHeader>
	{/snippet}

	{#snippet editing_form()}
		<RecordForm
			subject="song"
			label="Editing {held.name}"
			values={recordValues}
			onsave={saveRecord}
			oncancel={() => (editing = false)}
		/>
	{/snippet}

	{#if editing}
		<PageFrame header={identity}>
			{#snippet children()}
				{@render editing_form()}
			{/snippet}
		</PageFrame>
	{:else if showingHistory}
		<!-- History draws the identity and the tab strip in the shape every other tab does
		     (`PageAbove`, then the strip as the heading row), so the strip stands at one height
		     on every tab. -->
		<PageFrame {crumbs}>
			{#snippet header()}
				<PageAbove>{@render identity()}</PageAbove>
				<PageHeader title="History" icon="history" level={2} titleHidden beside={tabStrip} />
			{/snippet}
			{#snippet children()}
				<EntityHistory subject="song" id={songId} name={held.name} />
			{/snippet}
		</PageFrame>
	{:else}
		<TabHold tab={shown} wallOf={wallOfTab}>
			{#snippet surface(tab, arrived)}
				{#if tab === 'files'}
					<AssetGrid
						oncount={arrived}
						query={{ songs: songId }}
						title="Files"
						titleLevel={2}
						beside={tabStrip}
						titleHidden
						above={identity}
						{crumbs}
						empty={emptyWallSays('files', fileWords.asked, false, 'No file carries this song yet.')}
						pinnable
						menuExtraGrouped
					>
						{#snippet tools()}
							<WallControls
								noun="file"
								plural="files"
								bind:term={fileWords.term}
								onsettled={(typed) => fileWords.write(typed)}
							/>
						{/snippet}
						{#snippet menuExtra(item, grid)}
							<!-- Admin only: the cover and which files carry it are the song itself. -->
							{#if session.isAdmin}
								<ContextMenuGroup>
									<ContextMenuItem
										label="Remove from this song"
										icon="close"
										onselect={() => void takeOff(item.id, grid.forget)}
									/>
								</ContextMenuGroup>
								<ContextMenuGroup>
									<ContextMenuItem
										label="Use as this song's cover"
										icon={MUSIC}
										onselect={() => void useAsCover(item.id)}
									/>
								</ContextMenuGroup>
							{/if}
						{/snippet}
					</AssetGrid>
				{:else}
					<RelatedWall
						on="song"
						id={songId}
						named={held.name}
						showing={tab}
						title={tabs.find((one) => one.id === tab)?.label ?? ''}
						icon={tabs.find((one) => one.id === tab)?.icon ?? 'browse'}
						beside={tabStrip}
						titleHidden
						above={identity}
						{crumbs}
						oncount={(total, searched) => {
							if (!searched) counts.saw(tab, total);
							arrived();
						}}
					/>
				{/if}
			{/snippet}
		</TabHold>
	{/if}

	<ConfirmDialog
		bind:open={confirmVault}
		title={`Hide "${held.name}"?`}
		consequence={'It disappears from every list, count and search box, on your screens ' +
			'\u2014 and so does every file carrying it. Unlock Hidden with your PIN to bring it ' +
			'back. Nothing is deleted or moved.'}
		confirmLabel="Hide it"
		onconfirm={conceal}
	/>

	<ConfirmDialog
		bind:open={confirmDelete}
		title="Delete this song?"
		consequence="Its files stay exactly where they are and keep their music fingerprints. Their Music field is left empty."
		confirmLabel="Delete it"
		onconfirm={remove}
	/>
{/if}

<ShareDialog bind:open={shareOpen} targets={shareTarget ? [shareTarget] : []} />
<!-- Renaming an artist on every song, from the right-click on an artist under the name. -->
<ArtistRenameDialog />
<VisibilityDialog bind:open={reachOpen} target={shareTarget} />

<!-- No button of its own: the row in the Options menu opens it. -->
{#if song}
	<MergeEntities
		people={[{ id: song.id, name: song.name, files: song.item_count }]}
		kind="song"
		withButton={false}
		bind:open={mergeOpen}
		onmerged={(into) => goto(`/songs/${into}`)}
	/>
{/if}

<!-- The pencil's sheet: the song's own files, through the same route the menu writes through. -->
{#if song}
	<PickPicture
		bind:open={pickingPicture}
		name={song.name}
		query={{ songs: songId }}
		current={song.cover_asset_id}
		onpick={useAsCover}
		onupload={uploadCover}
	/>
{/if}
