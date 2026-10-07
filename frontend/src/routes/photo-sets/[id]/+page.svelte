<script lang="ts">
	/*
	 * One photo set: its cover and name, the pictures in it, and what else those pictures reach.
	 *
	 * The same shape a person's page has (one frame, one scrolling region, and the tabs inline with
	 * the heading), because it is the same kind of page: a thing, and several walls that are all
	 * about it. The pictures come back in the set's own order rather than newest-first, since a shoot
	 * was numbered and showing it shuffled is showing something else.
	 */
	import type { Crumb } from '$lib/components/common';
	import { coverBody, type Frame } from '$lib/entity/cover-frame';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import { page } from '$app/state';
	import {
		Button,
		ConfirmDialog,
		ContextMenuGroup,
		ContextMenuItem,
		Empty,
		Field,
		Problem,
		Skeleton
	} from '$lib/components/common';
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import PickPicture from '$lib/components/entity/PickPicture.svelte';
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
	import { narrowedFilesTotal, picksOf, tabsCarryingPicks } from '$lib/components/entity/picks';
	import EntityHistory from '$lib/components/entity/EntityHistory.svelte';
	import RelatedWall from '$lib/components/entity/RelatedWall.svelte';
	import TabHold, { wallOfTab } from '$lib/components/entity/TabHold.svelte';
	import ShareDialog from '$lib/components/ShareDialog.svelte';
	import VisibilityDialog from '$lib/components/VisibilityDialog.svelte';
	import type { ShareTarget } from '$lib/library/sharing';
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
	import { toasts } from '$lib/shell/toasts.svelte';
	import type { components } from '$lib/api/schema';
	import EntityDropZone from '$lib/components/entity/EntityDropZone.svelte';
	import { picturesSized, sizeOf } from '$lib/entity/entity-counts';

	type PhotoSet = Pick<
		components['schemas']['PhotoSetSummary'],
		| 'id'
		| 'name'
		| 'cover_asset_id'
		| 'cover_upload_id'
		| 'cover_at_ms'
		| 'cover_frame'
		| 'art'
		| 'item_count'
		| 'origin'
		| 'origin_url'
		| 'notes'
		| 'favorite'
		| 'rating'
		| 'o_count'
	>;

	const setId = $derived(page.params.id ?? '');
	/* History has no wall behind it, so it is kept out of `tabsFor`. */
	const HISTORY = 'history';

	const asked = $derived(page.url.searchParams.get('show'));
	const showingHistory = $derived(asked === HISTORY);
	const shown = $derived<RelatedKind>(chosenTab('photo_set', asked));
	const fileWords = new TabWords();

	/* The subject, and the three flags that go with fetching it, shared with every other entity
	 * detail page rather than written out here. See `EntitySubject` for what it decides, which is
	 * one thing: a placeholder belongs on a screen with nothing on it, so re-reading this album
	 * leaves this album on screen.
	 *
	 * `follow` takes both subscriptions, so a name, a heart or a cover that has moved is re-read
	 * rather than left until somebody reloads the page.
	 */
	const subject = new EntitySubject<PhotoSet>((id) => api.get<PhotoSet>(`/photo-sets/${id}`));
	subject.follow(() => setId);
	/* Read through a `const` so the markup keeps its narrowing (see the person page). */
	const set = $derived(subject.value);

	/* The two boxes the rename and the notes are typed into, kept in step with whatever is on
	   screen. An effect on the row itself follows a re-read as well as a first load; the shared
	   fetch knows nothing about this page's form. */
	$effect(() => {
		renamed = set?.name ?? '';
		noted = set?.notes ?? '';
	});

	/* Everything the record is made of, in one place: the summary under the name, the panel beside
	   it and the form are three surfaces over the same facts. See the person's page. */
	const recordValues = $derived({
		name: set?.name ?? '',
		details: set?.notes ?? '',
		origin_url: set?.origin_url ? [{ url: set.origin_url }] : []
	});
	let renamed = $state('');
	let noted = $state('');
	let busy = $state(false);
	let confirmDelete = $state(false);

	/* Sharing a set shares the shoot, and keeps doing so as more of it arrives, which is the point
	   of sharing a subject rather than a folder: it reaches next month's pictures without anybody
	   going back to it. */
	let shareOpen = $state(false);
	let reachOpen = $state(false);
	const shareTarget = $derived<ShareTarget | null>(
		set ? { type: 'photo_set', id: set.id, label: set.name } : null
	);
	/*
	 * The numbers beside the tab words.
	 *
	 * `follow` asks for all of them in one request the moment the page settles on a subject, so the
	 * strip opens complete rather than filling in as somebody presses each tab. `saw` is what the
	 * wall on screen actually found, which is the fresher of the two and wins. Both come from the
	 * same listings, so they are one population and not two.
	 */
	const counts = new TabCounts();
	let editing = $state(false);

	/* What this page can do to this album, behind the one door every entity page wears.
	 *
	 * Delete is declared HERE rather than left to the header, which appends its own only when a page
	 * hands over `ondelete`. This one asks its own question, in its own words, about an album whose
	 * files are not touched, so it stays the page's to word.
	 */
	const options = $derived<Verb[]>(
		session.isAdmin
			? [
					{
						id: 'share',
						label: 'Sharing',
						icon: 'group' as const,
						run: () => (shareOpen = true)
					},
					/* What the sharing above it comes to. Share is where a decision is made; this
					   reports who can actually reach this, however the reach was arranged: through
					   a folder, a tag, a set, or the network above a label, none of which are
					   written here. */
					{
						id: 'visibility',
						label: 'Visibility',
						icon: 'policy' as const,
						run: () => (reachOpen = true)
					},
					{
						id: 'delete',
						label: 'Delete',
						icon: 'delete' as const,
						destructive: true,
						run: () => (confirmDelete = true)
					}
				]
			: []
	);
	/** Whether the picture chooser is open. Opened by the pencil on the cover. */
	let pickingPicture = $state(false);

	/* WHO MADE IT, for the line under the name. Read on its own rather than off the row: the row's
	   shape is the WALL's too, so a maker on it would be filled here and null there with nothing to
	   say which meaning the null had. See `makerOf`.

	   Guarded by the id it was asked for, like every other per-entity read on these screens: a
	   slower answer for a Photo Set navigated away from must not land under this heading. */
	let madeBy = $state<Maker | null>(null);
	let madeByFor = $state('');
	$effect(() => {
		const one = setId;
		if (!one || madeByFor === one) return;
		madeByFor = one;
		void makerOf('photo_set', one).then((held) => {
			if (madeByFor === one) madeBy = held;
		});
	});

	/** The whole record, saved once. The name and the details go to two routes because they are two
	 *  routes; what changed is what is sent. */
	async function saveRecord(draft: Record<string, unknown>) {
		if (!set) return;
		const wanted = String(draft.name ?? '').trim() || set.name;
		if (wanted !== set.name) {
			subject.value = await api.put<PhotoSet>(`/photo-sets/${set.id}`, { body: { name: wanted } });
		}
		const notes = String(draft.details ?? '').trim() || null;
		if (notes !== (set.notes ?? null)) {
			subject.value = await api.put<PhotoSet>(`/photo-sets/${set.id}/notes`, { body: { notes } });
		}
		editing = false;
		toasts.show('Saved', { tone: 'success' });
	}

	$effect(() => counts.follow('photo_set', setId));
	/* The strip's numbers follow the library as its walls do: History has no wall to report one. */
	reloadOnLibraryChange(() => counts.refresh());

	/* How many files the Files tab holds while picks filter it (null: nothing picked). Asked of
	   the same listing the tab reads, once per change of the picks, with a sequence number so a
	   slower answer for picks moved away from cannot land over the current one. */
	let narrowedFiles = $state<number | null>(null);
	let narrowedAsk = 0;
	$effect(() => {
		const url = page.url;
		const subject = set;
		const ask = (narrowedAsk += 1);
		if (!subject || picksOf(url).length === 0) {
			narrowedFiles = null;
			return;
		}
		void narrowedFilesTotal(url, { photo_sets: setId, photo_set: setId }).then((total) => {
			if (ask === narrowedAsk) narrowedFiles = total;
		});
	});

	const tabs = $derived([
		...tabsFor('photo_set', setId, `/photo-sets/${setId}`, {
			...counts.current,
			// The filtered figure while picks are in force, the record's own otherwise. See `narrowedFilesTotal`.
			files: narrowedFiles ?? set?.item_count
		}),
		/* And it wears its number: the strip is a MAP of what this page can show, and one bare
		   word on a row of numbered ones reads as a tab nobody has looked at yet. It is the
		   length of the very thread the pane draws, read beside the strip (`readThread`). */
		{
			id: HISTORY,
			label: 'History',
			icon: 'history' as const,
			href: `/photo-sets/${setId}?show=${HISTORY}`,
			count: counts.current.history
		}
	]);

	/* This account's own opinion of the set, not the set's. The store puts a failed write back on
	   its own, so all these have to do is say so. Silence would be a heart that springs back with
	   nothing on screen to explain it. */
	async function heart(favorite: boolean) {
		if (!set) return;
		try {
			await api.put(`/photo-sets/${set.id}/favorite`, { body: { favorite } });
			subject.value = { ...set, favorite };
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(rating: number | null) {
		if (!set) return;
		try {
			await api.put(`/photo-sets/${set.id}/rating`, { body: { rating } });
			subject.value = { ...set, rating };
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rename(event: SubmitEvent) {
		event.preventDefault();
		if (!set || busy) return;
		busy = true;
		try {
			subject.value = await api.put<PhotoSet>(`/photo-sets/${set.id}`, {
				body: { name: renamed.trim() }
			});
			toasts.show('Renamed', { tone: 'success' });
		} catch {
			toasts.show("That couldn't be renamed", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	/* What somebody wrote about the shoot. Its own field rather than part of the rename form: a
	   name is shared vocabulary and a note is a paragraph, and putting them in one form would make
	   saving one of them save the other. */
	async function saveNotes(event: SubmitEvent) {
		event.preventDefault();
		if (!set || busy) return;
		busy = true;
		try {
			const held = noted.trim();
			subject.value = await api.put<PhotoSet>(`/photo-sets/${set.id}/notes`, {
				body: { notes: held || null }
			});
			toasts.show('Saved', { tone: 'success' });
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		} finally {
			busy = false;
		}
	}

	/* The cover, chosen from the set itself, which is the only place it can honestly be chosen
	   from, since a cover is a picture of what is inside. The same control the collection page
	   carries, in the same slot on the same menu. */
	async function useAsCover(
		assetId: string,
		atMs: number | null = null,
		frame: Frame | null = null
	) {
		if (!set) return;
		try {
			subject.value = await api.put<PhotoSet>(`/photo-sets/${set.id}/cover`, {
				body: coverBody(assetId, atMs, { frame })
			});
			toasts.show("That's the cover now", { tone: 'success' });
		} catch {
			toasts.show("That couldn't be made the cover", { tone: 'error' });
		}
	}

	/* A picture from OUTSIDE the album, which is the one thing "chosen from the set itself" above
	   could not answer: an album of a shoot may have no single picture that stands for it. The
	   refusal is deliberately not caught: the sheet shows the server's own sentence, which is the
	   half that knows whether the file was too big or was not readable as a picture. */
	async function uploadCover(file: File) {
		if (!set) return;
		const form = new FormData();
		form.set('file', file);
		subject.value = await api.post<PhotoSet>(`/photo-sets/${set.id}/cover-picture`, { body: form });
		toasts.show("That's the cover now", { tone: 'success' });
	}

	/* Taking a picture out of the set. It leaves the grouping and stays exactly where it is on disk,
	   which is what the wording has to say. Next to a Delete on the same menu, anything vaguer
	   reads as the other thing. */
	async function takeOut(assetId: string, forget: (id: string) => void) {
		if (!set) return;
		try {
			await api.post(`/photo-sets/${set.id}/items`, {
				query: { remove: true },
				body: { asset_ids: [assetId] }
			});
			forget(assetId);
			// The set's History thread records this, so it is told at once, as every other write in
			// this tab that a history shows does.
			recorded.changed();
			subject.value = { ...set, item_count: Math.max(0, set.item_count - 1) };
			toasts.show('Out of the set. The file is where it was.', { tone: 'success' });
		} catch {
			toasts.show("Couldn't remove that", { tone: 'error' });
		}
	}

	async function remove() {
		if (!set) return;
		try {
			await api.del(`/photo-sets/${set.id}`);
			toasts.show('The set is gone. The pictures are where they were.', { tone: 'success' });
			await leaveFor('/photo-sets');
		} catch {
			toasts.show("That couldn't be deleted", { tone: 'error' });
		}
	}
	/* The trail, handed to whichever grid is showing and drawn by the frame's band above the
	   identity band: the same trail on every tab of this page. */
	const crumbs = $derived<Crumb[]>([
		{ label: 'Photo Sets', href: '/photo-sets' },
		{ label: set?.name ?? '' }
	]);
</script>

<svelte:head><title>{set?.name ?? 'Photo Set'}</title></svelte:head>

<!-- A link dropped anywhere on this page is fetched and filed under it, exactly as one
     dropped on its card on the wall is. Only while there IS one: a page still settling
     has no id to aim at. See `EntityDropZone`. -->
{#if set}
	<EntityDropZone kind="photo_set" id={set.id} name={set.name} />
{/if}

{#if subject.settling}
	<Skeleton lines={3} />
{:else if subject.unreadable}
	<Problem message="That Photo Set couldn't be loaded. It's still there — try again in a moment." />
{:else if !set}
	<Empty scope="page" icon="photo_library" title="That Photo Set isn't here"
		>It may have been deleted.</Empty
	>
{:else}
	{@const shot = set}
	{#snippet tabStrip()}
		<Tabs
			tabs={tabsCarryingPicks(tabs, page.url)}
			current={showingHistory ? HISTORY : shown}
			label="What to show for {shot.name}"
		/>
		<!-- The cards picked on the tabs, counted, and the press that filters the Files tab to
		     them. Draws nothing while nothing is picked. See `picks.ts`. -->
		<PickedFilter />
	{/snippet}

	{#snippet identity()}
		<EntityHeader
			{options}
			optionIds={[setId]}
			bind:editing
			icon={iconOf('photo_set')}
			kind="photo_set"
			mayEdit={session.isAdmin}
			name={shot.name}
			onpicture={() => (pickingPicture = true)}
			oncover={async (assetId, atMs, more) => {
				subject.value = await api.put<PhotoSet>(`/photo-sets/${setId}/cover`, {
					body: coverBody(assetId, atMs, more)
				});
			}}
			coverHref={`/photo-sets/${setId}`}
			coverAssetId={shot.cover_asset_id}
			coverUploadId={shot.cover_upload_id}
			coverAtMs={shot.cover_at_ms}
			coverFrame={shot.cover_frame}
			art={shot.art}
			counts={picturesSized(shot.item_count, sizeOf(shot))}
			oCount={shot.o_count}
			favorite={shot.favorite}
			rating={shot.rating}
			onfavorite={(next) => heart(next)}
			onrate={(next) => rate(next)}
			{madeBy}
		>
			{#snippet summary()}
				<RecordSummary subject="photo_set" label="About {shot.name}" values={recordValues} />
			{/snippet}

			{#snippet facts()}
				<RecordFacts subject="photo_set" label="Facts about {shot.name}" values={recordValues} />
			{/snippet}

			{#snippet record()}
				<RecordView subject="photo_set" label="More about {shot.name}" values={recordValues} />
			{/snippet}
		</EntityHeader>
	{/snippet}

	{#snippet editing_form()}
		<RecordForm
			subject="photo_set"
			label="Editing {shot.name}"
			values={{ ...recordValues, origin_url: shot.origin_url ? [shot.origin_url] : [] }}
			onsave={saveRecord}
			oncancel={() => (editing = false)}
		/>
	{/snippet}

	{#if editing}
		<!-- Editing takes the page. See the person's, which explains why. -->
		<PageFrame header={identity}>
			{#snippet children()}
				{@render editing_form()}
			{/snippet}
		</PageFrame>
	{:else if showingHistory}
		<!--
			The thread, in the frame every other screen uses. Not a wall: nothing to select, nothing
			to page and nothing to count, so the grid's furniture would be furniture with no work
			behind it. The identity band stays: this is a different view OF the set, not a
			different page.

			WITHOUT `measure`: that bounds the line AND centres it, and the thread belongs at the
			page's own left edge where the tabs and the title are.
		-->
		<!-- History draws the identity and the tab strip in the shape every other tab does
		     (`PageAbove`, then the strip as the heading row), so the strip stands at one height
		     on every tab. -->
		<PageFrame {crumbs}>
			{#snippet header()}
				<PageAbove>{@render identity()}</PageAbove>
				<PageHeader title="History" icon="history" level={2} titleHidden beside={tabStrip} />
			{/snippet}
			{#snippet children()}
				<EntityHistory subject="photo_set" id={setId} name={shot.name} />
			{/snippet}
		</PageFrame>
	{:else}
		<TabHold tab={shown} wallOf={wallOfTab}>
			{#snippet surface(tab, arrived)}
				{#if tab === 'files'}
					<AssetGrid
						oncount={arrived}
						query={{ photo_sets: setId, photo_set: setId }}
						title="Pictures"
						titleLevel={2}
						beside={tabStrip}
						titleHidden
						above={identity}
						{crumbs}
						empty={emptyWallSays('pictures', fileWords.asked, false, 'Nothing in this set yet.')}
						pinnable
						menuExtraGrouped
					>
						{#snippet tools()}
							<WallControls
								noun="picture"
								plural="pictures"
								bind:term={fileWords.term}
								onsettled={(typed) => fileWords.write(typed)}
							/>
						{/snippet}
						{#snippet menuExtra(item, grid)}
							<!-- The two things this screen can do to a picture that the grid knows nothing about.
						     Admin only, because both change what everybody sees: the cover is the set's face,
						     and membership is what the set IS. -->
							{#if session.isAdmin}
								<!-- Two parts of the menu, in its order: filing (what the set holds), then
							     changing the set (its face). -->
								<ContextMenuGroup>
									<ContextMenuItem
										label="Remove from this Photo Set"
										icon="close"
										onselect={() => void takeOut(item.id, grid.forget)}
									/>
								</ContextMenuGroup>
								<ContextMenuGroup>
									<ContextMenuItem
										label="Use as this set's cover"
										icon="photo_library"
										onselect={() => void useAsCover(item.id)}
									/>
								</ContextMenuGroup>
							{/if}
						{/snippet}
					</AssetGrid>
				{:else}
					<RelatedWall
						on="photo_set"
						id={setId}
						named={set.name}
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

	{#if shareTarget}
		<ShareDialog bind:open={shareOpen} targets={[shareTarget]} />
		<VisibilityDialog bind:open={reachOpen} target={shareTarget} />
	{/if}

	<ConfirmDialog
		bind:open={confirmDelete}
		title="Delete this Photo Set?"
		consequence="The pictures stay exactly where they are on disk. Only the grouping goes."
		confirmLabel="Delete it"
		onconfirm={remove}
	/>
{/if}

<!-- The pencil's sheet. The same files this page's own wall shows, and the same route the
     right-click menu already writes through: one way to set a cover, reached from two places. -->
{#if set}
	<PickPicture
		bind:open={pickingPicture}
		name={set.name}
		query={{ photo_sets: setId, photo_set: setId }}
		current={set.cover_asset_id}
		onpick={useAsCover}
		onupload={uploadCover}
	/>
{/if}

<style>
</style>
