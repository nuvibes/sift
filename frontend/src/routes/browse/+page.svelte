<script lang="ts">
	/* WHY NO HOVER: the one hover rule here dresses the SHARED button, whose motion is its own: it
	   transitions its background over --dur-instant for every tone. What this file changes is which
	   shade the step lands on, not whether there is a step, and a second transition written here
	   would be one rule kept in two files. */
	/* Browse: the whole library, newest first. Everything that makes a grid a grid lives in the
	 * component (see `$lib/components/AssetGrid`). */
	import { onMount } from 'svelte';
	import { ANCHOR } from '$lib/grid/anchor';
	import { bareWord, cleared, filteredBy, questionIn } from './question';
	import { clearWallSays, emptyWallSays } from '$lib/components/shell/wall-words';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import AssetGrid from '$lib/components/AssetGrid.svelte';
	import FolderExplorer, { type FolderView } from '$lib/library/FolderExplorer.svelte';
	import FolderGround from '$lib/library/FolderGround.svelte';
	import { directContents } from '$lib/library/direct';
	import { Button, SettingLink, SplitButton } from '$lib/components/common';
	import { fetchSettings } from '$lib/settings-ui/settings';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { pressTask, type At } from '$lib/jobs/tasks.svelte';
	import type { Crumb } from '$lib/components/common/Breadcrumbs.svelte';
	import EntityBand from '$lib/components/EntityBand.svelte';
	import RecapLine from '$lib/components/insights/RecapLine.svelte';
	import PageFrame from '$lib/components/shell/PageFrame.svelte';
	import PageHeader from '$lib/components/shell/PageHeader.svelte';
	import { session } from '$lib/shell/session.svelte';
	import { Library } from '$lib/library/library.svelte';
	import { benchmarkRun } from '$lib/shell/toasts-benchmark.svelte';
	import AddFolder from '$lib/library/AddFolder.svelte';
	import DownloadFolderOffer from '$lib/library/DownloadFolderOffer.svelte';
	import { Grants } from '$lib/library/grants-state.svelte';
	import { Picker } from '$lib/library/picker.svelte';
	import { screenBar } from '$lib/components/shell/screen-bar.svelte';
	import { FOLDER_ORDERS, FOLDERS_DEFAULT_SORT } from '$lib/grid/sort-state.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';

	/* The explorer, as a MODE of this screen rather than a sheet over it. */
	const EXPLORER = 'folders';

	/* Everything in the address EXCEPT where in the results we are. */

	const query = $derived(questionIn(page.url.searchParams, [EXPLORER]));

	const exploring = $derived(page.url.searchParams.get(EXPLORER));
	/** Where the explorer is: a folder's id, or null for the top of the library. */
	const openFolder = $derived(exploring === null || exploring === '1' ? null : exploring);

	/* The folders directly inside the open one, as the explorer found them. */
	let insideThisOne = $state<string[]>([]);

	/* What the wall is asked for: the files in THIS folder. */
	const inFolder = $derived<Record<string, string>>(
		openFolder === null ? {} : directContents({ ...query, in: openFolder })
	);

	/** The explorer, so the toolbar's New-folder button can ask it for one. */
	let explorer = $state<FolderExplorer | null>(null);

	/** How the folders are drawn. The control is on the toolbar; see `folderTools`. */
	let view = $state<FolderView>('list');

	/** Which order they are in. The control is the bar's own, published below. */
	let folderSort = $state<string>(FOLDERS_DEFAULT_SORT);

	/* WHAT THE TOP OF THE TREE OFFERS THE BAR ABOVE IT. */
	const mine = Symbol('folder-view');

	$effect(() => {
		if (exploring === null || openFolder !== null) return;
		screenBar.publish(mine, {
			filterable: 'These are your folders, not files',
			resizable: true,
			playable: 'A folder has nothing to play',
			sorts: [...FOLDER_ORDERS],
			sort: folderSort,
			onSort: (next) => (folderSort = next)
		});
	});

	$effect(() => () => screenBar.release(mine));

	/* How many folders are on screen at the top of the tree. */
	let folderCount = $state<number | undefined>(undefined);

	/* THE ONE THING TO DO ON AN EMPTY LIBRARY THAT HAS FOLDERS IN IT. */
	const library = new Library();
	let scanning = $state(false);
	/* The task a first read is, on Tasks. */
	const SCAN_TASK = 'scan';

	/* AND THE ONE THING TO DO ON A LIBRARY WITH NO FOLDERS AT ALL: add one. */
	const grants = new Grants();
	const picker = new Picker();

	/* ONCE, ON MOUNT, not in an `$effect`. `load` reads the roots and the folders it is about to
	 * write. */
	/* Whether faces are recognized, for the first read's one recommendation: a scan with the
	 * face models downloaded and the switch on finds the people as it reads, and turning it on
	 * later means a second pass over every file. */
	const FACES_KEY = 'faces.enabled';
	let facesOn = $state<boolean | null>(null);

	async function readFaces() {
		try {
			const every = (await fetchSettings()).flatMap((section) => section.settings ?? []);
			const found = every.find((one) => one.key === FACES_KEY);
			if (found && typeof found.value === 'boolean') facesOn = found.value;
		} catch {
			// No recommendation rather than a wrong one.
		}
	}

	onMount(() => {
		void library.load();
		if (session.isAdmin) void readFaces();
		/* Not `grants.load()`: the offer reads only whether the machine's own dialog exists,
		   which is no request at all, and asking a guest's browser for the grants is a refusal
		   in the log on every visit. */
	});
	/* Read again when a setting moves, so the recommendation goes the moment faces are turned on. */
	whenChanged(settingChanges, () => {
		if (session.isAdmin) void readFaces();
	});

	/* The first read of a library is the Scan task's own press: Run now, or Run during quiet
	 * hours. */
	async function scanEverything(at: At) {
		if (scanning) return;
		scanning = true;
		try {
			await pressTask(SCAN_TASK, at);
		} finally {
			scanning = false;
		}
	}

	function explore(folderId: string | null) {
		const address = new URL(page.url);
		address.searchParams.set(EXPLORER, folderId ?? '1');
		/* The position in the OLD folder's listing goes with it, but ONLY when the scope really
		 * changed. */
		if (folderId !== openFolder) address.searchParams.delete(ANCHOR);
		void goto(`${address.pathname}${address.search}`, { keepFocus: true, noScroll: true });
	}

	function closeExplorer() {
		const address = new URL(page.url);
		address.searchParams.delete(EXPLORER);
		void goto(`${address.pathname}${address.search}`, { keepFocus: true, noScroll: true });
	}

	/* The trail above the title, as far as the rows on screen can say. */
	let trail = $state<{ id: string; name: string }[]>([]);
	const crumbs = $derived<Crumb[]>([
		{ label: 'Your folders', href: `${page.url.pathname}?${EXPLORER}=1` },
		...trail.map((step) => ({
			label: step.name,
			href: `${page.url.pathname}?${EXPLORER}=${encodeURIComponent(step.id)}`
		}))
	]);

	/* The strip belongs on the unfiltered library and nowhere else. */
	const typedWords = $derived((query.q ?? '').trim());
	const filters = $derived(filteredBy(query));
	const filtered = $derived(typedWords !== '' || filters);
	const clearedTo = $derived.by(() => {
		const kept = `${cleared(page.url.searchParams, { words: typedWords !== '', filters }, [EXPLORER])}`;
		return kept ? `/browse?${kept}` : '/browse';
	});

	/* WHETHER THE LIBRARY HAS NEVER BEEN READ, which is not "this page came back empty". */
	const unread = $derived(!filtered && library.roots.length > 0);
	/* The first folder on a device never measured is held until the benchmark it queued has
	   settled: nothing of it is read yet, so the wall says the server's sentence for that wait
	   (`toasts-benchmark.svelte.ts`) and offers no Scan now under it. */
	const held = $derived(unread ? benchmarkRun.held : null);
	/* No folders at all, on an account that could add one. See `AddFolder` above. */
	const folderless = $derived(!filtered && library.canManage && library.roots.length === 0);

	/* A plain word answers with the things it NAMES as well as with the files it appears in, and
	 * that answer goes in the slot the strip occupies. */
	const word = $derived(bareWord(query));
	const bare = $derived(word !== '');

	/* The folder browser, opened from a control beside the title. */
</script>

{#if exploring !== null}
	{#if openFolder === null}
		<!-- THE TOP OF THE TREE HOLDS FOLDERS AND NOTHING ELSE. -->
		<!--
			`fillBody`, for the same reason the wall below sets it: the ground here is a place you can
			ACT on.
		-->
		<PageFrame fillBody {crumbs}>
			{#snippet header()}
				<PageHeader
					title="Browse"
					icon="browse"
					count={folderCount}
					beside={backToTiles}
					controls={folderTools}
				/>
			{/snippet}
			<FolderExplorer
				bind:this={explorer}
				here={null}
				{view}
				sort={folderSort}
				whole
				onmove={(folderId) => explore(folderId)}
				ontrail={(steps) => (trail = steps)}
				oncount={(total) => (folderCount = total)}
			/>
		</PageFrame>
	{:else}
		<!-- INSIDE A FOLDER: THE SAME WALL, SCOPED TO IT, not a different screen. -->
		<AssetGrid
			query={inFolder}
			icon="browse"
			title="Browse"
			{crumbs}
			beside={backToTiles}
			tools={folderTools}
			background={folderGround}
			empty={insideThisOne.length > 0
				? 'Nothing loose in this folder. What is here is in the folders above.'
				: 'Nothing in this folder that you can see.'}
		>
			{#snippet beneath()}
				<FolderExplorer
					bind:this={explorer}
					here={openFolder}
					{view}
					onmove={(folderId) => explore(folderId)}
					ontrail={(steps) => (trail = steps)}
					onlisted={(ids) => (insideThisOne = ids)}
				/>
			{/snippet}
		</AssetGrid>
	{/if}

	<!-- Declared once and handed to both branches. Two copies of a header's controls is exactly how
	     the two halves of a screen come to offer slightly different ones. -->
	{#snippet backToTiles()}
		<Tooltip label="Back to tiles">
			<Button
				tone="ghost"
				class="folders"
				icon="folder"
				iconFilled
				aria-label="Back to tiles"
				pressed
				onclick={closeExplorer}
			/>
		</Tooltip>
	{/snippet}

	{#snippet folderGround()}
		<!--
			What right-clicking the empty space of the WALL offers, which is the same two verbs the
			empty space of the folder band offers, because it is the same place.
		-->
		<FolderGround
			onnew={() => explorer?.newFolder()}
			onproperties={() => explorer?.showProperties()}
			onvisibility={() => explorer?.showVisibility()}
			marks={explorer?.groundMarks()}
		/>
	{/snippet}

	{#snippet folderTools()}
		{#if session.isAdmin}
			<!-- The same verb the band's background menu carries. -->
			<Tooltip label="New folder">
				<!-- Outlined at rest and FILLED under the pointer, in the folder yellow. -->
				<Button
					tone="ghost"
					class="new-folder"
					icon="create_new_folder"
					aria-label="New folder"
					onclick={() => explorer?.newFolder()}
				/>
			</Tooltip>
		{/if}
		<!--
			How the FOLDERS are drawn, on the toolbar with the wall's own controls rather than on a bar
			of its own above the band.
		-->
		<div class="views" role="group" aria-label="How to show the folders">
			<Tooltip label="Folders as a list">
				<Button
					tone="ghost"
					icon="menu"
					aria-label="Folders as a list"
					pressed={view === 'list'}
					onclick={() => (view = 'list')}
				/>
			</Tooltip>
			<Tooltip label="Folders in columns">
				<Button
					tone="ghost"
					icon="view_column"
					aria-label="Folders in columns"
					pressed={view === 'columns'}
					onclick={() => (view = 'columns')}
				/>
			</Tooltip>
			<Tooltip label="Folders as tiles">
				<Button
					tone="ghost"
					icon="grid_view"
					aria-label="Folders as tiles"
					pressed={view === 'thumbs'}
					onclick={() => (view = 'thumbs')}
				/>
			</Tooltip>
		</div>
	{/snippet}
{:else}
	<!-- Mounted by the page and not inside AddFolder, which goes the moment the first folder lands:
	     that moment is when this proposes where downloads go. -->
	<DownloadFolderOffer {library} />

	{#if filtered && bare}
		<EntityBand {word} />
	{/if}

	<AssetGrid
		{query}
		icon="browse"
		title="Browse"
		empty={held
			? held
			: unread
				? "Sift has your folders and hasn't read them yet."
				: filtered
					? emptyWallSays('files', typedWords, filters, 'Nothing here matches.')
					: folderless
						? 'Sift has no folders yet. Add one and its files will appear here.'
						: session.isAdmin
							? 'Nothing here yet. Add some files and they will appear.'
							: 'Nothing has been shared with you yet.'}
		emptyAction={held
			? undefined
			: unread
				? scanNow
				: filtered
					? clearSearch
					: folderless
						? addFirstFolder
						: undefined}
	>
		{#snippet notice()}
			<!-- "Your September is ready" for a week after a recap is made, in the header's status
			     place, the one slot for a passing fact, never a second bar above the wall. -->
			<RecapLine />
		{/snippet}
		{#snippet beside()}
			<Tooltip label="Folders">
				<!--
					FILLED, like every other folder in the app: the disk picker in Settings, the rows
					in the explorer this opens, the folder on a tile.
				-->
				<Button
					tone="ghost"
					class="folders"
					icon="folder"
					iconFilled
					aria-label="Folders"
					onclick={() => explore(null)}
				/>
			</Tooltip>
		{/snippet}
	</AssetGrid>
{/if}

<!-- A TOP-LEVEL snippet, handed over as a prop. -->
{#snippet clearSearch()}
	<!-- Clears what its words name; the order and the rest of the address stay. -->
	<Button onclick={() => void goto(clearedTo)}>{clearWallSays(typedWords, filters)}</Button>
{/snippet}

{#snippet addFirstFolder()}
	<div class="scan-now">
		<AddFolder {library} {grants} {picker} scan={false} offer />
	</div>
{/snippet}

{#snippet scanNow()}
	<div class="scan-now">
		<SplitButton
			tone="primary"
			icon="play_arrow"
			disabled={scanning}
			aria-busy={scanning}
			onclick={() => void scanEverything('now')}
			trailingIcon="schedule"
			trailingLabel="Run during quiet hours"
			ontrailing={() => void scanEverything('quiet')}
		>
			Scan now
		</SplitButton>
		<p class="scan-note">
			Scanning checks every file in your folders. On a large library this can take hours, and this
			device may feel slower while it runs. Run during quiet hours waits until quiet hours start,
			which you can set in
			<SettingLink section="tasks" setting="tasks.quiet-hours"
				>Settings > Tasks and Activity</SettingLink
			>.
		</p>
		<!--
			The switch first, linked where it is turned on: Import tasks, whose Identify page the link
			opens on the switch, and which the Faces pane only points at.
		-->
		{#if facesOn === false}
			<p class="scan-note">
				To have Sift find the people in your files during a scan, turn on Recognize faces in your
				library, in
				<SettingLink section="tasks" setting={FACES_KEY}
					>Settings > Tasks and Activity > Import tasks</SettingLink
				>, before you scan. Sift then shows you where to download the face models.
			</p>
		{/if}
	</div>
{/snippet}

<style>
	/* Pointing at New folder fills its glyph and turns it the warm folder yellow, the one colour
	   in the application that means "folder", so the verb says what it is about to make. */
	:global(.btn.new-folder:hover:not(:disabled)) {
		color: var(--sift-folder);
	}

	:global(.btn.new-folder:hover:not(:disabled) .icon) {
		font-variation-settings: 'FILL' 1;
	}

	/* The empty library's one call to action, centred under the sentence it answers. */
	.scan-now {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: var(--space-3);
		margin-block-start: var(--space-3);
	}

	.scan-note {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
		max-inline-size: 60ch;
		text-align: center;
	}

	/* The three folder-view buttons as one control, the way the bar's own groups are spaced. */
	.views {
		display: flex;
		gap: var(--space-1);
	}

	/* The warm folder yellow the disk picker in Settings uses, so a folder is the same colour
	   wherever one is drawn, and so this reads as a folder rather than as another grey tool. */
	/* The warm folder yellow, which is the whole reason this rule survives: everything else
	   about the control (the box, the corner, the hover, the focus ring) is the shared button's. */
	:global(.btn.folders) {
		inline-size: 32px;
		block-size: 32px;
		border-radius: var(--radius-md);
		color: var(--sift-folder);
	}

	/* The ground steps and the COLOUR does not. */
	:global(.btn.folders:hover:not(:disabled)) {
		background: var(--sift-surface-3);
		color: var(--sift-folder);
	}
</style>
