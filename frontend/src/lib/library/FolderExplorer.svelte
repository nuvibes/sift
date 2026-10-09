<script lang="ts">
	import { Button, Empty, Pressable, Skeleton, TextInput } from '$lib/components/common';
	import { onRecord } from '$lib/shell/when';
	import Scroller from '$lib/components/common/Scroller.svelte';
	/* The file explorer: Browse, taken out of tiles and into folders. */
	import { onMount } from 'svelte';
	import { SvelteMap } from 'svelte/reactivity';
	import { api } from '$lib/api/client';
	import * as facts from '$lib/library/facts';
	import Icon from '$lib/components/Icon.svelte';
	import { thumbUrl } from '$lib/entity/art';
	import ContextMenu from '$lib/components/common/ContextMenu.svelte';
	import ContextMenuItem from '$lib/components/common/ContextMenuItem.svelte';
	import BatchRename from '$lib/organize/BatchRename.svelte';
	import ContextMenuGroup from '$lib/components/common/ContextMenuGroup.svelte';
	import SharingMark from '$lib/components/common/SharingMark.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import FolderGround from '$lib/library/FolderGround.svelte';
	import FolderSheets from '$lib/library/FolderSheets.svelte';
	import { folderMarks, folderMenuParts, refusedOf } from '$lib/library/folder-verbs';
	import { session } from '$lib/shell/session.svelte';
	import { setHidden } from '$lib/library/hiding';
	import { gridSize } from '$lib/grid/grid.svelte';
	import { FOLDERS_DEFAULT_SORT } from '$lib/grid/sort-state.svelte';
	import { reloadOnLibraryChange } from '$lib/library/changes.svelte';
	import { Library } from '$lib/library/library.svelte';
	import Field from '$lib/components/common/Field.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import DataRows from '$lib/components/common/DataRows.svelte';
	import DataRow from '$lib/components/common/DataRow.svelte';
	import PickDialog from '$lib/components/common/PickDialog.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Problem from '$lib/components/common/Problem.svelte';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { FileVerbs, Selection, VerbMenuItems } from '$lib/components/common';
	import { filesUnder, namingFolder, overFolder } from '$lib/library/folder-add-to';
	import type { Folder } from '$lib/library/tree';
	import type { components } from '$lib/api/schema';

	/** The three ways of looking at a folder's folders: `list`, one name per line; `columns`,
	 * flowed down then across, using a wide window; `thumbs`, a picture each, when the name is
	 * not known. */
	export type FolderView = 'list' | 'columns' | 'thumbs';

	interface Props {
		/** Which folder is open, or null for the top. Owned by the ADDRESS, so it comes in one way. */
		here?: string | null;
		/** Somebody walked into a folder, or back up. The screen puts it in the address. */
		onmove?: (folderId: string | null) => void;
		/** The way back up, as far as the rows can say it, so the screen can draw it above its title. */
		ontrail?: (steps: { id: string; name: string }[]) => void;
		/** A list of names, or a wall of pictures: owned by the SCREEN, whose toolbar holds every
		 * "how should this look" control, so this mode looks like the wall it belongs to. */
		view?: FolderView;
		/** Whether this is the WHOLE screen rather than a band above a wall: at the top of the
		 * tree there are no loose files, so the list takes no scrolling box of its own (the
		 * frame scrolls) and no half-window cap. */
		whole?: boolean;
		/** How many folders are on screen, for a screen that draws the number elsewhere: at the
		 * top, with no wall, it comes from the very list drawn (as `AssetGrid.oncount`). */
		oncount?: (total: number) => void;
		/** WHICH folders are directly inside this one, for the screen's EMPTY STATE, which tells
		 * "nothing loose here" from "nothing at all". */
		onlisted?: (folderIds: string[]) => void;
		/** Which order the folders are in (`FOLDER_ORDERS`), owned by the SCREEN like the view:
		 * the control is the bar's order menu. */
		sort?: string;
	}

	let {
		here = null,
		onmove,
		ontrail,
		view = 'list',
		whole = false,
		oncount,
		onlisted,
		sort = FOLDERS_DEFAULT_SORT
	}: Props = $props();

	function goTo(folderId: string | null) {
		onmove?.(folderId);
	}

	/* Which shape a folder is drawn as: a row in both list views, a tile in the picture one. */
	const shape = $derived(view === 'thumbs' ? 'tile' : 'row');

	/* How tall a folder's picture is: the wall's own row height, so ONE slider moves both; null
	   leaves the stylesheet's default to answer. */
	const artHeight = $derived(gridSize.step === null ? null : `${gridSize.step}px`);

	let folders = $state<Folder[]>([]);
	let loaded = $state(false);
	let failed = $state(false);
	/* One cover per folder, for the picture view, fetched as a folder is drawn. */
	const covers = new SvelteMap<string, string | null>();
	const asked = new Set<string>();

	onMount(() => void load());

	/* And again the moment a share, a restrict or a rearrangement moves, which changes the marks or
	 * the list itself, as on the Library screen and the entity walls. */
	reloadOnLibraryChange(() => void load());

	async function load() {
		loaded = false;
		failed = false;
		/* A count under a folder is exactly what these changes move, so the tallies are asked again. */
		tallies.clear();
		talliesAsked.clear();
		try {
			const answer = await api.get<components['schemas']['FoldersView']>('/library/folders');
			folders = answer.folders;
		} catch {
			failed = true;
			folders = [];
		} finally {
			loaded = true;
		}
	}

	const byId = $derived(new Map(folders.map((folder) => [folder.id, folder])));
	const current = $derived(here === null ? null : (byId.get(here) ?? null));

	/* What is directly inside where we are, sorted HERE so the order does not depend on how the
	 * rows arrived and cannot change under the pointer when a cover lands. */
	const children = $derived(
		folders
			.filter((folder) => {
				const parent = folder.parent_id ?? null;
				if (parent === here) return true;
				/* At the top, a folder whose PARENT is not in the answer belongs here too: a folder
				 * shared inside a restricted one is visible without its parent (nearest wins), and
				 * dropping it would make the share reachable from nowhere. */
				return here === null && parent !== null && !byId.has(parent);
			})
			.slice()
			.sort(inOrder)
	);

	/* The order, applied HERE: the whole tree arrives in one answer, so nothing is paged. */
	function inOrder(one: Folder, other: Folder): number {
		const byName = one.name.localeCompare(other.name, undefined, { sensitivity: 'base' });
		if (sort === 'name_za') return -byName;
		if (sort === 'name_az') return byName;

		/* The four orders that need the second request: name order breaks ties and holds the
		 * list steady until the facts land. */
		const mine = ordering.get(one.id);
		const theirs = ordering.get(other.id);
		if (sort === 'largest' || sort === 'smallest') {
			const by = (mine?.file_count ?? 0) - (theirs?.file_count ?? 0);
			if (by !== 0) return sort === 'largest' ? -by : by;
			return byName;
		}
		const at = mine?.newest_at ?? null;
		const other_at = theirs?.newest_at ?? null;
		if (at === null && other_at === null) return byName;
		if (at === null) return 1;
		if (other_at === null) return -1;
		if (at !== other_at) return sort === 'newest' ? other_at - at : at - other_at;
		return byName;
	}

	/* WHAT AN ORDER NEEDS, ASKED FOR ONLY WHEN AN ORDER NEEDS IT: the tree carries no counts
	 * (`_VISIBLE_FOLDERS`), so size and time orders come from their own request, keyed by the
	 * folder looked at. */
	let ordering = $state(new SvelteMap<string, components['schemas']['FolderFacts']>());

	$effect(() => {
		const needed = sort !== 'name_az' && sort !== 'name_za';
		const parent = here;
		if (!needed) return;
		void loadFacts(parent);
	});

	async function loadFacts(parent: string | null): Promise<void> {
		try {
			const said = await api.get<components['schemas']['FoldersFacts']>('/library/folders/facts', {
				query: parent === null ? {} : { parent }
			});
			// Still the folder being looked at: two quick steps down are enough to change it.
			if (here !== parent) return;
			const next = new SvelteMap<string, components['schemas']['FolderFacts']>();
			for (const one of said.folders) next.set(one.id, one);
			ordering = next;
		} catch {
			// A guest, or a library that could not be read. Left empty, which sorts by name.
		}
	}

	/** The way back up, top-most first. Read from the rows rather than from the path text. */
	const trail = $derived.by(() => {
		const path: Folder[] = [];
		let walk = current;
		while (walk) {
			path.unshift(walk);
			walk = walk.parent_id ? (byId.get(walk.parent_id) ?? null) : null;
		}
		return path;
	});

	/* Handed up to the screen, which has none of the names, rather than it fetching the tree again. */
	$effect(() => {
		ontrail?.(trail.map((step) => ({ id: step.id, name: step.name })));
	});

	/* Told only once the answer has landed, or the header would flash zero on the way to ten. */
	$effect(() => {
		if (loaded) oncount?.(children.length);
	});

	/* And which they are, for the wall's empty state, on the same condition. */
	$effect(() => {
		if (loaded) onlisted?.(children.map((folder) => folder.id));
	});

	/** What names this folder to the rest of the application: its id. */
	function nameFor(folder: Folder): string {
		return folder.id;
	}

	/** A new folder, from OUTSIDE: the toolbar's button, since a full band has no background. */
	export function newFolder(): void {
		askToMake();
	}

	/** What the folder looked at IS, from OUTSIDE: the ground is the wall's, the folder is ours. */
	export function showProperties(): void {
		if (current) void askAboutProperties(current);
	}

	/** Who can reach the folder being looked at, from OUTSIDE this component, as `showProperties`. */
	export function showVisibility(): void {
		if (current) sheets?.visibility(current);
	}

	/** A folder's own rows, a part each: what may leave this device, then who sees it. */
	function folderParts(folder: Folder) {
		const hide = (hidden: boolean) => void hideFolder(folder, hidden);
		const share = () => sheets?.share([folder]);
		const handlers = { hide, share, visibility: () => sheets?.visibility(folder) };
		const marks = folderMarks(folder, () => void load());
		return folderMenuParts({ isAdmin: session.isAdmin, hidden: folder.hidden, handlers }, marks);
	}

	/** The two marks on the folder being looked at, for the ground's menu, as `showVisibility`. */
	export const groundMarks = () =>
		current && session.isAdmin ? folderMarks(current, () => void load()) : undefined;

	/* WHAT A FOLDER IS: the panel a file manager opens on right-click, from one request
	 * (`/library/folders/{id}/properties`) so count and size describe one moment. */
	/* `onDisk`, not `facts`, which is the imported formatter's name. */
	let about = $state<Folder | null>(null);
	let onDisk = $state<components['schemas']['FolderProperties'] | null>(null);
	let factsFailed = $state(false);

	async function askAboutProperties(folder: Folder) {
		about = folder;
		onDisk = null;
		factsFailed = false;
		try {
			const said = await propertiesOf(folder);
			// Still the folder the panel is open on, or a slow answer would land under another name.
			if (about?.id === folder.id) onDisk = said;
		} catch {
			// A guest, or a folder whose disk moved: the two rows still true are drawn.
			if (about?.id === folder.id) factsFailed = true;
		}
	}

	/** The one request behind both the panel and the hover tally, filling the tally cache on the way,
	 *  so the two never disagree and nothing is asked twice. */
	async function propertiesOf(folder: Folder): Promise<components['schemas']['FolderProperties']> {
		const said = await api.get<components['schemas']['FolderProperties']>(
			`/library/folders/${encodeURIComponent(folder.id)}/properties`
		);
		tallies.set(folder.id, said);
		talliesAsked.add(folder.id);
		return said;
	}

	/* WHAT IS UNDER A FOLDER, ON THE WAY PAST: the status-bar count, from the properties route,
	 * asked once per folder when a pointer first crosses it, never up front. */
	const tallies = new SvelteMap<string, components['schemas']['FolderProperties'] | null>();
	const talliesAsked = new Set<string>();

	async function tallyFor(folder: Folder): Promise<void> {
		if (talliesAsked.has(folder.id)) return;
		talliesAsked.add(folder.id);
		try {
			await propertiesOf(folder);
		} catch {
			// A guest, or a folder whose disk has gone: said, rather than "Reading..." for ever.
			tallies.set(folder.id, null);
		}
	}

	/** The facts, as rows, keyed for `DataRows`, which holds a list under the pointer. */
	const aboutFacts = $derived.by(() => {
		const folder = about;
		if (folder === null) return [];
		const disk = onDisk;
		const waiting = disk === null && !factsFailed;
		return [
			{
				name: 'Location',
				said: disk?.location ?? (waiting ? 'Reading\u2026' : folder.rel_path || folder.name)
			},
			...(factsFailed
				? []
				: [
						{
							name: 'Size on disk',
							said: disk === null ? 'Reading\u2026' : inBytes(disk.size_bytes)
						},
						{
							name: 'Contains',
							said:
								disk === null
									? 'Reading\u2026'
									: `${countOf(disk.file_count, 'File')}, ${countOf(disk.folder_count, 'Folder')}`
						},
						{
							name: 'Created',
							said:
								disk === null
									? 'Reading\u2026'
									: disk.created_at === null
										? 'Not known'
										: onDay(disk.created_at)
						}
					]),
			{ name: 'Shared', said: folder.shared || folder.shared_here ? 'Yes' : 'No' },
			{ name: 'Hidden', said: folder.hidden ? 'Yes' : 'No' }
		];
	});

	/** "1,954 Files": a grouped number and an agreeing plural, as a properties panel writes it. */
	function countOf(many: number, what: string): string {
		return `${many.toLocaleString()} ${many === 1 ? what : `${what}s`}`;
	}

	/** The exact byte count beside the round one (`facts.size`, the app's one formatter): one is read,
	 *  the other compared. */
	function inBytes(many: number): string {
		return `${facts.size(many) ?? '0 B'} (${many.toLocaleString()} bytes)`;
	}

	/** When it was made, in the reader's own zone. Seconds from the server, milliseconds to `Date`. */
	function onDay(seconds: number): string {
		return onRecord(seconds);
	}

	/* Sharing, the report and hiding a folder from here, through the same sheets and helpers as the
	 * tree in Settings: a share reaches everything inside, including what arrives later. */
	let sheets = $state<FolderSheets | null>(null);
	let renameFilesOpen = $state(false);
	let renamingFiles = $state<string | null>(null);

	async function hideFolder(folder: Folder, hide: boolean) {
		const moved = await setHidden([folder.id], hide, {
			noun: 'folder',
			set: (id, wanted) => api.put(`/folders/${id}/vault`, { body: { vault: wanted } })
		});
		if (moved.length > 0) await load();
	}

	/** One picture to stand for a folder, in the picture view. Asked once per folder, ever. */
	async function coverFor(folder: Folder): Promise<void> {
		if (asked.has(folder.id)) return;
		asked.add(folder.id);
		try {
			const page = await api.get<components['schemas']['AssetPageResponse']>('/assets', {
				query: { in: nameFor(folder), limit: '1' }
			});
			const first = page.items.find((item) => item.thumb && !item.concealed);
			// Through the one address a tile's still uses, so a still cut again is fetched again.
			covers.set(folder.id, first ? thumbUrl(first) : null);
		} catch {
			covers.set(folder.id, null);
		}
	}

	$effect(() => {
		if (view !== 'thumbs') return;
		for (const folder of children) void coverFor(folder);
	});

	/* Making a folder, renaming one and moving one: the server decides whether Sift may change
	 * files here, and its refusal is a sentence drawn in the dialog where the name was typed. */
	const library = new Library();
	let asking = $state<'make' | 'rename' | null>(null);
	let subject = $state<Folder | null>(null);
	let typed = $state('');
	let refusal = $state<string | null>(null);
	let movingOpen = $state(false);

	/* Add to's host gets an empty selection: a folder's menu acts on its files, not the wall's picks. */
	const nothingPicked = new Selection();

	function askToMake() {
		subject = current;
		typed = '';
		refusal = null;
		asking = 'make';
	}

	function askToRename(folder: Folder) {
		subject = folder;
		typed = folder.name;
		refusal = null;
		asking = 'rename';
	}

	async function settle() {
		const folder = subject;
		if (asking === 'make') {
			// At the top there is no folder open; a library folder is added in Settings.
			if (folder === null) {
				refusal = 'Open one of your folders first, then Sift can make a folder inside it.';
				return;
			}
			refusal = await library.createFolder(folder.id, typed);
		} else if (asking === 'rename' && folder !== null) {
			refusal = await library.renameFolder(folder.id, typed);
		}
		if (refusal === null) {
			asking = null;
			await load();
		}
	}

	function askToMove(folder: Folder) {
		subject = folder;
		refusal = null;
		movingOpen = true;
	}

	/** Everywhere this folder could go: its own library, minus itself and anything inside it. */
	const destinations = $derived(
		subject === null
			? []
			: folders
					.filter(
						(one) =>
							one.root_id === subject?.root_id &&
							one.id !== subject?.id &&
							!(one.rel_path ?? '').startsWith(`${subject?.rel_path ?? ''}/`)
					)
					.map((one) => ({ id: one.id, name: one.rel_path || one.name }))
	);

	/* DELETING A FOLDER: the one thing on this screen that removes bytes, asked with the count
	 * in the question ("and the 1,954 files in it?") */
	let deleting = $state<Folder | null>(null);
	/* Bound to the dialog; `deleting` stays while the sheet fades, so the name does not vanish. */
	let deleteOpen = $state(false);

	const deletingCount = $derived(deleting === null ? null : (tallies.get(deleting.id) ?? null));

	function askToDelete(folder: Folder) {
		// Asked here too: a menu opened from the keyboard never hovered the row.
		void tallyFor(folder);
		deleting = folder;
		deleteOpen = true;
	}

	async function deleteIt(folder: Folder) {
		const { said, refusal } = await library.deleteFolder(folder.id);
		if (refusal !== null) {
			toasts.show(refusal, { tone: 'error' });
			return;
		}
		if (said === null) return;
		if (said.left_behind) {
			// A partial run said out loud: the directory stays for what Sift does not index.
			toasts.show(
				`Deleted ${said.files === 1 ? 'one file' : `${said.files.toLocaleString()} files`} from ${folder.name}. It's still on your disk: it holds something Sift doesn't index.`,
				{ tone: 'info' }
			);
		} else {
			toasts.show(
				said.files === 1
					? `Deleted ${folder.name} and the one file in it`
					: `Deleted ${folder.name} and the ${said.files.toLocaleString()} files in it`,
				{ tone: 'success' }
			);
		}
		await load();
	}

	async function moveTo(parentId: string) {
		const folder = subject;
		if (folder === null) return;
		movingOpen = false;
		const said = await library.moveFolder(folder.id, parentId);
		if (said) toasts.show(said, { tone: 'error' });
		else await load();
	}
</script>

<!--
	THE FOLDERS IN THIS ONE, as a band directly above the wall of files, which is the real `AssetGrid`
	(clickable, previewing, sized by the slider).
-->
<ContextMenu
	triggerClass="band-wrap {whole ? 'as-screen' : 'as-band'}"
	label={current ? current.name : 'Your folders'}
>
	<div class="band" class:whole>
		{#if failed}
			<Problem message="Those couldn't be read." />
		{:else if !loaded}
			<Skeleton lines={3} />
		{:else if children.length === 0 && here === null}
			<!-- Only at the TOP is an empty answer worth a sentence: no folders at all. -->
			<Empty scope="block">No folders yet. Add one in Settings and it will appear here.</Empty>
		{:else if children.length > 0}
			<!-- As a BAND it caps itself and scrolls inside; as the WHOLE screen the frame scrolls, and a
			     second scrollbar is forbidden. -->
			{#if whole}
				{@render folderList()}
			{:else}
				<Scroller>
					{@render folderList()}
				</Scroller>
			{/if}
		{/if}
	</div>
	<!-- The menu on the BACKGROUND of the band, a file manager's gesture; the toolbar's button calls
	     the same function. -->
	{#snippet items()}
		<FolderGround
			onnew={askToMake}
			onproperties={current ? showProperties : undefined}
			onvisibility={current ? showVisibility : undefined}
			marks={groundMarks()}
		/>
	{/snippet}
</ContextMenu>

{#snippet folderList()}
	<ul class={view} style:--folder-art={artHeight}>
		{#each children as folder (folder.id)}
			<li>
				<!-- Declared under the `<li>`: as a direct child of a component a snippet becomes its
				     prop, and `howMuch` is not one of `ContextMenu`'s. -->
				{#snippet howMuch()}
					{@const under = tallies.get(folder.id)}
					{#if under === undefined}
						<span class="tally">Reading&hellip;</span>
					{:else if under === null}
						<span class="tally">Not known</span>
					{:else}
						<span class="tally">
							<Icon name="article" size={14} />
							{under.file_count.toLocaleString()}
						</span>
						<!-- And the room they take, as the folder's Properties writes it. -->
						<span class="tally">
							<Icon name="data_usage" size={14} />
							{facts.size(under.size_bytes) ?? facts.size(0)}
						</span>
						<span class="tally">
							<Icon name="folder" size={14} />
							{under.folder_count.toLocaleString()}
						</span>
					{/if}
				{/snippet}
				<ContextMenu triggerClass="menu-wrap" label={folder.name}>
					<!--
						HOW MUCH IS IN IT, on the way past: two figures and their glyphs, a label.
					-->
					{#if session.isAdmin}
						<Tooltip
							label=""
							detail={howMuch}
							stretch={view === 'thumbs'}
							placement={view === 'thumbs' ? 'top' : 'right'}
						>
							{@render folderRow(folder)}
						</Tooltip>
					{:else}
						{@render folderRow(folder)}
					{/if}

					{#snippet items()}{@render folderMenu(folder)}{/snippet}
				</ContextMenu>
			</li>
		{/each}
	</ul>
{/snippet}

<!-- The row itself, written once and rendered with or without the label around it. -->
{#snippet folderRow(folder: Folder)}
	<Pressable
		class={shape}
		feedback="wash"
		radius="md"
		onclick={() => goTo(folder.id)}
		onpointermove={() => void tallyFor(folder)}
	>
		{#if view === 'thumbs'}
			<span class="art">
				{#if covers.get(folder.id)}
					<!-- A still that will not load falls back to the folder's glyph. -->
					<img src={covers.get(folder.id)} alt="" onerror={() => covers.set(folder.id, null)} />
				{:else}
					<Icon name="folder" size={20} />
				{/if}
			</span>
		{:else}
			<Icon name="folder" size={18} />
		{/if}
		<!-- The name and its marks on ONE line, so every tile's hover wash is the same box. -->
		<span class="foot">
			<span class="what">{folder.name}</span>
			<SharingMark
				shared={folder.shared}
				restricted={folder.restricted}
				shared_here={folder.shared_here}
				restricted_here={folder.restricted_here}
				hidden={folder.hidden}
				refused={refusedOf(folder)}
			/>
		</span>
	</Pressable>
{/snippet}

<!--
	What can be done to a folder from here, in the parts every menu has, the delete last and alone.
-->
{#snippet folderMenu(folder: Folder)}
	{#if session.isAdmin}
		<!-- Renaming and moving, here where somebody wants them; a library without write access refuses
		     in words. NOT offered on a LIBRARY folder, which nothing here can write to (moving one is the
		     Folders screen's), so the item could only ever refuse. -->
		{#if folder.parent_id !== null}
			<ContextMenuGroup>
				<ContextMenuItem
					icon="drive_file_move"
					label={'Move to\u2026'}
					onselect={() => askToMove(folder)}
				/>
				<ContextMenuItem icon="edit_square" label="Rename" onselect={() => askToRename(folder)} />
				<ContextMenuItem
					icon="edit_square"
					label="Rename files in this folder"
					onselect={() => {
						renamingFiles = folder.id;
						renameFilesOpen = true;
					}}
				/>
			</ContextMenuGroup>
		{/if}
		<!--
			ADD TO, the door every file menu and the selection bar open, from the one declaration.
		-->
		<FileVerbs items={[]} around={{ selection: nothingPicked }}>
			{#snippet children(verbs)}
				{@const door = verbs.menu([]).find((one) => one.id === 'add')}
				{#if door}
					<ContextMenuGroup>
						<VerbMenuItems
							verbs={[
								overFolder(door, folder, () => filesUnder(folder.id), namingFolder(folder.id))
							]}
							ids={[]}
						/>
					</ContextMenuGroup>
				{/if}
			{/snippet}
		</FileVerbs>
		<!-- What may leave this device, then who sees it (`folderVerbs`), a part each. -->
		{#each folderParts(folder) as part (part[0].id)}
			<ContextMenuGroup>
				<VerbMenuItems verbs={part} ids={[folder.id]} />
			</ContextMenuGroup>
		{/each}
	{/if}
	<!-- What the thing IS, and the one row a guest gets. -->
	<ContextMenuGroup>
		<ContextMenuItem icon="info" label="Properties" onselect={() => askAboutProperties(folder)} />
	</ContextMenuGroup>
	{#if session.isAdmin && folder.parent_id !== null}
		<!-- The one row that removes bytes, red, and withheld from a LIBRARY folder as renaming is:
		     "delete" there means stop reading the library, a different act with its own confirmation. -->
		<ContextMenuGroup>
			<ContextMenuItem
				icon="delete"
				label="Delete"
				destructive
				onselect={() => askToDelete(folder)}
			/>
		</ContextMenuGroup>
	{/if}
{/snippet}

<!-- What this folder is: the shared sheet and row list every "here are some facts" surface uses. -->
<Modal
	open={about !== null}
	onOpenChange={(open) => {
		if (!open) about = null;
	}}
	title={about?.name ?? 'This folder'}
	description="What Sift knows about this folder."
	sheetClass="folder-facts"
	scrolls={false}
>
	<DataRows items={aboutFacts} key={(fact) => fact.name} label="What Sift knows about this folder">
		{#snippet row(fact)}
			<DataRow>
				{fact.name}
				{#snippet trailing()}{fact.said}{/snippet}
			</DataRow>
		{/snippet}
	</DataRows>
</Modal>

<FolderSheets bind:this={sheets} />
<BatchRename bind:open={renameFilesOpen} folderId={renamingFiles} />

<!-- One dialog for both: what should this folder be called; the heading says which. -->
<Modal
	open={asking !== null}
	onOpenChange={(open) => {
		if (!open) asking = null;
	}}
	title={asking === 'rename' ? 'Rename this folder' : 'New folder'}
	scrolls={false}
	description={asking === 'rename'
		? 'Sift renames the folder on your disk, and keeps everything it knows about it.'
		: "Sift creates it inside the folder you're in, both on your disk and in your library."}
>
	<form
		onsubmit={(event) => {
			event.preventDefault();
			void settle();
		}}
	>
		<Field label="Name" error={refusal ?? undefined}>
			{#snippet control({ id, describedBy, invalid })}
				<!-- svelte-ignore a11y_autofocus -->
				<TextInput {id} bind:value={typed} {describedBy} {invalid} autofocus />
			{/snippet}
		</Field>
		<div class="buttons">
			<Button onclick={() => (asking = null)}>Cancel</Button>
			<Button
				type="submit"
				tone="primary"
				icon={asking === 'rename' ? 'edit_square' : 'create_new_folder'}
				disabled={library.busy || typed.trim().length === 0}
			>
				{asking === 'rename' ? 'Rename' : 'Create'}
			</Button>
		</div>
	</form>
</Modal>

<!-- The question names the folder AND how much is in it (`ConfirmDialog.consequence`), never "Are you
     sure?", which people learn to click through. -->
<ConfirmDialog
	bind:open={deleteOpen}
	title="Delete {deleting?.name ?? 'this folder'}?"
	consequence={deletingCount === null
		? 'This deletes the folder and everything Sift has indexed inside it from your disk. There is no bin and no way back.'
		: `This deletes ${countOf(deletingCount.file_count, 'file')} and ${countOf(deletingCount.folder_count, 'folder')} from your disk. There is no bin and no way back.`}
	confirmLabel="Delete"
	onconfirm={() => {
		const folder = deleting;
		if (folder) void deleteIt(folder);
	}}
/>

<PickDialog
	bind:open={movingOpen}
	title="Move {subject?.name ?? 'this folder'}"
	subject="Anywhere in the same library. Everything inside it goes too, and Sift keeps what it knows about it."
	choices={destinations}
	confirmLabel={() => 'Move it'}
	onpick={(picked) => void moveTo(picked[0].id)}
/>

<style>
	/* The wrapper the context menu puts around a row is EXACTLY AS WIDE AS THE ROW, or the strip
	 * beside a name would answer for that folder and the ground's New folder could not be
	 * reached. */
	.band :global(.menu-wrap) {
		min-inline-size: 0;
	}

	.thumbs :global(.menu-wrap) {
		flex: 1;
	}

	/* The band's own wrapper, which the background menu hangs on, filling the band; as the WHOLE
	   screen it claims the rest of a filled body (`PageFrame.fillBody`). */
	:global(.band-wrap) {
		display: block;
		flex: 1;
	}

	/* AS A BAND, the ground swallows the frame's own gap under the header (`AssetGrid.beneath`),
	 * or the last 16px above the first tile belonged to nobody: a negative margin hands it back,
	 * padding takes it in, nothing moves. */
	:global(.band-wrap.as-band) {
		margin-block-end: calc(var(--page-gap) * -1);
		padding-block-end: var(--page-gap);
	}

	.band {
		display: grid;
		grid-template-rows: minmax(0, auto);
		/* PADDING, not a margin, so the gap below the folders is inside the menu's trigger. */
		padding-block-end: var(--space-3);
		/* Pulled back by a row's own padding, so a folder's glyph lines up with the title's and
		   the wall's first tile. */
		margin-inline-start: calc(var(--space-2) * -1);
	}

	/* As the whole screen there is nothing below to leave room for, and the frame already scrolls. */
	.band.whole {
		padding-block-end: 0;
	}

	/* A ceiling on the box that SCROLLS, not the list, which has no height to be bounded by. */
	.band:not(.whole) :global(.scroll-root) {
		/* A THIRD of the window: the wall below is what is IN the folder, read after the list. */
		max-block-size: 33vh;
	}

	/* No offset here: it is on `.band`, outside the box that scrolls. */
	.list,
	.columns,
	.thumbs {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.list li,
	.columns li,
	.thumbs li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* THE SAME NAMES, FLOWED DOWN AND THEN ACROSS: what a file manager calls List, filling a
	 * wide window in reading order (an `auto-fill` grid would read across and zig-zag the
	 * alphabet). */
	.columns {
		columns: 240px;
		column-gap: var(--space-6);
	}

	.columns li {
		break-inside: avoid;
	}

	/* The picture size follows the wall's own row height (one slider), in `minmax` columns that
	   fill the row as the wall's tiles do. */
	.thumbs {
		/* The default, here: `style:--folder-art` is dropped when nothing is chosen, and the
		   token gate needs the property defined somewhere other than inline. */
		--folder-art: 180px;
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(var(--folder-art), 1fr));
		gap: var(--space-3);
	}

	.thumbs li {
		flex-direction: column;
		align-items: stretch;
	}

	/* A folder as a row, and as a tile: both `Pressable` surfaces. `:global`, a handed class. */
	.band :global(.row),
	.band :global(.tile) {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
		padding: var(--space-2);
		color: var(--sift-ink);
		font: var(--text-body);
		text-align: start;
	}

	/* A ROW IS AS WIDE AS ITS NAME, as the rail's rows are, so its hover wash and its sharing
	 * mark stay with the name; a long name ellipsises. */
	.band :global(.row) {
		inline-size: fit-content;
		max-inline-size: 100%;
	}

	.band :global(.tile) {
		flex: 1;
		inline-size: 100%;
		flex-direction: column;
		align-items: stretch;
	}

	/* `inline-size` said outright: only a tile with a cover is stretched by its picture. */
	.art {
		display: grid;
		place-items: center;
		inline-size: 100%;
		block-size: var(--folder-art);
		border-radius: var(--radius-md);
		overflow: hidden;
		background: var(--sift-surface-3);
		color: var(--sift-ink-3);
	}

	.art img {
		inline-size: 100%;
		block-size: 100%;
		object-fit: cover;
	}

	/* The name and its marks: the body line is taller than the 16px marks, so a foot with marks
	   is as tall as one without and every hover wash is one size. */
	.foot {
		display: flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
	}

	/* NOT `flex: 1`, which would push the sharing mark away from its folder. */
	.what {
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	/* Wider than a sheet's default, for a full path and a size written twice, which would
	   otherwise wrap under their labels. */
	:global(.folder-facts) {
		--sheet-inline: 34rem;
	}

	/* One figure in the hover label: a snippet carries the scope of the file it is written in,
	   so this reaches the portalled bubble. */
	.tally {
		display: inline-flex;
		align-items: center;
		gap: var(--space-1);
		font: var(--text-data);
		color: var(--sift-ink-2);
	}
</style>
