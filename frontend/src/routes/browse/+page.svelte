<script lang="ts">
	/* WHY NO HOVER: the one hover rule here dresses the SHARED button, whose motion is its own: it
	   transitions its background over --dur-instant for every tone. What this file changes is which
	   shade the step lands on, not whether there is a step, and a second transition written here
	   would be one rule kept in two files. */
	/*
	 * Browse: the whole library, newest first.
	 *
	 * Everything that makes a grid a grid lives in the component (see `$lib/components/AssetGrid`).
	 * What is left here is the one thing that is this screen's own: the query comes from the address
	 * bar, so a filtered view is a link somebody can send or reopen tomorrow.
	 */
	import { onMount } from 'svelte';
	import { ANCHOR } from '$lib/grid/anchor';
	import { bareWord, questionIn } from './question';
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

	/*
	 * The explorer, as a MODE of this screen rather than a sheet over it.
	 *
	 * `folders` in the address is what carries it: `1` at the top of the library, a folder's id
	 * inside one. That is the whole reason it is in the address rather than in a variable: a
	 * reload lands back where you were, the back button walks UP a folder instead of out of the
	 * explorer, and the folder you are looking at is a link you can send.
	 *
	 * It is excluded from `query` below, which is what the grid is handed: it says which SCREEN this
	 * is, not which files to ask for, and passed down it would go to the server as a filter nothing
	 * understands.
	 */
	const EXPLORER = 'folders';

	/*
	 * Everything in the address EXCEPT where in the results we are.
	 *
	 * `from` names the file the page starts at, and `near` where that file was. Both are a position,
	 * not a question about a file. So handing it down as part of the query would send it to the server as a filter, and
	 * would make every page turn look to this screen like a NEW question: the grid resets to the
	 * beginning when the question changes, and re-fetches. The grid reads the anchor from the address
	 * itself, which is where it belongs.
	 *
	 * Also excluded from `filtered` and `bare` below, and that matters: with either counted, turning
	 * one page would take the recently-viewed strip off the screen, and coming Back to a search would
	 * take the band of things it names away.
	 */

	const query = $derived(questionIn(page.url.searchParams, [EXPLORER]));

	const exploring = $derived(page.url.searchParams.get(EXPLORER));
	/** Where the explorer is: a folder's id, or null for the top of the library. */
	const openFolder = $derived(exploring === null || exploring === '1' ? null : exploring);

	/*
	 * The folders directly inside the open one, as the explorer found them.
	 *
	 * What the EMPTY STATE reads, and nothing else: a folder with nothing loose in it says so in
	 * different words from a folder with nothing in it at all, and that is the only question here
	 * the wall's own count cannot answer. The wall itself is filtered by the folder's own id with a
	 * depth (`in:` alone means a folder's whole SUBTREE to the server). See
	 * `$lib/library/direct`.
	 *
	 * It comes up from the explorer rather than being fetched here, because the explorer has
	 * already read the whole tree to draw the band and a second reader of it is a second answer.
	 */
	let insideThisOne = $state<string[]>([]);

	/*
	 * What the wall is asked for: the files in THIS folder.
	 *
	 * `in` takes a folder's id, which is the one form that names exactly one folder: a path is
	 * relative to the library folder it is in, so two libraries each holding a "2024" answer to the
	 * same name. At the top of the tree there is no folder to be inside, so nothing is passed and
	 * the wall is the whole library: those files are all in one of the folders listed above it,
	 * which is what "Your folders" means.
	 *
	 * IN this folder, not UNDER it. See `directContents`.
	 */
	const inFolder = $derived<Record<string, string>>(
		openFolder === null ? {} : directContents({ ...query, in: openFolder })
	);

	/** The explorer, so the toolbar's New-folder button can ask it for one. */
	let explorer = $state<FolderExplorer | null>(null);

	/** How the folders are drawn. The control is on the toolbar; see `folderTools`. */
	let view = $state<FolderView>('list');

	/** Which order they are in. The control is the bar's own, published below. */
	let folderSort = $state<string>(FOLDERS_DEFAULT_SORT);

	/*
	 * WHAT THE TOP OF THE TREE OFFERS THE BAR ABOVE IT.
	 *
	 * Only at the top, and that is the whole condition: inside a folder the wall is `AssetGrid` and
	 * it publishes for itself, so a second publisher here would be two screens fighting over one bar.
	 * Up here there is no wall, so without this nothing is published and the bar sits with every
	 * control dimmed, including the order menu, on the one screen in the application that is a LIST
	 * of things with names.
	 *
	 * Each control says what it is rather than disappearing, which is the rule this bar is built on.
	 * A folder is not a file, so the query language does not apply to it; a folder has nothing to
	 * play; and the size slider genuinely does act here, because the folder tiles read `gridSize`.
	 */
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

	/*
	 * How many folders are on screen at the top of the tree.
	 *
	 * The wall's own count is what the header draws everywhere else, and at the top there is no
	 * wall. So the honest number beside the title is the one thing this screen is showing. It
	 * comes up from the explorer with the trail, because the folders live in the rows it fetched.
	 */
	let folderCount = $state<number | undefined>(undefined);

	/*
	 * THE ONE THING TO DO ON AN EMPTY LIBRARY THAT HAS FOLDERS IN IT.
	 *
	 * Adding a folder does not read it (see `NewRoot.scan` on the server), so somebody finishing
	 * setup arrives here with their folders known and nothing in them read yet, and needs a way to
	 * start from this screen rather than a sentence telling them to add files.
	 *
	 * The roots are read once. This screen does not otherwise care about them, and one that polled
	 * for a state it uses to decide a single button would be paying for it on every visit.
	 */
	const library = new Library();
	let scanning = $state(false);
	/* The task a first read is, on Tasks. */
	const SCAN_TASK = 'scan';

	/*
	 * AND THE ONE THING TO DO ON A LIBRARY WITH NO FOLDERS AT ALL: add one.
	 *
	 * A fresh install has no folder step, so this screen must not tell somebody to add files
	 * without a way to do it. The offer is the Settings flow itself (`AddFolder`), never a second
	 * copy of it, and it adds WITHOUT reading: what follows on this screen is the Scan Now offer
	 * above, with its warning about how long a first read takes.
	 *
	 * Only for an account that may manage the library, which `canManage` says once the roots have
	 * answered, so a guest, and the moment before the answer lands, see the plain sentence.
	 */
	const grants = new Grants();
	const picker = new Picker();

	/*
	 * ONCE, ON MOUNT, not in an `$effect`.
	 *
	 * `load` reads the roots and the folders it is about to write. Inside an effect those reads
	 * would be dependencies, so finishing the load would re-run the effect: a request storm. The
	 * store untracks its own read as well, and what this screen wants is the answer once.
	 */
	/*
	 * Whether faces are recognized, for the first read's one recommendation: a scan with the face
	 * models downloaded and the switch on finds the people as it reads, and turning it on later
	 * means a second pass over every file. Null until read, and only an admin reads it.
	 */
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
		/* Not `grants.load()`: the offer reads only whether the machine's own dialog exists, which is
		   no request at all, and asking a guest's browser for the grants is a refusal in the log on
		   every visit. */
	});
	/* Read again when a setting moves, so the recommendation goes the moment faces are turned on. */
	whenChanged(settingChanges, () => {
		if (session.isAdmin) void readFaces();
	});

	/*
	 * The first read of a library is the Scan task's own press: Run now, or Run during quiet hours. The
	 * Scan task is the one door; what follows a scan is each stage's own When, which is where
	 * somebody chose it.
	 */
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
		/*
		 * The position in the OLD folder's listing goes with it, but ONLY when the scope really
		 * changed.
		 *
		 * `from` names the file the wall opens at. Walking into a folder is a different listing, so
		 * carrying it over asks the new wall to start at a file that is not in it. Opening the
		 * explorer where you already are is not a different listing, though, and clearing it there
		 * would throw away somebody's place in the wall for nothing, which is the whole of what
		 * `from` is for.
		 */
		if (folderId !== openFolder) address.searchParams.delete(ANCHOR);
		void goto(`${address.pathname}${address.search}`, { keepFocus: true, noScroll: true });
	}

	function closeExplorer() {
		const address = new URL(page.url);
		address.searchParams.delete(EXPLORER);
		void goto(`${address.pathname}${address.search}`, { keepFocus: true, noScroll: true });
	}

	/* The trail above the title, as far as the rows on screen can say.
	 *
	 * The explorer knows the folder names and this screen owns the header, so the names come back up
	 * from it. Empty at the top, where `Breadcrumbs` draws nothing at all: one crumb is not a trail.
	 */
	let trail = $state<{ id: string; name: string }[]>([]);
	const crumbs = $derived<Crumb[]>([
		{ label: 'Your folders', href: `${page.url.pathname}?${EXPLORER}=1` },
		...trail.map((step) => ({
			label: step.name,
			href: `${page.url.pathname}?${EXPLORER}=${encodeURIComponent(step.id)}`
		}))
	]);

	/* The strip belongs on the unfiltered library and nowhere else. Under a filtered view it would
	 * be a row of things that do not match what was asked for, sitting above the things that do. */
	const filtered = $derived(Object.keys(query).length > 0);

	/*
	 * WHETHER THE LIBRARY HAS NEVER BEEN READ, which is not "this page came back empty".
	 *
	 * A search that matches nothing on a scanned library is also folders and no files on screen,
	 * and offering to scan the whole thing again there is alarming and wrong on both counts.
	 *
	 * A filter is the difference. Under one, an empty page means nothing matched and says so; the
	 * offer belongs only to the unfiltered view of a library that genuinely holds nothing.
	 */
	const unread = $derived(!filtered && library.roots.length > 0);
	/* The first folder on a device never measured is held until the benchmark it queued has
	   settled: nothing of it is read yet, so the wall says the server's sentence for that wait
	   (`toasts-benchmark.svelte.ts`) and offers no Scan now under it. */
	const held = $derived(unread ? benchmarkRun.held : null);
	/* No folders at all, on an account that could add one. See `AddFolder` above. */
	const folderless = $derived(!filtered && library.canManage && library.roots.length === 0);

	/*
	 * A plain word answers with the things it NAMES as well as with the files it appears in, and
	 * that answer goes in the slot the strip occupies.
	 *
	 * Only for a BARE word: a query carrying filters is a different question, and those results are
	 * files. A band of people and tags above them answers something nobody asked. See `bareWord`.
	 */
	const word = $derived(bareWord(query));
	const bare = $derived(word !== '');

	/* The folder browser, opened from a control beside the title.
	 *
	 * Beside the TITLE rather than on the rail's Browse row, deliberately: a rail row navigates,
	 * and a click target inside one that does something else is a trap. The glyph is the folder
	 * the Library screen uses, so the same picture means the same thing in both places.
	 *
	 * Between the word and the count rather than out at the end of the toolbar. It is another way
	 * of saying which files "Browse" is about, so it belongs to the heading; at the far end it
	 * would be a grey glyph among the sort and size controls and read as one of them.
	 */
</script>

{#if exploring !== null}
	{#if openFolder === null}
		<!--
			THE TOP OF THE TREE HOLDS FOLDERS AND NOTHING ELSE.

			There are no loose files up here to draw: every file in the library is inside one of the
			folders on this screen, so a wall under them would be the whole library repeated under a
			list of the folders it is already in, which is Browse, one press away, wearing a folder
			list as a hat. This is what "This PC" shows: the drives, and no files.

			The same frame and the same header as the wall it is a mode of, so the title, the trail and
			the controls land in the same place either way. `PageFrame` owns that inset, which is the
			whole reason this branch cannot drift from the one below it.
		-->
		<!--
			`fillBody`, for the same reason the wall below sets it: the ground here is a place you can
			ACT on. Right-clicking the empty space offers New folder, and "the empty space" is exactly
			the part of the window the content box does not reach: ten folders in a list is a band a
			few hundred pixels tall, so everything under it would belong to nothing and a right-click
			there would get the browser's own menu. It only grows the box to the height it is already scrolling
			inside, so nothing can overflow because of it.
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
		<!--
			INSIDE A FOLDER: THE SAME WALL, SCOPED TO IT, not a different screen.

			It is `AssetGrid`, with `in` set to the folder being looked at, so what is in the folder
			IS the wall: the same tiles, the same hover preview, the same size slider, the same
			menus, the same paging, and the same insets and heading. The folders inside it are
			handed in as the band above it.
		-->
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
			empty space of the folder band offers, because it is the same place. A file manager makes
			no distinction between the part of a window that happens to have folders in it and the part
			that happens to have files; both are "in this folder".

			Rendered by the explorer so there is one dialog and one refusal behind both, rather than a
			second copy of the New folder flow living up here.
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
			<!-- The same verb the band's background menu carries. The menu is what somebody reaches for
			     by habit; this is the one that can be FOUND, and it has to exist because a band is
			     exactly as tall as its folders: with ten of them there is no empty space to
			     right-click. Both call `newFolder` on the explorer, so there is one dialog and one
			     refusal. -->
			<Tooltip label="New folder">
				<!-- Outlined at rest and FILLED under the pointer, in the folder yellow. The fill is the
				     hover state rather than the resting one: this is a verb, not a place, so it should
				     not sit lit in the same colour as the folder button beside the title, which IS a
				     place and is coloured for what it is. -->
				<Button
					tone="ghost"
					class="new-folder"
					icon="create_new_folder"
					aria-label="New folder"
					onclick={() => explorer?.newFolder()}
				/>
			</Tooltip>
		{/if}
		<!-- How the FOLDERS are drawn, on the toolbar with the wall's own controls rather than on a
		     bar of its own above the band. Three ways of looking, because they answer three
		     questions: a plain list when you know the name, the same names in columns when there are
		     enough of them that one column wastes the window, and pictures when you do not know the
		     name at all. -->
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
					? 'Nothing here matches.'
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
				<!-- FILLED, like every other folder in the app: the disk picker in Settings, the rows in
				     the explorer this opens, the folder on a tile. It is filled because of what it IS,
				     not because of a state it is in, so the shared button is handed `iconFilled`.

				     ONE button, not two. It turns the explorer on and the same button turns it off,
				     because a second folder button beside it doing almost the same thing is two names
				     for one idea. -->
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

<!-- A TOP-LEVEL snippet, handed over as a prop. One written inside the component's own tag is
     passed whatever the condition beside it says, so the offer would appear on a library that has
     no folders to scan. -->
{#snippet clearSearch()}
	<!-- The one action that ends a filtered wall with nothing on it: the address without its
	     filters is the whole of Browse again. -->
	<Button onclick={() => void goto('/browse')}>Clear the search</Button>
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
		<!-- The switch first, linked where it is turned on: Importing, whose Identify page the link
		     opens on the switch, and which the Faces pane only points at. The models come after,
		     because the Faces pane draws their download only once the switch is on, and the Identify
		     page then links it itself. -->
		{#if facesOn === false}
			<p class="scan-note">
				To have Sift find the people in your files during a scan, turn on Recognize faces in your
				library, in
				<SettingLink section="importing" setting={FACES_KEY}>Settings > Importing</SettingLink>,
				before you scan. Sift then shows you where to download the face models.
			</p>
		{/if}
	</div>
{/snippet}

<style>
	/* Pointing at New folder fills its glyph and turns it the warm folder yellow, the one colour
	   in the application that means "folder", so the verb says what it is about to make. Outlined and
	   in ordinary ink at rest: it is a verb rather than a place, and the folder button beside the
	   title is the one that is lit for what it IS.

	   The colour needs the hover arm because the ghost tone's own hover sets `--sift-ink`, which
	   would take the glyph back to grey exactly when it is being used. `:global` twice because the
	   class is handed to `Button` and the glyph inside it is compiled in `Icon`. */
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
	/* The warm folder yellow, which is the whole reason this rule survives: everything else about
	   the control (the box, the corner, the hover, the focus ring) is the shared button's.
	   `:global` because the class is handed to it, and there is nothing to scope it under here, so
	   the name carries the scoping instead. */
	:global(.btn.folders) {
		inline-size: 32px;
		block-size: 32px;
		border-radius: var(--radius-md);
		color: var(--sift-folder);
	}

	/* The ground steps and the COLOUR does not. The shared ghost button's hover also sets
	   `--sift-ink`, which would turn the folder white the moment you pointed at it: the one
	   control in the app that is coloured for what it IS losing its colour exactly when it is
	   being used. Only the surface answers here. */
	:global(.btn.folders:hover:not(:disabled)) {
		background: var(--sift-surface-3);
		color: var(--sift-folder);
	}
</style>
