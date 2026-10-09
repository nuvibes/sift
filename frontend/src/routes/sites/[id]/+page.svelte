<script lang="ts">
	import type { Crumb } from '$lib/components/common';
	import type { Frame } from '$lib/entity/cover-frame';
	import {
		filesSaid,
		filesSized,
		joinCounts,
		peopleSaid,
		sizeOf,
		withSize
	} from '$lib/entity/entity-counts';
	import { untrack } from 'svelte';
	/* One site, and the usernames on it. */
	import { goto } from '$app/navigation';
	import { leaveFor } from '$lib/shell/navigation.svelte';
	import { page } from '$app/state';
	import {
		Button,
		ContextMenuItem,
		DataRow,
		DataRows,
		Empty,
		Field,
		Problem,
		Skeleton
	} from '$lib/components/common';
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageAbove from '$lib/components/shell/PageAbove.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import RecordForm from '$lib/components/record/RecordForm.svelte';
	import EntityHeader from '$lib/components/entity/EntityHeader.svelte';
	import RecordSummary from '$lib/components/record/RecordSummary.svelte';
	import RecordFacts from '$lib/components/entity/RecordFacts.svelte';
	import Disagreements from '$lib/components/record/Disagreements.svelte';
	import RecordView from '$lib/components/record/RecordView.svelte';
	import StashBoxIds from '$lib/components/record/StashBoxIds.svelte';
	import Tabs from '$lib/components/common/Tabs.svelte';
	import PickedFilter from '$lib/components/entity/PickedFilter.svelte';
	import { narrowedFilesTotal, picksOf, tabsCarryingPicks } from '$lib/components/entity/picks';
	import EntityHistory from '$lib/components/entity/EntityHistory.svelte';
	import { waitingText } from '$lib/entity/reconcile.svelte';
	import RelatedWall from '$lib/components/entity/RelatedWall.svelte';
	import TabHold, { wallOfTab } from '$lib/components/entity/TabHold.svelte';
	import MergeEntities from '$lib/components/entity/MergeEntities.svelte';
	import type { Verb } from '$lib/components/common/verbs';
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
	import ShareDialog from '$lib/components/ShareDialog.svelte';
	import VisibilityDialog from '$lib/components/VisibilityDialog.svelte';
	import type { ShareTarget } from '$lib/library/sharing';
	import { sites, type Site } from '$lib/people/people.svelte';
	import { usernames, type Username } from '$lib/people/usernames.svelte';
	import { bareLine, usernamesHere as fileUsernames } from './usernames-here';
	import UsernameLines from '$lib/components/entity/UsernameLines.svelte';
	import UsernameCard from '$lib/components/entity/UsernameCard.svelte';
	import { libraryChanges, reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { EntitySubject } from '$lib/entity/subject.svelte';
	import { entityTags } from '$lib/entity/entity-tags.svelte';
	import LinkToStashBox from '$lib/components/record/LinkToStashBox.svelte';
	import PickPicture from '$lib/components/entity/PickPicture.svelte';
	import { givenBy, sourcesOf, type Maker, type StashBoxLink } from '$lib/entity/enrich.svelte';
	import {
		EntityEnrichment,
		autoEnrichRows,
		enrichBoxes,
		loadEnrichBoxes,
		sayKeptLocal,
		setKeptLocal
	} from '$lib/entity/enrichment.svelte';
	import { enrichMany } from '$lib/entity/enrich-many.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import EntityDropZone from '$lib/components/entity/EntityDropZone.svelte';

	const siteId = $derived(page.params.id ?? '');
	/* Fetched BY ID, never read out of a wall's page: a wall is one capped page in a chosen
	   order, so anything ranked past the cap would have no page at all, and anything that
	   replaced the cached page would take the subject out from under an already-open one. */
	const subject = new EntitySubject<Site>((id) => sites.one(id));
	subject.follow(() => siteId);
	/* Read through a `const` so the markup keeps its narrowing (see the person page). */
	const site = $derived(subject.value);

	let editing = $state(false);

	/** The whole record, saved in one request: the server asks every refusal before it writes, so
	 *  a refused parent or a taken name leaves the Site as it was rather than half saved. */
	async function saveRecord(draft: Record<string, unknown>) {
		if (!site) return;
		const name = String(draft.name ?? '').trim() || site.name;
		/* The site's whole list of addresses; its first IS the site's address. */
		const links = ((draft.links as string[]) ?? []).map((one) => one.trim()).filter(Boolean);
		const notes = String(draft.details ?? '').trim() || null;
		const aliases = ((draft.aliases as string[]) ?? []).map((one) => one.trim()).filter(Boolean);
		const parent = String(draft.parent ?? '').trim() || null;
		/* The reply carries the record with the parent's id, which only the server knows (saving
		   a network nobody has typed before creates it). */
		const saved = await sites.save(site.id, name, { notes, aliases, parent, links });
		subject.value = { ...saved, o_count: subject.value?.o_count ?? saved.o_count };

		/* Tags, which the form edits into its draft rather than writing as they are pressed. */
		const wantedTags = (draft.tags as { id: string }[] | undefined) ?? [];
		for (const gone of tags.filter((one) => !wantedTags.some((held) => held.id === one.id))) {
			await entityTags.set('sites', site.id, gone.id, false);
		}
		for (const added of wantedTags.filter((one) => !tags.some((held) => held.id === one.id))) {
			await entityTags.set('sites', site.id, added.id, true);
		}

		editing = false;
		toasts.show('Saved', { tone: 'success' });
	}

	/* Which wall this page is showing, read off the address so every tab is a real place: the
	   back button steps between them and a shared link opens on the one the sender was on. */
	/* History has no wall behind it, so it is kept out of `tabsFor`. */
	const HISTORY = 'history';

	const asked = $derived(page.url.searchParams.get('show'));
	const showingHistory = $derived(asked === HISTORY);
	const shown = $derived<RelatedKind>(chosenTab('site', asked));
	const fileWords = new TabWords();

	/* The numbers beside the tab words. */
	const counts = new TabCounts();
	$effect(() => counts.follow('site', siteId));
	/* The strip's numbers follow the library as its walls do: History has no wall to report one. */
	reloadOnLibraryChange(() => counts.refresh());

	/* How many fields a stash-box disagrees with about this record, out of the strip's own
	 * numbers. */
	const disagreeing = $derived(counts.current.disagreements ?? 0);

	/* How many files the Files tab holds while picks filter it (null: nothing picked). */
	let narrowedFiles = $state<number | null>(null);
	let narrowedAsk = 0;
	$effect(() => {
		const url = page.url;
		const subject = site;
		const ask = (narrowedAsk += 1);
		if (!subject || picksOf(url).length === 0) {
			narrowedFiles = null;
			return;
		}
		void narrowedFilesTotal(url, { sites: site.id }).then((total) => {
			if (ask === narrowedAsk) narrowedFiles = total;
		});
	});

	const tabs = $derived([
		...tabsFor('site', siteId, `/sites/${siteId}`, {
			...counts.current,
			// The filtered figure while picks are in force, the record's own otherwise. See `narrowedFilesTotal`.
			files: narrowedFiles ?? site?.asset_count
		}),
		/* And it wears its number: the strip is a MAP of what this page can show, and one bare
		   word on a row of numbered ones reads as a tab nobody has looked at yet. */
		{
			id: HISTORY,
			label: 'History',
			icon: 'history' as const,
			href: `/sites/${siteId}?show=${HISTORY}`,
			count: counts.current.history,
			/* And a mark where a stash-box disagrees with this record. */
			attention: disagreeing > 0 ? waitingText(disagreeing, counts.boxes) : undefined
		}
	]);
	/* THE USERNAMES ON THIS SITE, filed by the person behind each, for the People tab. */
	/* ONE READ, FILED TWO WAYS. */
	let usernamesAll = $state<readonly Username[]>([]);
	let usernamesAsked = 0;
	const filed = $derived(fileUsernames(usernamesAll));
	const usernamesHere = $derived(filed.held);
	const bareHere = $derived(filed.bare);
	/* The usernames here that belong to nobody yet, drawn as cards after the people. */
	const looseHere = $derived(shown === 'people' ? filed.loose : []);

	async function readUsernames(where: string): Promise<void> {
		const wanted = ++usernamesAsked;
		try {
			const all = await usernames.allOf({ siteId: where });
			if (wanted === usernamesAsked) usernamesAll = all;
		} catch {
			if (wanted === usernamesAsked) usernamesAll = [];
		}
	}

	$effect(() => {
		void libraryChanges.generation;
		const where = siteId;
		if (shown !== 'people' || !where) return;
		untrack(() => void readUsernames(where));
	});

	let busy = $state(false);
	/* Sharing a site shares what came from it, and keeps doing so. */
	let shareOpen = $state(false);
	let reachOpen = $state(false);
	const shareTarget = $derived<ShareTarget | null>(
		site ? { type: 'site', id: site.id, label: site.name } : null
	);

	const tags = $derived(entityTags.items);

	/* Everything the record is made of, in one place: the summary under the name, the panel
	   beside it and the form are three surfaces over the same facts. */
	/* The stash-boxes agreed to know this site. Its own read, from another slice, and optional:
	 * an install with none configured gets an empty list and this page is exactly as it was. */
	let sources = $state<StashBoxLink[]>([]);
	/* Which box MADE this thing, read in the same answer the links come in (see `sourcesOf`). */
	let madeBy = $state<Maker | null>(null);
	let sourcesFor = $state('');
	let lookUpOpen = $state(false);
	let mergeOpen = $state(false);

	/* What this page can do to this Site, behind the one door every entity page wears. */
	/* WHERE THIS ROW STANDS WITH ENRICHMENT, so the two rows that send its name outside are
	   drawn refused rather than refused on the press. */
	const enrichment = new EntityEnrichment('site');
	enrichment.follow();
	const enrichState = $derived(enrichment.of(site?.id));
	const enrichRefused = $derived(enrichState?.refused === true);
	const enrichWhy = $derived(enrichState?.why || undefined);
	const enrichKept = $derived(enrichState?.kept_local === true);
	$effect(() => {
		enrichment.ask(site?.id);
	});
	/* The box list for Auto-enrich's row per box: the same list every wall's flyout reads. */
	$effect(() => {
		if (session.isAdmin) loadEnrichBoxes();
	});
	/* AUTO-ENRICH, THE SAME ROWS EVERY SURFACE DRAWS: every box, then each box by name, and the
	   box handed on. */
	function autoEnrichThis(id: string, box: string = ''): void {
		if (enrichRefused) return void sayKeptLocal();
		void enrichMany('site', [id], box);
	}

	const options = $derived<Verb[]>(
		!session.isAdmin || !site
			? []
			: [
					/* Auto-enrich, Enrich, Do not enrich: the order every wall and file menu uses. */
					{
						id: 'auto-enrich',
						label: 'Auto-enrich',
						icon: 'auto_fix_high' as const,
						...(enrichRefused ? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) } : {}),
						...(enrichBoxes().length > 0 && !enrichRefused
							? {
									children: autoEnrichRows(enrichBoxes(), (_ids, box) =>
										autoEnrichThis(site.id, box)
									)
								}
							: { run: () => autoEnrichThis(site.id) })
					},
					{
						id: 'enrich',
						label: 'Enrich',
						icon: 'backlight_low' as const,
						...(enrichRefused ? { disabled: true, ...(enrichWhy ? { why: enrichWhy } : {}) } : {}),
						run: () => (enrichRefused ? sayKeptLocal() : (lookUpOpen = true))
					},
					/* KEPT LOCAL, beside the two rows it refuses: the pair every other surface
					   draws, on the one page somebody stands on when they decide a Site should
					   never leave. */
					{
						id: 'keep-local',
						label: enrichKept ? 'Allow enrichment' : "Don't enrich",
						icon: enrichKept ? ('public' as const) : ('shield' as const),
						run: () =>
							void setKeptLocal('site', site.id, !enrichKept).then((state) =>
								enrichment.mark([site.id], state)
							)
					},
					{
						id: 'share',
						label: 'Sharing',
						icon: 'group' as const,
						run: () => (shareOpen = true)
					},
					/* What the sharing above it comes to. */
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
					}
				]
	);

	/* Arriving with the chooser already open, because a menu somewhere else asked for it. */
	$effect(() => {
		if (untrack(() => lookUpOpen)) return;
		// Refused before the sheet opens, even where the address asked for it: the chooser's whole
		// subject is a question nothing may ask about a Site kept local.
		if (page.url.searchParams.get('enrich') === '1' && !untrack(() => enrichRefused))
			lookUpOpen = true;
	});

	const recordValues = $derived({
		/* The site's own row first, so anything the record gains is here without a line being
		 * added. */
		...(site?.record ?? {}),
		name: site?.name ?? '',
		/* Every address the site has, from the record. The first IS the site's address; there is
		   one list and one reading of it. */
		links: ((site?.record?.links as string[] | undefined) ?? []).map((url) => ({ url })),
		details: site?.notes ?? '',
		tags,
		sources
	});

	$effect(() => {
		const id = siteId;
		if (!id || sourcesFor === id) return;
		sourcesFor = id;
		void (async () => {
			const held = await sourcesOf('site', id);
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
		if (!siteId) return;
		try {
			subject.value = await sites.one(siteId);
		} catch {
			/* The save has landed; a failed re-read is a stale screen, not a failed save. */
		}
	}

	async function reloadSources() {
		if (!siteId) return;
		const held = await sourcesOf('site', siteId);
		sources = held.links;
		madeBy = held.madeBy;
	}

	$effect(() => {
		const id = siteId;
		entityTags.forget();
		if (id) void entityTags.load('sites', id);
	});

	/* Set the still the site is drawn as, from one of its own files. */
	/** Whether the picture chooser is open. Opened by the pencil on the cover, while editing. */
	let pickingPicture = $state(false);

	async function makeCover(
		assetId: string,
		atMs: number | null = null,
		frame: Frame | null = null
	) {
		try {
			subject.value = await sites.setCover(siteId, assetId, atMs, { frame });
			toasts.show('Cover set', { tone: 'success' });
		} catch {
			toasts.show("That couldn't be used as the cover", { tone: 'error' });
		}
	}

	/* As above. See the person's page for why the refusal is left to the sheet. */
	async function uploadCover(file: File) {
		subject.value = await sites.uploadCover(siteId, file);
		toasts.show('Cover set', { tone: 'success' });
	}

	/** The id the header's Save submits. One form is open at a time, so one name is enough. */
	const RECORD_FORM = 'site-record-form';

	/* Deleting the site. The FILES that came from it are untouched: what goes is the site, what
	 * was recorded about it, and the rows joining it to files. */
	async function removeSite() {
		await sites.remove(siteId);
		toasts.show('Deleted. The files that came from it are still here.', { tone: 'success' });
		await leaveFor('/sites');
	}

	async function heart(favorite: boolean) {
		try {
			await sites.setFavorite(siteId, favorite);
			subject.value = subject.value ? { ...subject.value, favorite } : subject.value;
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	async function rate(rating: number | null) {
		try {
			await sites.setRating(siteId, rating);
			subject.value = subject.value ? { ...subject.value, rating } : subject.value;
		} catch {
			toasts.show("That couldn't be saved", { tone: 'error' });
		}
	}

	/* Put a tag on. Admin-only, like every other write to shared vocabulary: a rating is an
	 * opinion somebody holds privately, a tag changes what everybody else's searches return. */
	async function addTag(tag: { id: string }) {
		try {
			await entityTags.set('sites', siteId, tag.id, true);
		} catch {
			toasts.show("That tag couldn't be added", { tone: 'error' });
		}
	}

	async function untag(tagId: string) {
		try {
			await entityTags.set('sites', siteId, tagId, false);
		} catch {
			toasts.show("That tag couldn't be removed", { tone: 'error' });
		}
	}

	function countsFor(): string {
		if (!site) return '';
		const files = filesSized(site.asset_count, sizeOf(site));
		/* This account's own O tally over everything the site reaches goes beside this line as
		   the header's own drop and figure (`oCount`), not in its words. */
		return joinCounts(files, peopleSaid(site.people_count));
	}

	/* The trail, handed to whichever grid is showing and drawn by the frame's band above the
	   identity band: the same trail on every tab of this page. */
	const crumbs = $derived<Crumb[]>([
		{ label: 'Sites', href: '/sites' },
		{ label: site?.name ?? '' }
	]);
</script>

<svelte:head><title>{site?.name ?? 'Site'}</title></svelte:head>

<!-- A link dropped anywhere on this page is fetched and filed under it, exactly as one
     dropped on its card on the wall is. Only while there IS one: a page still settling
     has no id to aim at. See `EntityDropZone`. -->
{#if site}
	<EntityDropZone kind="site" id={site.id} name={site.name} />
{/if}

<!-- ONE frame and one scrolling region: the grid's. -->
{#if subject.settling}
	<Skeleton lines={3} />
{:else if subject.unreadable}
	<Problem message="That Site couldn't be loaded. It's still there — try again in a moment." />
{:else if !site}
	<Empty scope="page" icon="public" title="That Site isn't here"
		>It may have been deleted, or merged into another Site.</Empty
	>
{:else}
	<!-- Its media, which is what the page is about. -->
	<!-- Whose page this is, drawn on EVERY tab: what is being shown OF a site changes, and which
	     site it is does not. -->
	{#snippet identity()}
		<EntityHeader
			{options}
			optionIds={[siteId]}
			bind:editing
			icon={iconOf('site')}
			kind="site"
			mayEdit={session.isAdmin}
			saveForm={editing ? RECORD_FORM : undefined}
			deleteWord="Site"
			ondelete={session.isAdmin ? removeSite : undefined}
			name={site.name}
			sitesWithin={site.counts?.sites_within ?? 0}
			siteName={site.name}
			siteIcon={site.icon}
			onpicture={() => (pickingPicture = true)}
			oncover={async (assetId, atMs, more) => {
				subject.value = await sites.setCover(siteId, assetId, atMs, more);
			}}
			coverHref={`/sites/${site.id}`}
			coverAssetId={site.cover_asset_id}
			coverUploadId={site.cover_upload_id}
			coverAtMs={site.cover_at_ms}
			coverFrame={site.cover_frame}
			art={site.art}
			counts={countsFor()}
			oCount={site.o_count}
			favorite={site.favorite}
			rating={site.rating ?? null}
			enrichedBy={sources.map((one) => ({ name: one.box_name, box: one.box_slug }))}
			{madeBy}
			{tags}
			onfavorite={(next) => heart(next)}
			onrate={(next) => rate(next)}
			onuntag={session.isAdmin ? (tagId) => untag(tagId) : undefined}
			ontag={session.isAdmin ? (tag) => addTag(tag) : undefined}
		>
			{#snippet summary()}
				<RecordSummary subject="site" label="About {site.name}" values={recordValues} />
			{/snippet}

			{#snippet facts()}
				<RecordFacts
					subject="site"
					label="Facts about {site.name}"
					values={recordValues}
					given={givenBy(sources)}
				/>
			{/snippet}

			{#snippet record()}
				<!-- Which entry in each stash-box this is: under More, with the rest of the record, and
				     first there, on the facts' columns. Read by every viewer; Remove is an admin's. See
				     `StashBoxIds`. -->
				<StashBoxIds
					subject="site"
					id={site.id}
					name={site.name}
					links={sources}
					mayForget={session.isAdmin}
					onforgot={reloadSources}
				/>
				<RecordView
					subject="site"
					label="More about {site.name}"
					values={recordValues}
					given={givenBy(sources)}
				/>
			{/snippet}
		</EntityHeader>
	{/snippet}

	{#snippet tabStrip()}
		<Tabs
			tabs={tabsCarryingPicks(tabs, page.url)}
			current={showingHistory ? HISTORY : shown}
			label="What to show for {site.name}"
		/>
		<!-- The cards picked on the tabs, counted, and the press that filters the Files tab to
		     them. Draws nothing while nothing is picked. See `picks.ts`. -->
		<PickedFilter />
	{/snippet}

	{#snippet editing_form()}
		<RecordForm
			subject="site"
			formId={RECORD_FORM}
			label="Editing {site.name}"
			values={{
				...recordValues,
				links: (site.record?.links as string[] | undefined) ?? []
			}}
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
		<!-- The thread, in the frame every other screen uses. -->
		<!-- History draws the identity and the tab strip in the shape every other tab does
		     (`PageAbove`, then the strip as the heading row), so the strip stands at one height
		     on every tab. -->
		<PageFrame {crumbs}>
			{#snippet header()}
				<PageAbove>{@render identity()}</PageAbove>
				<PageHeader title="History" icon="history" level={2} titleHidden beside={tabStrip} />
			{/snippet}
			{#snippet children()}
				<EntityHistory subject="site" id={siteId} name={site.name}>
					{#snippet waiting()}
						<!--
							Where a stash-box disagrees with THIS record, at the top of the thread
							rather than in the header.
						-->
						{#if session.isAdmin}
							<Disagreements
								subject="site"
								localId={site.id}
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
					<AssetGrid
						oncount={arrived}
						query={{ sites: site.id }}
						title="Files"
						titleLevel={2}
						beside={tabStrip}
						titleHidden
						above={identity}
						{crumbs}
						empty={emptyWallSays(
							'files',
							fileWords.asked,
							false,
							'Nothing has come from this Site yet.'
						)}
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
								<ContextMenuItem
									label={site?.cover_asset_id === item.id
										? 'This is the cover'
										: 'Use as the cover'}
									icon="star"
									disabled={site?.cover_asset_id === item.id}
									onselect={() => void makeCover(item.id)}
								/>
							{/if}
						{/snippet}
					</AssetGrid>
				{:else}
					<RelatedWall
						on="site"
						id={siteId}
						named={site.name}
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
						trailing={looseHere.length}
					>
						{#snippet after()}
							{#each looseHere as one (one.id)}
								<!-- Told to the whole library: the person it joins now belongs on this wall. -->
								<UsernameCard
									{one}
									detail={withSize(
										`${filesSaid(one.asset_count)} from this Site`,
										one.asset_count,
										sizeOf(one)
									)}
									onjoined={() => libraryChanges.changed()}
								/>
							{/each}
						{/snippet}
						{#snippet under(row, drawn)}
							{#if drawn === 'people'}
								<UsernameLines
									usernames={usernamesHere.get(row.id) ?? []}
									onchanged={() => void readUsernames(siteId)}
								/>
								{#if !usernamesHere.has(row.id)}
									<!--
										Somebody on this wall only by a username with nothing under it:
										said as that, so the card's "0 files" has its reason beside it.
									-->
									{#each bareHere.get(row.id) ?? [] as one (one.id)}
										<p class="bare">{bareLine(one)}</p>
									{/each}
								{/if}
							{/if}
						{/snippet}
					</RelatedWall>
				{/if}
			{/snippet}
		</TabHold>
	{/if}
{/if}

<ShareDialog bind:open={shareOpen} targets={shareTarget ? [shareTarget] : []} />
<VisibilityDialog bind:open={reachOpen} target={shareTarget} />

<!-- No button of its own: the row in the Options menu opens it. -->
{#if site}
	<MergeEntities
		people={[{ id: site.id, name: site.name }]}
		kind="site"
		withButton={false}
		bind:open={mergeOpen}
		onmerged={(into) => goto(`/sites/${into}`)}
	/>
{/if}

<!-- The same sheet the person's page uses, with a different subject. -->
{#if site}
	<LinkToStashBox
		bind:open={lookUpOpen}
		subject="site"
		id={site.id}
		name={site.name}
		values={recordValues}
		onlinked={afterLinked}
	/>

	<!-- The pencil's sheet, same as the person's page. -->
	<PickPicture
		bind:open={pickingPicture}
		name={site.name}
		query={{ sites: site.id }}
		current={site.cover_asset_id}
		onpick={makeCover}
		onupload={uploadCover}
	/>
{/if}

<style>
	/* A username with nothing filed under it, under a card on the People tab. */
	.bare {
		margin: 0;
		font: var(--text-label);
		color: var(--sift-ink-3);
	}
</style>
