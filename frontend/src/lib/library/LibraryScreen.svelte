<script lang="ts">
	/* DRESSED BY: .item, .ui-menu (the shared ContextMenu and ContextMenuItem style the rows and the
	   surface this file hands them, with `:global` from their own files). */

	import { onMount, untrack } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import {
		ConfirmDialog,
		ContextMenuItem,
		DataRow,
		DataRows,
		Empty,
		Problem,
		Select,
		Selection,
		SharingMark,
		Skeleton,
		TileGesture,
		Tree,
		VerbMenuItems
	} from '$lib/components/common';
	import ContextMenu from '$lib/components/common/ContextMenu.svelte';
	import ContextMenuGroup from '$lib/components/common/ContextMenuGroup.svelte';
	import EntitySelectionBar from '$lib/components/entity/EntitySelectionBar.svelte';
	import FolderSheets from '$lib/library/FolderSheets.svelte';
	import { folderMarks, folderMenuParts, folderVerbs, refusedOf } from '$lib/library/folder-verbs';
	import { session } from '$lib/shell/session.svelte';
	import type { Verb } from '$lib/components/common/verbs';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import { Library, type Root } from '$lib/library/library.svelte';
	import PathText from '$lib/components/PathText.svelte';
	import type { FolderNode } from '$lib/library/tree';
	import { reloadOnLibraryChange, settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { Picker } from '$lib/library/picker.svelte';
	import { api } from '$lib/api/client';
	import { bridge } from '$lib/bridge';
	import { fetchSettings, saveSettings, type SettingEntry } from '$lib/settings-ui/settings';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { setHidden } from '$lib/library/hiding';
	import { vault } from '$lib/shell/vault.svelte';
	import SettingGroup from '$lib/settings-ui/SettingGroup.svelte';
	import SettingRow from '$lib/settings-ui/SettingRow.svelte';
	import StorageFolders from '$lib/settings-ui/StorageFolders.svelte';
	import AddFolder from '$lib/library/AddFolder.svelte';
	import DownloadFolderOffer from '$lib/library/DownloadFolderOffer.svelte';
	import { Grants } from '$lib/library/grants-state.svelte';
	import { enrichFolder } from '$lib/entity/enrich-many.svelte';

	/* The library: where your files are, and what is in them. */

	/* Which library folders are opened out, by root id. */
	const opened = new SvelteSet<string>();

	/** Open a library folder out, or shut it. What the arrow at the head of the row does, and what
	 *  a press anywhere else on the row does with it. */
	function openOut(id: string) {
		if (opened.has(id)) opened.delete(id);
		else opened.add(id);
	}

	/* A press on a folder OPENS IT OUT: the root rows here and the subfolders in the tree alike. */

	const library = new Library();
	const picker = new Picker();
	const grants = new Grants();
	grants.follow();

	/* The Add button is always offered here: the picker can walk the server's own drives, so
	 * anybody who may manage the library has somewhere to go from here, whichever device they
	 * are sitting at, and there is no case where the button is a dead end. */

	let removing = $state<Root | null>(null);
	let removeOpen = $state(false);

	/* And again whenever a share or a restrict moves. */
	reloadOnLibraryChange(() => void library.load());

	/* Adding a folder (the button and its dialog) is `AddFolder`, shared with the empty Browse
	   wall of an install that has no folders yet. */

	/* A library folder that is not where Sift last saw it. */
	async function askWhereItMoved(root: Root) {
		if (!grants.canAdd) {
			toasts.show(
				[
					'Open Sift on the computer holding ',
					library.named(root.id, root.name),
					' to tell it where the folder is now'
				],
				{ tone: 'info' }
			);
			return;
		}
		const chosen = await bridge.chooseFolder();
		// Closing the dialog without choosing is not an error and must not draw one.
		if (chosen === null) return;
		await grants.ensure(chosen);
		const refusal = await library.moved(root, chosen);
		if (refusal) toasts.show(refusal, { tone: 'error' });
	}

	onMount(async () => {
		// The roots first, then the picker, and only if this account may manage the library.
		await library.load();
		if (library.canManage) await picker.open();
	});

	/* What the local badge says, and it names the COMPUTER where the machine will say what it is
	 * called. */
	function localLabel(machine: string | null | undefined): string {
		return machine ? `On ${machine}` : 'On a disk in this device';
	}

	function askToRemove(root: Root) {
		removing = root;
		removeOpen = true;
	}

	async function confirmRemove() {
		if (removing) await library.removeRoot(removing);
		removing = null;
		/* The folder's grant goes with it on the server, once nothing else uses it, so the
		   picker is read again to stop offering it. */
		if (library.canManage) await picker.open();
	}

	/* Picking several folders together. Sharing or hiding a run of folders is the commonest
	 * thing anybody wants to do to several things together here, so the tree selects like every
	 * other surface. */
	const selection = new Selection();

	/** Every folder on screen, in the order the tree draws them. What a run is resolved against. */
	function drawn(): string[] {
		const walk = (nodes: FolderNode[]): string[] =>
			nodes.flatMap((node) => [node.id, ...walk(node.children ?? [])]);
		return walk(library.tree);
	}

	/* Escape lets go, and Ctrl+Z takes back the last row that was picked. */
	const gesture = new TileGesture(selection, drawn);

	function letGo(event: KeyboardEvent) {
		if (gesture.undoKeys(event)) {
			event.preventDefault();
			event.stopPropagation();
			return;
		}
		if (event.key !== 'Escape') return;
		if (gesture.escaped(event)) event.stopPropagation();
	}

	/* Anything that leaves the tree leaves the selection with it, or the bar goes on counting
	   rows nobody can see. */
	$effect(() => {
		const here = drawn();
		untrack(() => selection.retain(here));
	});

	function shareFolders(ids: string[]) {
		sheets?.share(
			ids
				.map((id) => library.folder(id))
				.filter((folder): folder is NonNullable<ReturnType<typeof library.folder>> => !!folder)
		);
	}

	/* Hiding a whole selection: ONE call, so one sentence afterwards. */
	async function hideFolders(ids: string[], wanted: boolean) {
		const moved = await setHidden(ids, wanted, {
			noun: 'folder',
			stays: vault.unlocked,
			set: (each, vault) => api.put(`/folders/${each}/vault`, { body: { vault } })
		});
		selection.clear();
		if (moved.length > 0) await library.load();
	}

	/* The Share and Visibility sheets the folder menu, a row's own sharing mark, or the bar
	 * opens. */
	let sheets = $state<FolderSheets | null>(null);

	function askToShare(id: string, name: string) {
		sheets?.share([{ id, name }]);
	}

	function askAboutVisibility(id: string, name: string) {
		sheets?.visibility({ id, name });
	}

	/* A tree folder's own rows, a part each: what may leave this device, then who sees it. */
	function treeParts(id: string, name: string): Verb[][] {
		const handlers = {
			hide: (hidden: boolean) => void hideFolder(id, hidden),
			share: () => askToShare(id, name),
			visibility: () => askAboutVisibility(id, name)
		};
		const marks = folderMarks(library.folder(id) ?? { id }, () => void library.load());
		const context = { isAdmin: session.isAdmin, hidden: hiddenFolders.has(id), handlers };
		return folderMenuParts(context, marks);
	}

	/* The top folder of each root, by root. */
	/** What is inside one library folder, as the tree draws it: the children of its own top row. */
	function insideRoot(rootId: string): FolderNode[] {
		const top = topFolders.get(rootId);
		if (!top) return [];
		return library.tree.find((node) => node.id === top.id)?.children ?? [];
	}

	/* Which root each folder belongs to. */
	const rootOfFolder = $derived(
		new Map(library.folders.map((folder) => [folder.id, folder.root_id]))
	);

	/* Everything a library folder can be told, declared once. */
	/* What the mark on a folder on another machine says. */
	const NAS_SAYS = {
		here: 'On a network drive',
		silent: "The drive isn't answering right now. Sift keeps what it knows and tries again.",
		missing: "The drive is there, but this folder isn't on it any more."
	} as const;

	function nasSays(root: Root): string {
		if (root.reachable !== false) return NAS_SAYS.here;
		return root.presence === 'missing' ? NAS_SAYS.missing : NAS_SAYS.silent;
	}

	function rootVerbs(root: Root): Verb[] {
		const verbs: Verb[] = [];
		/* About THIS folder, the way a file's own Run task is about the files picked, so it
		   stays here rather than going to Tasks, and it means what the Scan task's Run now
		   means: read the files and stop. */
		verbs.push({
			id: 'scan',
			label: 'Scan now',
			icon: 'split_scene',
			group: 'change',
			run: () => library.rescan(root, { scanOnly: true })
		});
		// Where downloads go is not here, and that is deliberate.
		if (root.reachable === false) {
			verbs.push({
				id: 'moved',
				label: 'It moved',
				icon: 'drive_file_move',
				group: 'change',
				run: () => askWhereItMoved(root)
			});
		}
		/* The two this row offers that a menu over the name alone would. */
		const folder = topFolders.get(root.id);
		if (session.isAdmin && folder) {
			verbs.push({
				id: 'enrich',
				label: 'Auto-enrich this folder',
				icon: 'auto_fix_high',
				group: 'enrich',
				run: () => void enrichFolder(folder.id, root.name)
			});
			/* Who sees it and what may leave this device, the rows every folder menu draws,
			   about the top folder as above. */
			const marks = folderMarks(folder, () => void library.load());
			verbs.push(
				...folderVerbs({
					isAdmin: session.isAdmin,
					hidden: hiddenFolders.has(folder.id),
					shareLabel: 'Sharing',
					keptLocal: marks.keptLocal,
					keptFromSwaps: marks.keptFromSwaps,
					handlers: {
						hide: (hidden) => void hideFolder(folder.id, hidden),
						share: () => askToShare(folder.id, root.name),
						visibility: () => askAboutVisibility(folder.id, root.name),
						keepLocal: marks.keepLocal,
						keepFromSwaps: marks.keepFromSwaps
					}
				})
			);
		}
		verbs.push({
			id: 'remove',
			label: 'Remove',
			icon: 'delete',
			filled: true,
			destructive: true,
			run: () => askToRemove(root)
		});
		return verbs.map((verb) => ({ ...verb, disabled: library.busy }));
	}

	const topFolders = $derived(
		new Map(
			library.folders
				.filter((folder) => folder.parent_id === null)
				.map((folder) => [folder.root_id, folder])
		)
	);

	/* Which folders are already in the vault, so the row offers the way out rather than the way
	 * in. */
	const hiddenFolders = $derived(
		new Set(library.folders.filter((folder) => folder.hidden).map((folder) => folder.id))
	);

	/* The preferences registered into the "Library" section, and writing one back. */
	const PREFERENCES_SECTION = 'Library';
	let preferences = $state<SettingEntry[]>([]);

	/* The headings, and which settings sit under each. */
	const HEADED: { heading: string; keys: string[] }[] = [];

	/* Everything registered into this section that no heading above claims. */
	const claimed = HEADED.flatMap((one) => one.keys);
	const unheaded = $derived(preferences.filter((one) => !claimed.includes(one.key)));

	onMount(() => {
		void loadPreferences();
	});

	/* And again when a setting moves somewhere else: this account in a browser, a second window,
	 * or another admin changing one the installation shares. */
	whenChanged(settingChanges, () => void loadPreferences());

	async function loadPreferences() {
		try {
			const sections = await fetchSettings();
			// Named rather than chained, so the "this pane draws whatever the server declares"
			// claim is one a checker can see: the build looks for a section name beside a read of
			// that section's settings list, and a chain would hide the second half, leaving every
			// setting registered into Library reading as one nothing on any screen could reach.
			const section = sections.find((one) => one.name === PREFERENCES_SECTION);
			preferences = section?.settings ?? [];
		} catch {
			preferences = [];
		}
	}

	async function savePreference(key: string, value: unknown) {
		const previous = preferences;
		// Optimistic, and put back only if the server refuses: the same shape every other pane uses.
		preferences = preferences.map((one) => (one.key === key ? { ...one, value } : one));
		try {
			await saveSettings({ [key]: value });
		} catch {
			preferences = previous;
			toasts.show("That setting couldn't be saved", { tone: 'error' });
		}
	}

	async function hideFolder(id: string, hide: boolean) {
		const moved = await setHidden([id], hide, {
			noun: 'folder',
			stays: vault.unlocked,
			set: (each, vault) => api.put(`/folders/${each}/vault`, { body: { vault } })
		});
		// The tree is what changed: a hidden folder leaves it, and everything under it goes too.
		if (moved.length > 0) await library.load();
	}
</script>

<svelte:head><title>Library - Sift</title></svelte:head>

<!--
	The first folder can be added here as well as on Browse's empty wall, so the offer of where
	downloads go is mounted on both: it shows once, when the first folder lands.
-->
<DownloadFolderOffer {library} />

{#if library.failed}
	<Problem message={library.failed} />
{:else if library.loading}
	<Skeleton lines={3} />
{:else}
	{#if library.canManage}
		<section aria-label="Your folders">
			<!-- ONE LIST. -->
			<!-- A button, not a form. -->
			<div class="add-folder">
				<AddFolder {library} {grants} {picker} />
			</div>
			<DataRows items={library.roots} key={(root: Root) => root.id} label="Library folders" edges>
				{#snippet row(root: Root)}
					<DataRow
						verbs={rootVerbs(root)}
						ids={[root.id]}
						menuLabel={`More for ${root.name}`}
						onpress={() => openOut(root.id)}
						expanded={opened.has(root.id)}
						ontoggle={() => openOut(root.id)}
						toggleLabel="what is inside {root.name}"
					>
						{#snippet expansion()}
							{#if opened.has(root.id)}
								<div class="inside">
									{#if insideRoot(root.id).length === 0}
										<Empty scope="block">No folders inside this one.</Empty>
									{:else}
										<!--
											No dragging here, deliberately. Dragging a folder here to
											move the real files on the disk would be a gesture nobody
											could find, on a screen nobody would look for it on.
										-->
										<Tree
											nodes={insideRoot(root.id)}
											label="Folders in {root.name}"
											rowMenu={session.isAdmin ? folderMenu : undefined}
											onsharing={session.isAdmin ? askToShare : undefined}
											{selection}
										/>
									{/if}
								</div>
							{/if}
						{/snippet}

						<!--
							Right-click a library folder and get the same two verbs its subfolders
							have.
						-->
						<!-- What is inside, one press away, behind the row's arrow, which
						     `DataRow` draws at the end of the row, pointing down, where every
						     list that folds draws it. This is the folder tree, under the row it
						     is about, rather than a second list further down the page. -->
						<span class="subject">
							<Icon name="folder" />
							<span class="titles">
								<span class="name">
									<span class="name-words">{root.name}</span>
									<!--
										The same mark the tree draws one level down, on the same
										folder.
									-->
									<SharingMark
										shared={root.shared}
										restricted={root.restricted}
										shared_here={root.shared_here}
										restricted_here={root.restricted_here}
										hidden={root.vault}
										refused={topFolders.has(root.id)
											? refusedOf(topFolders.get(root.id)!)
											: undefined}
										onopen={session.isAdmin && topFolders.has(root.id)
											? () => askToShare(topFolders.get(root.id)!.id, root.name)
											: undefined}
									/>
								</span>
								<span class="kind"><PathText path={root.path} /></span>
							</span>
						</span>

						{#snippet trailing()}
							<span class="marks">
								{#if root.kind === 'nas'}
									<!--
										Two states, because the server proves two: the folder answers,
										or it does not.
									-->
									<Tooltip label={nasSays(root)}>
										<span
											class="badge"
											class:down={root.reachable === false}
											role="img"
											aria-label={nasSays(root)}
										>
											<Icon name="lan" size={14} />
										</span>
									</Tooltip>
								{:else if root.reachable === false}
									<Tooltip
										label="Sift can't see this folder. Check that the disk is still attached."
									>
										<span
											class="badge down"
											role="img"
											aria-label="Sift can't see this folder. Check that the disk is still attached."
										>
											<Icon name="warning" size={14} />
										</span>
									</Tooltip>
								{:else}
									<!--
										WHICH disk in this computer, where the machine will say, and
										that it is one of them where it will not.
									-->
									<Tooltip label={localLabel(root.machine)}>
										<span class="badge local" role="img" aria-label={localLabel(root.machine)}>
											<Icon name="hard_disk" size={14} />
										</span>
									</Tooltip>
								{/if}
							</span>
						{/snippet}
					</DataRow>
				{/snippet}
			</DataRows>

			{#if library.roots.length === 0}
				<Empty scope="block">No folders yet. Add one and Sift will read what is in it.</Empty>
			{/if}
		</section>
	{/if}

	<!--
		The preferences registered into this section, drawn through the row like everywhere else.
	-->
	{#each HEADED as block (block.heading)}
		{@const rows = preferences.filter((one) => block.keys.includes(one.key))}
		{#if rows.length > 0}
			<SettingGroup heading={block.heading}>
				{#each rows as entry (entry.key)}
					<SettingRow
						{entry}
						value={entry.value}
						onchange={(next: unknown) => void savePreference(entry.key, next)}
					/>
				{/each}
			</SettingGroup>
		{/if}
	{/each}

	{#if unheaded.length > 0}
		<SettingGroup>
			{#each unheaded as entry (entry.key)}
				<SettingRow
					{entry}
					value={entry.value}
					onchange={(next: unknown) => void savePreference(entry.key, next)}
				/>
			{/each}
		</SettingGroup>
	{/if}
{/if}

<!-- Sift's OWN folders, under the ones it can see. -->
<StorageFolders />

{#snippet folderMenu(id: string, name: string)}
	<!-- Asking about what is in this folder: the stash-boxes, or the disk again. -->
	<ContextMenuGroup>
		<!--
			A folder is the obvious scope for asking the catalogs: asking about everything instead, to
			learn about the folder you just filled, is minutes of asking about files settled weeks ago
			on a large library.
		-->
		{#if session.isAdmin}
			<ContextMenuItem
				icon="auto_fix_high"
				label="Auto-enrich this folder"
				onselect={() => void enrichFolder(id, name)}
			/>
		{/if}
		<!-- Scanning ONE folder. -->
		<ContextMenuItem
			icon="sync"
			label="Scan this folder"
			onselect={() => void library.rescanFolder(rootOfFolder.get(id), id, name)}
		/>
	</ContextMenuGroup>
	<!--
		Who sees it. Sharing is about which account and hiding is about nobody, but both answer who
		sees this folder, the part every menu in Sift keeps together.
	-->
	{#each treeParts(id, name) as part (part[0].id)}
		<ContextMenuGroup>
			<VerbMenuItems verbs={part} ids={[id]} />
		</ContextMenuGroup>
	{/each}
{/snippet}

<!-- Escape lets go, and Ctrl+Z takes back the last folder that was picked. -->
<svelte:window onkeydown={letGo} />

<!-- The same bar every other wall has, over the folders that are picked. -->
<EntitySelectionBar
	{selection}
	order={drawn}
	noun="folder"
	handlers={{
		share: session.isAdmin ? shareFolders : undefined,
		hide: hideFolders
	}}
/>

<FolderSheets bind:this={sheets} onapplied={() => selection.clear()} />

<ConfirmDialog
	bind:open={removeOpen}
	title={removing ? `Remove ${removing.name} from Sift?` : 'Remove this folder?'}
	consequence="Sift forgets what is in it. Your files aren't touched, not moved and not deleted. Add the folder again within thirty days and everything comes back, tags and all; after that, Maintenance may throw the records away."
	confirmLabel="Remove"
	destructive={false}
	onconfirm={confirmRemove}
/>

<style>
	section {
		margin-block-end: var(--space-8);
	}

	.subject {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		min-inline-size: 0;
	}

	/* Indented under the row it belongs to, and quieter than it: this is what is inside that
	   folder rather than another list of libraries. */
	.inside {
		margin-inline-start: var(--space-6);
		margin-block-end: var(--space-3);
		padding-inline-start: var(--space-3);
		border-inline-start: 1px solid var(--sift-line);
	}

	.titles {
		display: flex;
		flex-direction: column;
		min-inline-size: 0;
	}

	.name {
		display: inline-flex;
		align-items: center;
		gap: var(--space-2);
		min-inline-size: 0;
		font: var(--text-body);
		color: var(--sift-ink);
	}

	/* A folder's name and its path end in an ellipsis where the row runs out, never cut through
	   a letter: the subject clips, and `text-overflow` on it reaches only its own text, not
	   these lines inside it. */
	.name-words,
	.kind {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.name-words {
		min-inline-size: 0;
	}

	.kind {
		font: var(--text-micro);
		color: var(--sift-ink-3);
	}

	.marks {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* Clear of the panel above it. Without this the button's border would meet the panel's
	   border and the two read as one control. */
	.add-folder {
		margin-block-end: var(--space-4);
	}

	/* Only ever drawn on a folder that is somewhere else, so it is a mark rather than a label. */
	/* Round, so it reads as a lamp rather than as another button in a row that already has one. */
	/* 1.5rem, which is 1.75 less fifteen per cent, with a 14px glyph in it. */
	.badge {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		inline-size: 1.5rem;
		block-size: 1.5rem;
		border-radius: var(--radius-full);
		background: var(--sift-ok-bg);
		color: var(--sift-ok);
	}

	/* On a disk in this device. Quieter than the network's green, because it is the ordinary
	   case: it is here to be READ, not to be noticed. */
	.badge.local {
		background: var(--sift-surface-4);
		color: var(--sift-ink-2);
	}

	.badge.down {
		background: var(--sift-bad-bg);
		color: var(--sift-bad-text);
	}
</style>
