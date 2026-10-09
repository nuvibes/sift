<script lang="ts">
	import { phoneWidth } from '$lib/components/common/phone-width.svelte';
	import { counted } from '$lib/entity/entity-counts';
	import { onMount, tick } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { barShape, type Verb } from '$lib/components/common/verbs';
	import {
		ActionBar,
		VerbButtons,
		VerbMore,
		Button,
		ConfirmDialog,
		DataRows,
		Empty,
		NarrowBox,
		Problem
	} from '$lib/components/common';
	import Pager from '$lib/components/common/Pager.svelte';
	import Tabs from '$lib/components/common/Tabs.svelte';
	import { Selection } from '$lib/components/common/selection.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { noteFolderUse } from '$lib/shell/interface-state.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import CookiesSheet from '$lib/components/downloads/CookiesSheet.svelte';
	import { imports } from '$lib/library/imports.svelte';
	import { downloadChanges, settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import DownloadRow, {
		DOWNLOAD_ACTIONS,
		PHONE_ACTIONS,
		downloadColumns
	} from './DownloadRow.svelte';
	import PasteBox from './PasteBox.svelte';
	import DownloadOptions from './DownloadOptions.svelte';
	import { openSettings } from '$lib/settings-ui/settings-view';
	import { openAsset } from '$lib/player/asset-view';
	import { api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { Connections } from '$lib/settings-ui/connections-state.svelte';
	import { fetchSettings, saveSettings } from '$lib/settings-ui/settings';
	import { Changes, Sounds } from './sounds.svelte';
	import { FACTS_UNDER_THE_NAME, Narrow } from './narrow.svelte';
	import { forTheScreen, holdsALink, intoBox } from './paste';
	import {
		DownloadQueue,
		FILTERS,
		PAGE,
		SORTS,
		canCancel,
		canGoFirst,
		canPause,
		canRemove,
		canResume,
		canRetry,
		lines,
		queueSortFrom,
		type BulkPreview,
		type DownloadItem,
		type PasteChoices,
		type QueueFilter,
		type QueueSort
	} from './queue.svelte';

	/* The download queue: what Sift is fetching from the internet, and where you start a fetch. */

	const queue = new DownloadQueue();
	const sounds = new Sounds();
	const changes = new Changes();
	const connections = new Connections();
	const selection = new Selection();
	/* Whether this window is too small for the wide arrangement. */
	const narrow = new Narrow();
	/* And whether a row has room for its facts beside its name. */
	const stacked = new Narrow(FACTS_UNDER_THE_NAME);

	type SupportedSite = components['schemas']['SupportedSite'];

	let url = $state('');
	/* Where the next download goes: '' for the default folder, else a folder id. */
	let dest = $state('');
	/* The paste's own answer, chosen beside the folder. */
	let remember = $state<boolean | null>(null);
	/** Whether the page's Options menu is open: the paste box opens it when a paste has nowhere to go. */
	let optionsOpen = $state(false);
	/** Everything the page decided for the next paste, in the shape the queue sends. */
	const choices = $derived<PasteChoices>({ dest, remember });
	let now = $state(Math.floor(Date.now() / 1000));
	/** What the pasted address turned out to hold, once asked. Nothing is queued by asking. */
	let found = $state<BulkPreview | null>(null);
	/** Every site Sift has a record for. Read once: it is a fact about what Sift ships. */
	let sites = $state<SupportedSite[]>([]);
	/** Which row is open, so only one detail is expanded at a time. */
	let expanded = $state<string | null>(null);

	/* The queue's pause, which the head's Pause button draws. */
	const PAUSED_KEY = 'download.paused';
	let paused = $state(false);

	/* The cookies sheet, and which Site it opens on. */
	let cookiesOpen = $state(false);
	let cookiesSite = $state<string | null>(null);
	/* And which download is waiting on them, when the sheet was opened from a row. */
	let cookiesRetry = $state<string | null>(null);

	/** Asking before several downloads are stopped together. One row asks for itself. */
	let confirmCancel = $state(false);

	/* Read when the screen opens, and again whenever the connection says the queue moved, which
	   includes a transfer simply getting further along, since that is announced from where it is
	   known rather than written down and noticed. */
	whenChanged(downloadChanges, () => void queue.refresh());

	/* And the preferences this screen reports, whenever one moves: the sounds, and the three the
	   choices line says out loud. */
	whenChanged(settingChanges, () => {
		void sounds.load();
		void readSettings();
	});

	onMount(() => {
		void queue.refresh();
		void sounds.load();
		void readSettings();
		void readSites();
		void connections.load();
		// Opening the screen is seeing the outcome, so the rail's status dot goes out.
		imports.clearDownloadStatus();
		const clock = setInterval(() => (now = Math.floor(Date.now() / 1000)), 1000);
		return () => clearInterval(clock);
	});

	// A sound belongs to a transition, never to a state: the list is re-read whenever the queue
	// moves, and anything keyed on "is finished" would chirp for every one of those.
	$effect(() => {
		for (const landing of changes.since(queue.items)) sounds.play(landing.kind);
	});

	/** Whether the queue is paused, for the head's Pause button. */
	async function readSettings() {
		try {
			const inDownloads = (await fetchSettings()).find((one) => one.name === 'Downloads');
			const entry = inDownloads?.settings?.find((one) => one.key === PAUSED_KEY);
			if (entry && typeof entry.value === 'boolean') paused = entry.value;
		} catch {
			// The button shows the default. Nothing else on the screen depends on it.
		}
	}

	/** Put back on failure, so the button never shows a state the server did not accept. */
	async function setPaused(on: boolean) {
		const before = paused;
		paused = on;
		try {
			await saveSettings({ [PAUSED_KEY]: on });
		} catch {
			paused = before;
		}
	}

	/* WHAT IS SAID OUT LOUD about the queue as a whole, at milestones. */
	let announced = $state('');
	$effect(() => {
		const running = queue.summary?.running ?? 0;
		const queued = queue.summary?.queued ?? 0;
		const milestone = queue.total > 0 ? `${running} downloading, ${queued} waiting` : '';
		if (milestone !== announced) announced = milestone;
	});

	async function readSites() {
		try {
			sites = await api.get<SupportedSite[]>('/supported-sites');
		} catch {
			// The pills and the sentence in the empty state are both absent rather than wrong.
		}
	}

	/* Addresses that hold a set of things rather than one thing. */
	const A_SET_OF_THINGS =
		/[?&]list=|\/playlist\b|\/channel\/|\/c\/|\/@[^/]+\/?$|\/user\/|\/users\/|\/profile\/|\/videos\/?$/i;

	async function submit() {
		const pasted = lines(url);
		// More than one line is a different question with a different answer (how many went in and
		// which did not), so it goes to the route that can say, rather than round this one N times.
		if (pasted.length > 1) {
			const answer = await queue.submitMany(pasted, choices);
			if (answer) {
				landedIn();
				// What is left in the box is what still needs doing, and nothing else.
				url = [...answer.refused.map((one) => one.url), ...answer.left_over].join('\n');
				found = null;
			}
			return;
		}
		const link = url.trim();
		if (!link) return;
		if (A_SET_OF_THINGS.test(link) && found === null) {
			// Asking fetches no media and queues nothing.
			found = (await queue.preview(link, choices)) ?? null;
			if (found) return;
		}
		await justOne();
	}

	/** Queue the one link that was pasted, whatever else is behind the address. */
	async function justOne() {
		const link = url.trim();
		if (!link) return;
		if (await queue.submit(link, choices)) return; // the refusal is shown by the queue
		landedIn();
		url = '';
		found = null;
	}

	/* A download that NAMED a folder is remembered as that folder's use, which is what puts it
	   at the top of the chooser next time, here and in Add, which read the same record. */
	function landedIn() {
		noteFolderUse(dest);
	}

	/** Go ahead. Each item is queued as its own download, so one failure is one video. */
	async function takeThemAll() {
		const link = url.trim();
		if (!link) return;
		// The refusal is shown by the queue, not put there from here.
		if (await queue.queueAll(link, choices)) return;
		landedIn();
		url = '';
		found = null;
	}

	/* How many Sites are asking for something. Two halves, and both are about the same act. */
	const expiredCookies = $derived(
		connections.items.filter((one) => one.state === 'expired').length
	);
	/* Over the whole queue, from the server's counts: the page holds fifty of however many, and
	   a blocked row on page three is still a Site waiting on somebody. */
	const waitingOnCookies = $derived(
		queue.summary?.by_state?.blocked ?? queue.items.filter((one) => one.status === 'blocked').length
	);
	const cookiesWanted = $derived(expiredCookies + waitingOnCookies);

	function openCookies(site: string | null, retry: string | null = null) {
		cookiesSite = site;
		cookiesRetry = retry;
		cookiesOpen = true;
	}

	/** The sentence in the empty state names the five people have heard of and counts the rest. */
	const FAMILIAR = ['tiktok', 'instagram', 'youtube', 'x', 'reddit'];
	const familiar = $derived(
		FAMILIAR.map((key) => sites.find((site) => site.key === key)).filter(
			(site): site is SupportedSite => site !== undefined
		)
	);
	const alsoWorks = $derived(sites.length - familiar.length);

	/* What is picked, as rows rather than as ids: every verb below asks what the rows can take. */
	const picked = $derived(queue.items.filter((item) => selection.has(item.id)));
	/* THE BAR OFFERS ONLY WHAT EVERY PICKED ROW CAN TAKE. */
	const everyFirst = $derived(picked.length > 0 && picked.every(canGoFirst));
	const everyCancel = $derived(picked.length > 0 && picked.every(canCancel));
	const everyRemove = $derived(picked.length > 0 && picked.every(canRemove));
	const everyRetry = $derived(picked.length > 0 && picked.every(canRetry));
	/* Held and resumed over a pick, on the same rule as every other verb here: offered only
	   where every picked row can take it. */
	const everyPause = $derived(picked.length > 0 && picked.every(canPause));
	const everyResume = $derived(picked.length > 0 && picked.every(canResume));
	/* Whether stopping these throws bytes away. A waiting row has fetched nothing, so asking
	   about it would be a dialog whose answer is always yes. */
	const anyRunning = $derived(picked.some((item) => item.status === 'running'));

	/* THE BAR'S VERBS, AS ONE LIST THE SHARED SPLIT DIVIDES. */
	const barVerbs = $derived.by((): Verb[] => {
		const built: Verb[] = [];
		if (everyPause) {
			built.push({
				id: 'pause',
				label: 'Pause',
				icon: 'pause',
				group: 'change',
				primary: true,
				run: () => void overPicked((id) => queue.pause(id))
			});
		}
		if (everyResume) {
			built.push({
				id: 'resume',
				label: 'Resume',
				icon: 'play_arrow',
				group: 'change',
				primary: true,
				run: () => void overPicked((id) => queue.resume(id))
			});
		}
		if (everyRetry) {
			built.push({
				id: 'retry',
				label: 'Try again',
				icon: 'sync',
				group: 'change',
				primary: true,
				run: () => void overPicked((id) => queue.retry(id))
			});
		}
		if (everyFirst) {
			built.push({
				id: 'first',
				label: 'Move to the front',
				icon: 'arrow_upward',
				group: 'change',
				run: () => void overPicked((id) => queue.first(id))
			});
		}
		if (everyRemove) {
			built.push({
				id: 'remove',
				label: 'Remove from the list',
				icon: 'delete',
				group: 'change',
				run: () => void removeRows()
			});
		}
		if (everyCancel) {
			built.push({
				id: 'cancel',
				label: 'Cancel',
				icon: 'close',
				destructive: true,
				run: () => {
					if (anyRunning) confirmCancel = true;
					else void overPicked((id) => queue.cancel(id));
				}
			});
		}
		return built;
	});
	const shape = $derived(barShape(barVerbs));
	const pickedIds = $derived(picked.map((item) => item.id));

	async function overPicked(act: (id: string) => Promise<void>) {
		const ids = picked.map((item) => item.id);
		selection.clear();
		for (const id of ids) await act(id);
	}

	/* Taking rows off the list: the act, the sentence and the way back are one thing in the
	 * queue, because the row's own verb and the bar below both end there, and two copies of the
	 * sentence would be two sentences for one act. */
	async function removeOne(id: string) {
		await queue.removeRows([id]);
	}

	/** The same, over everything picked. */
	async function removeRows() {
		const ids = picked.map((item) => item.id);
		selection.clear();
		await queue.removeRows(ids);
	}

	/* Escape, one layer at a time. What Escape means is "put back the last thing I filtered",
	 * and the layers are in the order they were put on: the pick, then the chip, then the
	 * search. */
	function onKeydown(event: KeyboardEvent) {
		if (event.key !== 'Escape') return;
		/* A panel or menu of the bar's is open: this Escape is ITS (it shuts the Filter panel or
		   the order menu), and taking a layer off the list as well would be one press doing two
		   things. */
		if (screenBar.open !== null || event.defaultPrevented) return;
		if (!selection.isEmpty) selection.clear();
		else if (queue.filter !== 'all') void goto(addressFor({ show: 'all' }), QUIETLY);
		else if (queue.search) searchFor('');
		else return;
		event.preventDefault();
	}

	/* A PASTE ANYWHERE ON THIS SCREEN. It fills the box and puts the cursor in it, and it QUEUES
	 * NOTHING: pasting is not agreeing. */
	let box = $state<{ focus: () => void } | undefined>();

	function onPaste(event: ClipboardEvent) {
		if (!forTheScreen(event.target)) return;
		const text = event.clipboardData?.getData('text') ?? '';
		if (!holdsALink(text)) return;
		event.preventDefault();
		url = intoBox(url, text);
		found = null;
		box?.focus();
	}

	/* BEING SENT HERE TO LOOK AT ONE ROW. */
	let pointedAt: string | null = null;

	$effect(() => {
		// `?row=` names the download; `?job=` names the job that runs it, which is what the
		// Activity screen knows.
		const wanted = page.url.searchParams.get('row') ?? page.url.searchParams.get('job');
		if (!wanted || wanted === pointedAt) return;
		pointedAt = wanted;
		void showRow(wanted);
	});

	async function showRow(pointer: string) {
		const isIt = (item: { id: string; job_id: string | null }) =>
			item.id === pointer || item.job_id === pointer;
		/* Everything that could hide the row is put back BEFORE the read, and in the queue
		   itself rather than by way of the address: the address effect below runs after this
		   one, so a read asked here with the old tab still set would be answered for the wrong
		   tab. */
		if (
			!queue.items.some(isIt) ||
			queue.filter !== 'all' ||
			queue.search ||
			queue.siteNames.length > 0
		) {
			queue.filter = 'all';
			queue.search = '';
			queue.siteNames = [];
			queue.offset = 0;
			typed = '';
			await queue.refresh();
		}
		const found = queue.items.find(isIt);
		if (!found) return;
		const id = found.id;
		selection.clear();
		selection.toggle(id);
		// After the list has been drawn with the row in it, or there is nothing to scroll to.
		await tick();
		const at = queue.shown.findIndex((item) => item.id === id);
		const list = document.querySelector('ul[aria-label="Downloads"]');
		const row = at >= 0 ? list?.children[at] : undefined;
		// A guard rather than a call: a list item always has this, and the unit environment does not.
		if (row instanceof HTMLElement && typeof row.scrollIntoView === 'function') {
			row.scrollIntoView({ block: 'center' });
		}
	}

	/* THE TAB, THE SITES AND THE ORDER LIVE IN THE ADDRESS. */
	const QUIETLY = { replaceState: true, keepFocus: true, noScroll: true } as const;

	function readShow(value: string | null): QueueFilter {
		return FILTERS.find((one) => one === value) ?? 'all';
	}

	const wantedShow = $derived(readShow(page.url.searchParams.get('show')));
	/* Every `site` in the address, because the filter panel's Site column writes one per tick
	   and the server reads them as either. */
	const wantedSites = $derived(
		page.url.searchParams.getAll('site').filter((one) => one.trim() !== '')
	);
	const wantedSitesKey = $derived(JSON.stringify(wantedSites));
	const wantedSort = $derived(queueSortFrom(page.url.searchParams.get('sort')));

	/** This screen's address with some of what it says changed. Anything left at rest is left out. */
	function addressFor(changes: Partial<{ show: QueueFilter; sort: QueueSort }>): string {
		const query = new URLSearchParams(page.url.searchParams);
		// A pointer at one row is honoured once and is not part of what the list is filtered to.
		query.delete('row');
		query.delete('job');
		const show = changes.show ?? wantedShow;
		const sort = changes.sort ?? wantedSort;
		if (show !== 'all') query.set('show', show);
		else query.delete('show');
		if (sort !== 'newest') query.set('sort', sort);
		else query.delete('sort');
		const text = query.toString();
		return text ? `/downloads?${text}` : '/downloads';
	}

	/* The address moved: filter the queue to what it says. */
	$effect(() => {
		const show = wantedShow;
		const sites: string[] = JSON.parse(wantedSitesKey);
		const sort = wantedSort;
		if (
			show === queue.filter &&
			wantedSitesKey === JSON.stringify(queue.siteNames) &&
			sort === queue.sort
		) {
			return;
		}
		selection.clear();
		void queue.narrow({ filter: show, siteNames: sites, sort });
	});

	/** One tab per state, each a real address carrying the Sites and the order across. */
	const tabs = $derived(queue.tabs.map((tab) => ({ ...tab, href: addressFor({ show: tab.id }) })));

	/* THE SITE AND THE ORDER ARE THE BAR'S, like every other wall's. */
	const barOwner = Symbol('downloads');

	$effect(() => {
		screenBar.publish(barOwner, {
			filterable: true,
			subject: 'download',
			count: queue.matched,
			sorts: SORTS,
			sort: wantedSort,
			onSort: (next) => void goto(addressFor({ sort: queueSortFrom(next) }), QUIETLY)
		});
	});

	$effect(() => () => screenBar.release(barOwner));

	/* The search, sent a moment after the typing stops rather than per letter: each read is a
	   query over the whole ledger, and a word typed at speed is eight of them for one answer. */
	let typed = $state('');
	let typing: ReturnType<typeof setTimeout> | undefined;
	function searchFor(text: string) {
		typed = text;
		clearTimeout(typing);
		typing = setTimeout(() => void queue.narrow({ search: text }), text ? 250 : 0);
	}

	/** Where each turn of the pager lands, as an offset into what the filtering keeps. */
	const lastPage = $derived(Math.max(0, Math.ceil(queue.matched / PAGE) - 1) * PAGE);

	/** What the list says when the tab, the Sites and the search between them leave nothing. */
	const nothingMatches = $derived(
		queue.search
			? 'Nothing matches that search.'
			: queue.siteNames.length === 1
				? `Nothing from ${queue.siteNames[0]} is in that state right now.`
				: queue.siteNames.length > 1
					? 'Nothing from those Sites is in that state right now.'
					: 'Nothing in the queue is in that state right now.'
	);
</script>

<svelte:window onkeydown={onKeydown} onpaste={onPaste} />

<PageFrame footer={queue.total > 0 ? pagerFooter : undefined}>
	{#snippet header()}
		<PageHeader title="Downloads" icon="download" count={queue.inSites}>
			{#snippet controls()}
				<!-- One box around the two, which is nothing on a wide window (`display: contents`, so
				     the header lays them as it always did) and on a phone one line under the title. -->
				<div class="head-acts" class:phone={phoneWidth.yes}>
					<!-- The page's Options, the door every entity page wears: the page's three doors
					     (Edit cookies, Open settings, Start or join a swap) and the paste's two
					     choices (the switch and Download folder) are its rows, so the paste box
					     and the queue start right under the title. -->
					<DownloadOptions
						bind:open={optionsOpen}
						bind:dest
						bind:remember
						{cookiesWanted}
						oncookies={() => openCookies(null)}
					/>
					{#if queue.total > 0 || paused}
						<!-- The queue's one verb, after the Options. The tip says what pausing does: a
					     transfer cannot be picked up halfway, so what is running finishes and only
					     what has not started is held. -->
						<span class="act-slot">
							<Tooltip
								label={paused
									? 'Paused \u2014 what is running finishes, nothing new starts'
									: "Hold back anything that hasn't started; what is running finishes"}
							>
								<Button
									icon={paused ? 'play_arrow' : 'pause'}
									onclick={() => void setPaused(!paused)}
								>
									{paused ? 'Resume the queue' : 'Pause the queue'}
								</Button>
							</Tooltip>
						</span>
					{/if}
				</div>
			{/snippet}
		</PageHeader>
	{/snippet}

	<PasteBox
		bind:this={box}
		bind:value={url}
		{dest}
		onask={() => (optionsOpen = true)}
		busy={queue.busy}
		error={queue.submitError}
		{sites}
		{found}
		narrow={narrow.yes}
		onsubmit={() => void submit()}
		onall={() => void takeThemAll()}
		onjustone={() => void justOne()}
	/>

	<Problem message={queue.problem} />

	<!-- The one live region on the screen: the queue's milestones, read aloud and never drawn. -->
	<p class="announcer" role="status" aria-live="polite">{announced}</p>

	{#if queue.total > 0}
		<!-- The state tabs, the strip every entity page and Organize draw (each a real address, so
		     the tab showing survives a reload), then the search, which filters inside a tab. The Site
		     and the order are on the bar across the top, as on every other wall. -->
		<div class="narrowing">
			<Tabs {tabs} current={queue.filter} label="Which downloads to show" />
			<NarrowBox
				class="find"
				value={typed}
				oninput={(event: Event) => searchFor((event.currentTarget as HTMLInputElement).value)}
				placeholder="Search downloads"
				label="Search downloads"
			/>
		</div>
	{/if}

	{#if queue.total === 0}
		<Empty scope="page" icon="download" title="Nothing downloading">
			Paste a link to download it. Sift finds who posted it and adds the file to their Username.
			{#if familiar.length > 0 && alsoWorks > 0}
				<br />
				Works with {familiar.map((site) => site.name).join(', ')} and {counted(alsoWorks)} more.
			{/if}
			{#snippet action()}
				{#if sites.length > 0}
					<Button onclick={() => openSettings('sites', 'sites.supported')}>Show all Sites</Button>
				{/if}
			{/snippet}
		</Empty>
	{:else if queue.shown.length === 0}
		<Empty icon="history" scope="block">{nothingMatches}</Empty>
	{:else}
		<!-- The list declares its columns once and every row obeys them. -->
		{#key `${stacked.yes}:${narrow.yes}`}
			<DataRows
				items={queue.shown}
				key={(item: DownloadItem) => item.id}
				label="Downloads"
				columns={downloadColumns(stacked.yes)}
				actions={narrow.yes ? PHONE_ACTIONS : DOWNLOAD_ACTIONS}
				folds
			>
				{#snippet row(item: DownloadItem)}
					<DownloadRow
						{item}
						{now}
						selected={selection.has(item.id)}
						onselect={(id: string, on: boolean, event?: MouseEvent) =>
							event
								? selection.pick(
										id,
										event,
										queue.shown.map((one) => one.id)
									)
								: selection.toggle(id)}
						expanded={expanded === item.id}
						onexpand={(id: string) => (expanded = expanded === id ? null : id)}
						oncancel={(id) => queue.cancel(id)}
						onpause={(id: string) => void queue.pause(id)}
						onresume={(id: string) => void queue.resume(id)}
						onretry={(id) => queue.retry(id)}
						onfirst={(id) => queue.first(id)}
						onanyway={(id) => queue.anyway(id)}
						onremove={(id: string) => void removeOne(id)}
						onopen={(assetId: string) => openAsset(assetId)}
						oncookies={(site: string) => openCookies(site, item.id)}
						narrow={stacked.yes}
						phone={narrow.yes}
					/>
				{/snippet}
			</DataRows>
		{/key}
	{/if}

	{#if !selection.isEmpty}
		<ActionBar count={selection.count} noun="download" onclear={() => selection.clear()}>
			{#snippet actions()}
				<VerbButtons ids={pickedIds} verbs={shape.named} />
			{/snippet}
			{#snippet overflow()}
				<VerbMore ids={pickedIds} verbs={shape.rest} noun="download" />
			{/snippet}
		</ActionBar>
	{/if}

	<ConfirmDialog
		bind:open={confirmCancel}
		title={picked.length === 1
			? 'Cancel this download?'
			: `Cancel ${counted(picked.length)} downloads?`}
		consequence="What has downloaded so far is deleted. The link stays in the list, so you can try again."
		confirmLabel={picked.length === 1 ? 'Cancel download' : 'Cancel downloads'}
		cancelLabel="Keep downloading"
		onconfirm={() => void overPicked((id) => queue.cancel(id))}
	/>

	<!-- One sheet, three doors: this screen's head, a row that is waiting for cookies, and the
	     Sites pane in Settings. Opened with no Site it is the whole list. -->
	<CookiesSheet
		bind:open={cookiesOpen}
		site={cookiesSite}
		retry={cookiesRetry}
		onsaved={() => {
			void connections.load();
			void queue.refresh();
		}}
	/>
</PageFrame>

<!-- Always, once there is a queue, as Browse does: it says WHERE YOU ARE as well as how to move,
     and "1-50 of 700" is worth saying. In the frame's foot, the one place every screen keeps it. -->
{#snippet pagerFooter()}
	<Pager
		offset={queue.offset}
		shown={queue.shown.length}
		total={queue.matched}
		noun="downloads"
		onfirst={() => void queue.turn(0)}
		onprevious={() => void queue.turn(queue.offset - PAGE)}
		onnext={() => void queue.turn(queue.offset + PAGE)}
		onlast={() => void queue.turn(lastPage)}
		onjump={(position: number) => void queue.turn(position - 1)}
	/>
{/snippet}

<style>
	.head-acts {
		display: contents;
	}

	/* A class from the one phone-width reader, never a media query: this screen follows a width
	   declared in script (`narrow.svelte.ts`), and a width written here would be a second copy. */
	.head-acts.phone {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
		inline-size: 100%;
	}

	.act-slot {
		display: contents;
	}

	/* Read aloud, never looked at. Not `display: none`, which would take it out of the
	   accessibility tree along with everything it is meant to announce. */
	.announcer {
		position: absolute;
		inline-size: 1px;
		block-size: 1px;
		margin: -1px;
		padding: 0;
		overflow: hidden;
		clip-path: inset(50%);
		white-space: nowrap;
	}

	/* The tabs and the search, and what gives way when there is no room for both. */
	.narrowing {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: var(--space-4);
		margin-block: var(--space-4) var(--space-3);
	}

	/* How wide this one may grow, and nothing else. */
	.narrowing :global(.find) {
		inline-size: min(220px, 100%);
	}
</style>
