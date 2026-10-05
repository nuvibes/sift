<script lang="ts">
	/*
	 * One tag: its files, and everything else those files reach.
	 *
	 * A tag is a place rather than only a word that writes a filter into the grid, so there is
	 * somewhere to say who turns up under it, which sites it comes from, or which shoots carry it.
	 * It is an entity like the other four, with the same frame and the same tabs.
	 */
	import {
		EntityEnrichment,
		autoEnrichRows,
		enrichBoxes,
		loadEnrichBoxes,
		sayKeptLocal,
		setKeptLocal
	} from '$lib/entity/enrichment.svelte';
	import type { Frame } from '$lib/entity/cover-frame';
	import { filesSized, sizeOf } from '$lib/entity/entity-counts';
	import type { Crumb } from '$lib/components/common';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { EntitySubject } from '$lib/entity/subject.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import PickPicture from '$lib/components/entity/PickPicture.svelte';
	import EntityHeader from '$lib/components/entity/EntityHeader.svelte';
	import type { Verb } from '$lib/components/common/verbs';
	import Tabs from '$lib/components/common/Tabs.svelte';
	import PickedFilter from '$lib/components/entity/PickedFilter.svelte';
	import { narrowedFilesTotal, picksOf, tabsCarryingPicks } from '$lib/components/entity/picks';
	import EntityHistory from '$lib/components/entity/EntityHistory.svelte';
	import { waitingText } from '$lib/entity/reconcile.svelte';
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
	import { tags, type Tag } from '$lib/entity/tags.svelte';
	import { Button, ContextMenuItem, Empty, Problem, Skeleton } from '$lib/components/common';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageAbove from '$lib/components/shell/PageAbove.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import RecordForm from '$lib/components/record/RecordForm.svelte';
	import RecordSummary from '$lib/components/record/RecordSummary.svelte';
	import RecordFacts from '$lib/components/entity/RecordFacts.svelte';
	import Disagreements from '$lib/components/record/Disagreements.svelte';
	import RecordView from '$lib/components/record/RecordView.svelte';
	import StashBoxIds from '$lib/components/record/StashBoxIds.svelte';
	import LinkToStashBox from '$lib/components/record/LinkToStashBox.svelte';
	import { givenBy, sourcesOf, type Maker, type StashBoxLink } from '$lib/entity/enrich.svelte';
	import { enrichMany } from '$lib/entity/enrich-many.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import EntityDropZone from '$lib/components/entity/EntityDropZone.svelte';

	const tagId = $derived(page.params.id ?? '');
	/* History has no wall behind it, so it is kept out of `tabsFor`. */
	const HISTORY = 'history';

	const asked = $derived(page.url.searchParams.get('show'));
	const showingHistory = $derived(asked === HISTORY);
	const shown = $derived<RelatedKind>(chosenTab('tag', asked));
	const fileWords = new TabWords();

	/*
	 * The numbers beside the tab words.
	 *
	 * `follow` asks for all of them in one request the moment the page settles on a subject, so the
	 * strip opens complete rather than filling in as somebody presses each tab. `saw` is what the
	 * wall on screen actually found, which is the fresher of the two and wins. Both come from the
	 * same listings, so they are one population and not two.
	 */
	const counts = new TabCounts();
	$effect(() => counts.follow('tag', tagId));
	/* The strip's numbers follow the library as its walls do: History has no wall to report one. */
	reloadOnLibraryChange(() => counts.refresh());

	/* How many fields a stash-box disagrees with about this record, out of the strip's own numbers.
	 *
	 * Off the counts map rather than asked for separately, because it arrives with every other number
	 * on that strip (one request, one moment), and because the panel on the History tab OVERWRITES
	 * it through `saw` the moment it has read them itself. Two readers of one number is how a mark
	 * comes to stand over a panel that has nothing in it.
	 *
	 * Undefined until the strip has answered, which draws no mark: a mark that appeared and then went
	 * again would report a question that was never there. */
	const disagreeing = $derived(counts.current.disagreements ?? 0);

	/* Fetched BY ID, never read out of a wall's page: a wall is one capped page in a chosen
	   order, so anything ranked past the cap would have no page at all, and anything that
	   replaced the cached page would take the subject out from under an already-open one.

	   The subject, and the three flags that go with fetching it, shared with every other
	   entity detail page rather than written out here. See `EntitySubject` for what it decides,
	   which is one thing: a placeholder belongs on a screen with nothing on it, so re-reading
	   this row leaves this row on screen.

	   `follow` takes both subscriptions, so a name, a heart or a cover that has moved is re-read
	   rather than left until somebody reloads the page. */
	const subject = new EntitySubject<Tag>((id) => tags.one(id));
	subject.follow(() => tagId);
	/* Read through a `const` so the markup keeps its narrowing (see the person page). */
	const tag = $derived(subject.value);

	/** The id the header's Save submits. One form is open at a time, so one name is enough. */
	const RECORD_FORM = 'tag-record-form';

	/* Deleting the tag. The files it was on are untouched: what goes is the tag, its other names,
	 * and the rows joining it to files, so those files simply stop carrying it.
	 */
	async function removeTag() {
		await tags.remove(tagId);
		toasts.show('Deleted. The files it was on are still here.', { tone: 'success' });
		await leaveFor('/tags');
	}

	/* Set the still this tag is drawn as, from one of its own files. Admin-only, like the server
	 * behind it: which picture represents a tag is shared vocabulary the same way its name is. */
	async function makeCover(
		assetId: string,
		atMs: number | null = null,
		frame: Frame | null = null
	) {
		try {
			subject.value = await tags.setCover(tagId, assetId, atMs, { frame });
			toasts.show('Cover set', { tone: 'success' });
		} catch {
			toasts.show("That couldn't be used as the cover", { tone: 'error' });
		}
	}

	async function uploadCover(file: File) {
		subject.value = await tags.uploadCover(tagId, file);
		toasts.show('Cover set', { tone: 'success' });
	}

	/* How many files the Files tab holds while picks filter it (null: nothing picked). Asked of
	   the same listing the tab reads, once per change of the picks, with a sequence number so a
	   slower answer for picks moved away from cannot land over the current one. */
	let narrowedFiles = $state<number | null>(null);
	/* The files under this tag AND the tags filed under it: the Files wall's own total, from the
	   strip's one request. The record's count is this tag's own files only, so it is the stand-in
	   until the strip answers, never the figure beside a wall that shows more. */
	const filesUnder = $derived(counts.current.files ?? tag?.asset_count);
	/* How big those same files are: off the strip's answer while its number is the one said, and
	   off the record while the record's is. Never one source's size beside the other's count. */
	const filesUnderBytes = $derived(
		counts.current.files !== undefined ? counts.current.files_bytes : tag ? sizeOf(tag) : null
	);
	let narrowedAsk = 0;
	$effect(() => {
		const url = page.url;
		const subject = tag;
		const ask = (narrowedAsk += 1);
		if (!subject || picksOf(url).length === 0) {
			narrowedFiles = null;
			return;
		}
		void narrowedFilesTotal(url, { tags: tag.name }).then((total) => {
			if (ask === narrowedAsk) narrowedFiles = total;
		});
	});

	const tabs = $derived(
		tagId
			? [
					...tabsFor('tag', tagId, `/tags/${tagId}`, {
						...counts.current,
						// The filtered figure while picks are in force; otherwise the Files wall's own total,
						// which takes in the tags filed under this one, and the record's until it arrives.
						files: narrowedFiles ?? filesUnder
					}),
					/* And it wears its number: the strip is a MAP of what this page can show,
					   and one bare word on a row of numbered ones reads as a tab nobody has
					   looked at yet. It is the count of the very thread the pane draws, at the
					   same cap. See `history_count_of_entity`. */
					{
						id: HISTORY,
						label: 'History',
						icon: 'history' as const,
						href: `/tags/${tagId}?show=${HISTORY}`,
						count: counts.current.history,
						/* And a mark where a stash-box disagrees with this record. It is on THIS word
						   because the panel that settles it is at the top of this tab, so the mark is
						   what says the question is there without anybody opening anything. Absent at
						   nought and absent for anyone who may not settle them, which is what an absent
						   count from the strip already means. */
						attention: disagreeing > 0 ? waitingText(disagreeing, counts.boxes) : undefined
					}
				]
			: []
	);

	/* A tag has a record.
	 *
	 * Beyond its name and colour, what it means, the other words for the same thing and its one
	 * category are three facts worth writing down: a tag whose meaning is recorded once is a tag two people use the same way.
	 */
	let editing = $state(false);

	/* What this page can do to this tag, behind the one door every entity page wears. */
	/* WHERE THIS ROW STANDS WITH ENRICHMENT, so the two rows that send its name outside are
	   drawn refused rather than refused on the press. See `EntityEnrichment`: one route answers
	   it, and pressing the row below writes the reply back. */
	const enrichment = new EntityEnrichment('tag');
	enrichment.follow();
	const enrichState = $derived(enrichment.of(tag?.id));
	const enrichRefused = $derived(enrichState?.refused === true);
	const enrichWhy = $derived(enrichState?.why || undefined);
	const enrichKept = $derived(enrichState?.kept_local === true);
	$effect(() => {
		enrichment.ask(tag?.id);
	});
	/* The box list for Auto-enrich's row per box: the same list every wall's flyout reads. */
	$effect(() => {
		if (session.isAdmin) loadEnrichBoxes();
	});
	/* AUTO-ENRICH, THE SAME ROWS EVERY SURFACE DRAWS: every box, then each box by name, and the
	   box handed on. With no list it is a plain press, which asks what Settings says. See
	   `autoEnrichRows`, which also says why there is no "Sift's own" row. */
	function autoEnrichThis(id: string, box: string = ''): void {
		if (enrichRefused) return void sayKeptLocal();
		void enrichMany('tag', [id], box);
	}

	const options = $derived<Verb[]>(
		session.isAdmin
			? [
					/* AUTO-ENRICH, as on a person's and a Site's page: the same three rows in
					   the same order as every other surface. */
					{
						id: 'auto-enrich',
						label: 'Auto-enrich',
						icon: 'auto_fix_high' as const,
						...(enrichRefused ? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) } : {}),
						...(tag && enrichBoxes().length > 0 && !enrichRefused
							? {
									children: autoEnrichRows(enrichBoxes(), (_ids, box) => {
										if (tag) autoEnrichThis(tag.id, box);
									})
								}
							: {
									run: () => {
										if (tag) autoEnrichThis(tag.id);
									}
								})
					},
					/* The chooser, called what it is called everywhere else, with the same word
					   and the same glyph. */
					{
						id: 'enrich',
						label: 'Enrich',
						icon: 'backlight_low' as const,
						...(enrichRefused ? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) } : {}),
						run: () => (enrichRefused ? sayKeptLocal() : (lookUpOpen = true))
					},
					/* KEPT LOCAL, beside the row it refuses. See the Site page. */
					{
						id: 'keep-local',
						label: enrichKept ? 'Allow enrichment' : "Don't enrich",
						icon: enrichKept ? ('public' as const) : ('shield' as const),
						run: () => {
							if (!tag) return;
							const which = tag.id;
							void setKeptLocal('tag', which, !enrichKept).then((state) =>
								enrichment.mark([which], state)
							);
						}
					}
				]
			: []
	);
	/** Whether the picture chooser is open. Opened by the pencil on the cover. */
	let pickingPicture = $state(false);
	let sources = $state<StashBoxLink[]>([]);
	/* Which box MADE this thing, read in the same answer the links come in (see `sourcesOf`).
	   Null for everything nothing recorded, which is most of a library. */
	let madeBy = $state<Maker | null>(null);
	let sourcesFor = $state('');
	let lookUpOpen = $state(false);

	/*
	 * Opened by the address, so the wall's Enrich verb can hand a tag straight to the chooser.
	 *
	 * The other two record pages answer `?enrich=1` the same way, so the Tags wall can offer the
	 * chooser beside Auto-enrich as the other walls do.
	 *
	 * Once, on arrival, and not as a `$derived`: this is a door being opened, not a fact about the
	 * page. Left reactive, closing the sheet with the parameter still in the address would
	 * immediately re-open it. The same shape, and the same note, as the Sites page.
	 */
	$effect(() => {
		if (untrack(() => lookUpOpen)) return;
		// Refused before the sheet opens, even where the address asked for it. See the Site page.
		if (page.url.searchParams.get('enrich') === '1' && !untrack(() => enrichRefused))
			lookUpOpen = true;
	});

	const recordValues = $derived({
		...(tag?.record ?? {}),
		name: tag?.name ?? '',
		sources
	});

	$effect(() => {
		const id = tagId;
		if (!id || sourcesFor === id) return;
		sourcesFor = id;
		void (async () => {
			const held = await sourcesOf('tag', id);
			// A slower answer for a tag navigated away from must not land under this heading.
			if (sourcesFor === id) {
				sources = held.links;
				madeBy = held.madeBy;
			}
		})();
	});

	/** After the chooser links and takes fields: the link list AND the record, because the take
	 *  route wrote through the record's own writer (so a hand link records what it filled in, the
	 *  same as one made from a queue) and this page holds a copy. */
	async function afterLinked() {
		await reloadSources();
		if (!tagId) return;
		try {
			subject.value = await tags.one(tagId);
		} catch {
			/* The save has landed; a failed re-read is a stale screen, not a failed save. */
		}
	}

	async function reloadSources() {
		if (!tagId) return;
		const held = await sourcesOf('tag', tagId);
		sources = held.links;
		madeBy = held.madeBy;
	}

	/* The one save, for the whole record.
	 *
	 * Everything a tag holds is on its own row, so this is a single write rather than the diff a
	 * person's page has to do across three tables. The name rides with it because the route that
	 * takes the record is the route that renames, and the reply carries the record back, so what
	 * is on screen after a save is what was STORED rather than what was typed.
	 */
	async function saveRecord(draft: Record<string, unknown>) {
		if (!tag) return;
		/* A tag filed under its own branch is refused in a sentence the form shows as it came. */
		subject.value = await tags.update(tag.id, {
			name: String(draft.name ?? '').trim() || tag.name,
			description: String(draft.description ?? '').trim(),
			category: String(draft.category ?? '').trim(),
			aliases: ((draft.aliases as string[]) ?? []).map((one) => one.trim()).filter(Boolean),
			/* "Part of": the tag this one is filed under, by name. Empty takes it back to the top. */
			parent: String(draft.parent ?? '').trim()
		});
		editing = false;
		toasts.show('Saved', { tone: 'success' });
	}
	/* The trail, handed to whichever grid is showing and drawn by the frame's band above the
	   identity band: the same trail on every tab of this page. */
	const crumbs = $derived<Crumb[]>([{ label: 'Tags', href: '/tags' }, { label: tag?.name ?? '' }]);
</script>

<svelte:head><title>{tag?.name ?? 'Tag'}</title></svelte:head>

<!-- A link dropped anywhere on this page is fetched and filed under it, exactly as one
     dropped on its card on the wall is. Only while there IS one: a page still settling
     has no id to aim at. See `EntityDropZone`. -->
{#if tag}
	<EntityDropZone kind="tag" id={tag.id} name={tag.name} />
{/if}

{#if subject.settling}
	<Skeleton lines={3} />
{:else if subject.unreadable}
	<Problem message="That tag couldn't be loaded. It's still there — try again in a moment." />
{:else if !tag}
	<Empty scope="page" icon="shoppingmode" title="That tag isn't here"
		>It may have been deleted, or merged into another tag.</Empty
	>
{:else}
	{#snippet tabStrip()}
		<Tabs
			tabs={tabsCarryingPicks(tabs, page.url)}
			current={showingHistory ? HISTORY : shown}
			label="What to show for {tag.name}"
		/>
		<!-- The cards picked on the tabs, counted, and the press that filters the Files tab to
		     them. Draws nothing while nothing is picked. See `picks.ts`. -->
		<PickedFilter />
	{/snippet}

	{#snippet identity()}
		<EntityHeader
			{options}
			optionIds={[tagId]}
			bind:editing
			icon={iconOf('tag')}
			kind="tag"
			mayEdit={session.isAdmin}
			saveForm={editing ? RECORD_FORM : undefined}
			deleteWord="tag"
			ondelete={session.isAdmin ? removeTag : undefined}
			name={tag.name}
			onpicture={() => (pickingPicture = true)}
			oncover={async (assetId, atMs, more) => {
				subject.value = await tags.setCover(tagId, assetId, atMs, more);
			}}
			coverHref={`/tags/${tag.id}`}
			coverAssetId={tag.cover_asset_id}
			coverUploadId={tag.cover_upload_id}
			coverAtMs={tag.cover_at_ms}
			coverFrame={tag.cover_frame}
			art={tag.art}
			counts={`On ${filesSized(filesUnder ?? tag.asset_count, filesUnderBytes)}`}
			oCount={tag.o_count}
			favorite={tag.favorite}
			rating={tag.rating ?? null}
			enrichedBy={sources.map((one) => ({ name: one.box_name, box: one.box_slug }))}
			{madeBy}
			onfavorite={async (next) => {
				await tags.setFavorite(tag.id, next);
				subject.value = subject.value ? { ...subject.value, favorite: next } : subject.value;
			}}
			onrate={async (next) => {
				await tags.setRating(tag.id, next);
				subject.value = subject.value ? { ...subject.value, rating: next } : subject.value;
			}}
		>
			{#snippet summary()}
				<RecordSummary subject="tag" label="About {tag.name}" values={recordValues} />
			{/snippet}

			{#snippet facts()}
				<RecordFacts
					subject="tag"
					label="Facts about {tag.name}"
					values={recordValues}
					given={givenBy(sources)}
				/>
			{/snippet}

			{#snippet record()}
				<!-- Which entry in each stash-box this is: under More, with the rest of the record, and
				     first there, on the facts' columns. Read by every viewer; Remove is an admin's. See
				     `StashBoxIds`. -->
				<StashBoxIds
					subject="tag"
					id={tag.id}
					name={tag.name}
					links={sources}
					mayForget={session.isAdmin}
					onforgot={reloadSources}
				/>
				<RecordView
					subject="tag"
					label="More about {tag.name}"
					values={recordValues}
					given={givenBy(sources)}
				/>
			{/snippet}
		</EntityHeader>
	{/snippet}

	{#snippet editing_form()}
		<RecordForm
			subject="tag"
			formId={RECORD_FORM}
			label="Editing {tag.name}"
			values={recordValues}
			onsave={saveRecord}
			oncancel={() => (editing = false)}
		/>
	{/snippet}

	{#if editing}
		<!-- Editing takes the page, exactly as it does for a person and a site: one Save at the end
		     of a whole record needs the window rather than a strip above a wall. -->
		<PageFrame header={identity}>
			{#snippet children()}
				{@render editing_form()}
			{/snippet}
		</PageFrame>
	{:else if showingHistory}
		<!--
			The thread, in the frame every other screen uses. Not a wall: nothing to select, nothing
			to page and nothing to count, so the grid's furniture would be furniture with no work
			behind it. The identity band stays: this is a different view OF the thing, not a
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
				<EntityHistory subject="tag" id={tagId} name={tag.name}>
					{#snippet waiting()}
						<!-- Where a stash-box disagrees with THIS record, at the top of the thread rather
						     than in the header. Draws nothing at all when nothing does, which is the
						     ordinary case. Admin-only: what a box wrote is shared vocabulary, like every
						     other stash-box control, and the strip answers None rather than a number for
						     anybody else, so the mark and the panel appear and disappear together. -->
						{#if session.isAdmin}
							<Disagreements
								subject="tag"
								localId={tag.id}
								onchange={(found, named) => counts.sawDisagreements(found, named)}
								onwritten={() => void afterLinked()}
							/>
						{/if}
					{/snippet}
				</EntityHistory>
			{/snippet}
		</PageFrame>
	{:else}
		<TabHold tab={shown} wallOf={wallOfTab}>
			{#snippet surface(tab, arrived)}
				{#if tab === 'files'}
					<!-- The same `AssetGrid` Browse draws, asking the one query language everything else uses:
				     `tags=` is the same parameter the filter panel sends and the same one `tags:` parses to. -->
					<AssetGrid
						oncount={arrived}
						query={{ tags: tag.name }}
						title="Files"
						titleLevel={2}
						beside={tabStrip}
						titleHidden
						above={identity}
						{crumbs}
						empty={emptyWallSays('files', fileWords.asked, false, 'Nothing carries this tag yet.')}
						pinnable
					>
						{#snippet tools()}
							<WallControls
								noun="file"
								plural="files"
								bind:term={fileWords.term}
								onsettled={(typed) => fileWords.write(typed)}
							/>
						{/snippet}
						{#snippet menuExtra(item)}
							{#if session.isAdmin}
								<!-- The same verb, in the same place, as a person's and a site's. A tag has a
							     picture like every entity with a page, and a wall of tags is where a picture helps
							     most: a tag's name says less about what is under it than a person's does. -->
								<ContextMenuItem
									label={tag?.cover_asset_id === item.id ? 'This is the cover' : 'Use as the cover'}
									icon="star"
									disabled={tag?.cover_asset_id === item.id}
									onselect={() => void makeCover(item.id)}
								/>
							{/if}
						{/snippet}
					</AssetGrid>
				{:else}
					<RelatedWall
						on="tag"
						id={tagId}
						named={tag.name}
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
{/if}

<!-- The same sheet the other two pages use. One screen, three kinds of subject. -->
{#if tag}
	<LinkToStashBox
		bind:open={lookUpOpen}
		subject="tag"
		id={tag.id}
		name={tag.name}
		values={recordValues}
		onlinked={afterLinked}
	/>
{/if}

<!-- The pencil's sheet. The same files this page's own wall shows, and the same route the
     right-click menu already writes through: one way to set a cover, reached from two places. -->
{#if tag}
	<PickPicture
		bind:open={pickingPicture}
		name={tag.name}
		query={{ tags: tag.name }}
		current={tag.cover_asset_id}
		onpick={makeCover}
		onupload={uploadCover}
	/>
{/if}

<style>
</style>
