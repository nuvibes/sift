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

	/* The library: where your files are, and what is in them.
	 *
	 * Two halves, for two different people. The settings are an admin's (adding a folder is
	 * saying where somebody's life is kept), and the tree is for whoever is looking, scoped by the
	 * server to what they may see. Not drawing the settings for a guest is tidiness and nothing
	 * more: every endpoint behind them refuses on its own, and would refuse a request this screen
	 * never made.
	 */

	/* Which library folders are opened out, by root id.
	 *
	 * A root row opens to show what is inside it, and everything a separate tree could do is done
	 * there: a second list further down naming the same folders (one row to watch, manage and
	 * remove, another to share, hide and drag, neither saying the other existed) would name
	 * them twice on one screen.
	 *
	 * A `SvelteSet`, not a plain one: a plain Set held in `$state` is not deeply reactive, so
	 * adding to it changes nothing on screen.
	 */
	const opened = new SvelteSet<string>();

	/** Open a library folder out, or shut it. What the arrow at the head of the row does, and what
	 *  a press anywhere else on the row does with it. */
	function openOut(id: string) {
		if (opened.has(id)) opened.delete(id);
		else opened.add(id);
	}

	/*
	 * A press on a folder OPENS IT OUT: the root rows here and the subfolders in the tree alike.
	 *
	 * Not an address: Settings is reached as a PANEL over whatever screen somebody was on, and a
	 * navigation unmounts the panel, so a click in the tree that navigated would shut the thing
	 * the tree was drawn in and drop the person onto a page they did not ask for.
	 *
	 * Not the row's verb menu either. The row already has two doors to that menu (the right
	 * button and the dots), and making the press a third would put the one thing a folder row
	 * obviously does behind the smallest target on it. The menu keeps the right button and the
	 * dots; the press, and Enter, open the folder.
	 */

	const library = new Library();
	const picker = new Picker();
	const grants = new Grants();
	grants.follow();

	/* The Add button is always offered here: the picker can walk the server's own drives, so
	 * anybody who may manage the library has somewhere to go from here, whichever device they are
	 * sitting at, and there is no case where the button is a dead end. The section this sits in
	 * is behind `library.canManage`, which is the check that matters.
	 */

	let removing = $state<Root | null>(null);
	let removeOpen = $state(false);

	/* And again whenever a share or a restrict moves.
	 *
	 * The tree draws a mark on every folder, and the marks are part of the answer the folder
	 * list gives, so sharing a folder from its own right-click menu changes rows that are already
	 * on screen. Without this the badge would appear at the next load, which on a settings panel means
	 * closing it and opening it again. See the helper; the four entity walls do the same. */
	reloadOnLibraryChange(() => void library.load());

	/* Adding a folder (the button and its dialog) is `AddFolder`, shared with the empty Browse
	   wall of an install that has no folders yet. The instances are this screen's, so the list
	   below shows a folder the moment it is added. */

	/*
	 * A library folder that is not where Sift last saw it.
	 *
	 * A folder INSIDE a library is recognised on the next walk, from what is inside it, so this is
	 * only ever offered for the library folder itself, which Sift is pointed at by its path and so
	 * is not looking anywhere near the new name of.
	 *
	 * Through the machine's own dialog where there is one, exactly as adding a folder is: it is the
	 * consent as well as the choice, and it can reach a folder that has never been handed over,
	 * which is the whole case here, since the folder has just moved somewhere new.
	 */
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
		// Browsing is admin-only, so asking for it as a guest is a request that is certain to be
		// refused, and a refusal in the log of everyone who ever opens this page is noise that
		// looks exactly like the real thing going wrong.
		await library.load();
		if (library.canManage) await picker.open();
	});

	/*
	 * What the local badge says, and it names the COMPUTER where the machine will say what it is
	 * called.
	 *
	 * "On a disk in this device" has a word in it that depends on who is reading. The desktop
	 * client on another PC draws this same row, and there "this computer" is not the reader's
	 * computer at all. The name settles it.
	 *
	 * Not the DISK: the folder's full path is on the row already, drive letter and all, so naming
	 * the disk answers a question the row already answers. Which machine is the fact that is
	 * nowhere else on the row.
	 *
	 * One function rather than the same conditional written in the tooltip and again in the
	 * accessible label: those two must say the same thing, and a badge whose spoken name disagrees
	 * with its tooltip is exactly the kind of drift nobody sees until somebody cannot see the
	 * screen. `machine` is absent where the name says nothing (a container, `localhost`), which
	 * is what the plain sentence is for; see `machine_name` on the server.
	 */
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
		/* The folder's grant goes with it on the server, once nothing else uses it, so the picker
		   is read again to stop offering it. */
		if (library.canManage) await picker.open();
	}

	/*
	 * Picking several folders at once.
	 *
	 * Sharing or hiding a run of folders is the commonest thing anybody wants to do to several
	 * things at once here, so the tree selects like every other surface. The gestures and the bar
	 * are the shared ones (see `Selection` and `EntitySelectionBar`), so a folder behaves like a
	 * person, a tag or a file.
	 */
	const selection = new Selection();

	/** Every folder on screen, in the order the tree draws them. What a run is resolved against. */
	function drawn(): string[] {
		const walk = (nodes: FolderNode[]): string[] =>
			nodes.flatMap((node) => [node.id, ...walk(node.children ?? [])]);
		return walk(library.tree);
	}

	/* Escape lets go, and Ctrl+Z takes back the last row that was picked. The same keys, through
	   the same shared rules, as every other screen that can select. */
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

	/* Anything that leaves the tree leaves the selection with it, or the bar goes on counting rows
	   nobody can see. */
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

	/* Hiding a whole selection: ONE call, so one sentence afterwards.
	 *
	 * The helper takes a list and says what happened once. Called per folder it would raise the
	 * same message five times for five folders, which is the shape the shared helper exists to
	 * stop, and is why the file grid and the four walls all hand it the whole set. */
	async function hideFolders(ids: string[], wanted: boolean) {
		const moved = await setHidden(ids, wanted, {
			noun: 'folder',
			stays: vault.unlocked,
			set: (each, vault) => api.put(`/folders/${each}/vault`, { body: { vault } })
		});
		selection.clear();
		if (moved.length > 0) await library.load();
	}

	/* The Share and Visibility sheets the folder menu, a row's own sharing mark, or the bar opens.
	 *
	 * Share takes a LIST, because the bar shares a whole selection: picking five folders and
	 * pressing Share must never silently share the first. One row is a list of one. */
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

	/* The top folder of each root, by root.
	 *
	 * A root row's mark is the mark of the folder directly under it (the server reads it there),
	 * so pressing the mark has to open the panel on THAT folder rather than on the root. Opening it
	 * on a different object would explain a different answer from the one being pointed at. */
	/** What is inside one library folder, as the tree draws it: the children of its own top row. */
	function insideRoot(rootId: string): FolderNode[] {
		const top = topFolders.get(rootId);
		if (!top) return [];
		return library.tree.find((node) => node.id === top.id)?.children ?? [];
	}

	/* Which root each folder belongs to.
	 *
	 * The tree is drawn per root, so the root IS known where a row is rendered, but `Tree`'s row
	 * menu is handed an id and a label and nothing else, and widening that contract for one verb
	 * would change every tree in the application. The folder rows already carry their root, so the
	 * answer is a lookup rather than a new argument.
	 */
	const rootOfFolder = $derived(
		new Map(library.folders.map((folder) => [folder.id, folder.root_id]))
	);

	/*
	 * Everything a library folder can be told, declared once.
	 *
	 * Declared, the rows reach the three-dot button AND the right-click on the row itself, with
	 * icons, in the menu's groups, the one that removes something last below its own line, and
	 * nothing can drift from the row's other affordances, which is what every surface with verbs
	 * owes.
	 *
	 * Availability is decided here rather than by which rows happen to be written: sharing needs an
	 * admin and a top folder to hang the grant on, and "It moved" is only a thing to say about a
	 * folder that is not answering.
	 */
	/* What the mark on a folder on another machine says. One sentence per state the server can
	   prove: the folder answered; the drive or share did not; or the drive answered and the folder
	   is not on it any more. */
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
		   means: read the files and stop. What follows a scan (the pictures, the faces) is each
		   stage's own When; two buttons called "Scan now" must not do two different things. */
		verbs.push({
			id: 'scan',
			label: 'Scan now',
			icon: 'split_scene',
			group: 'change',
			run: () => library.rescan(root, { scanOnly: true })
		});
		// Where downloads go is not here, and that is deliberate. A flag on this row would choose a
		// whole library, so a folder inside one could never be the answer, and a second control
		// elsewhere naming any folder would quietly win. One control, in Downloads, where somebody
		// is when they need it.
		if (root.reachable === false) {
			verbs.push({
				id: 'moved',
				label: 'It moved',
				icon: 'drive_file_move',
				group: 'change',
				run: () => askWhereItMoved(root)
			});
		}
		/*
		 * The two this row offers that a menu over the name alone would.
		 *
		 * One menu per row: a menu over the name and another over everything else, offering
		 * different things, would make what you got depend on where in the row you pressed: the
		 * exact drift a declared list exists to stop.
		 *
		 * Both are the top folder's rather than the root's, which is the same distinction the
		 * sharing row above makes: a grant, a scan and an enrich are all written one level down,
		 * where the server reads them.
		 */
		const folder = topFolders.get(root.id);
		if (session.isAdmin && folder) {
			verbs.push({
				id: 'enrich',
				label: 'Auto-enrich this folder',
				icon: 'auto_fix_high',
				group: 'enrich',
				run: () => void enrichFolder(folder.id, root.name)
			});
			/* Who sees it and what may leave this device, the rows every folder menu draws, about the
			   top folder as above. */
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

	/* Which folders are already in the vault, so the row offers the way out rather than the way in.
	 *
	 * A hidden folder is only in this list at all while the vault is open (concealed, the server
	 * does not send it), so the set is empty on an ordinary visit and the menu says Hide. */
	const hiddenFolders = $derived(
		new Set(library.folders.filter((folder) => folder.hidden).map((folder) => folder.id))
	);

	/*
	 * The preferences registered into the "Library" section, and writing one back.
	 *
	 * Read from the same declared registry the generated panes read, so a setting registered by any
	 * feature turns up here without this file learning about it. Failure is silent: a screen about
	 * folders should not report a fault because a preference could not be read, and the block
	 * simply does not appear.
	 */
	const PREFERENCES_SECTION = 'Library';
	let preferences = $state<SettingEntry[]>([]);

	/* The headings, and which settings sit under each.
	 *
	 * A block whose keys are all absent is not drawn, so this list may name a setting that has not
	 * been registered yet without leaving an empty heading on screen.
	 */
	const HEADED: { heading: string; keys: string[] }[] = [];

	/* Everything registered into this section that no heading above claims.
	 *
	 * The reason this pane can go on drawing the whole section rather than a hand-picked list: a
	 * setting nobody thought about still reaches the screen, under no heading, instead of being
	 * stored and read and impossible to press.
	 */
	const claimed = HEADED.flatMap((one) => one.keys);
	const unheaded = $derived(preferences.filter((one) => !claimed.includes(one.key)));

	onMount(() => {
		void loadPreferences();
	});

	/* And again when a setting moves somewhere else: this account in a browser, a second
	 * window, or another admin changing one the installation shares.
	 *
	 * Each control here writes on the press and holds nothing unsaved, so a re-read can only ever
	 * put the same value back, while not following would leave the pane showing a value that
	 * was not true any more, with nothing on screen to say why. The settings-followed gate holds
	 * it.
	 */
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
	downloads go is mounted on both: it shows once, when the first folder lands. Outside the
	branches below, because a read that fails draws the problem in the list's place, and a
	component inside them would be made again and forget that it saw none.
-->
<DownloadFolderOffer {library} />

{#if library.failed}
	<Problem message={library.failed} />
{:else if library.loading}
	<Skeleton lines={3} />
{:else}
	{#if library.canManage}
		<section aria-label="Your folders">
			<!--
				ONE LIST. The folders Sift may look in follow what uses them: a folder chosen for a
				library (or, on the server, the backup folder) is handed over as it is chosen, and
				given back when the last thing using it lets go. So there is no second list of the
				same folders under another name; the Add a folder dialog shows the ones Sift already
				has at its top level.
			-->
			<!--
				A button, not a form. Adding a library is done once and then almost never again, and
				a block about five hundred pixels tall standing open over the list would push the
				thing this screen is actually for, the folders you already have, off the screen.
				At the TOP, above the list, so it is in the same place however many folders there
				are: under a long list it would be a scroll away.
			-->
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
											No dragging here, deliberately. Dragging a folder here to move the real
											files on the disk would be a gesture nobody could find, on a screen nobody
											would look for it on. Settings is where a library is set up; moving files around inside
											one is a job for the grid, where the files are.
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

							A library folder is a folder like any other as far as sharing is
							concerned (the grant is written on the row one level down, which is
							where the server reads it), and the badge beside the name is drawn only
							when there is something to say, so without this the folders somebody
							most wants to share, the top-level ones, would be the only folders with
							no menu at all.
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
									<!-- The same mark the tree draws one level down, on the same folder. It is
									     here as well as there, not instead: this list holds only the folders
									     somebody added, and a share made three levels in has no row here at
									     all. Pressing it opens the panel on the folder the mark is about. -->
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
									<!-- Two states, because the server proves two: the folder answers,
									     or it does not. Not answering covers a drive that is off and a
									     folder gone from a drive that is on, so the sentence claims
									     neither. -->
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

										"On a disk in this device" is true of every local folder,
										so on a machine with two disks it is a sentence with no
										information in it. The volume's own name is the fact
										somebody with two disks is actually looking for, and it
										comes off the filesystem rather than being asked for. See
										`volume_of`; it answers None where the system will not say,
										and the plain sentence is what None draws.

										On a disk in this device, said rather than left to be
										inferred. Where a folder lives is not news, it is a property
										of the folder, and with a mark on one kind and nothing on
										the other, "on this computer" would be a fact you had to
										work out from the absence of a badge, which is not something
										a person reads.

										The pair says it: one glyph for the network, one for a local
										disk, and every row carries exactly one of them.
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

		The row picks the control from what a setting declares, so a number cannot come out as a
		switch that reads as off and can only be refused when pressed: the fault of a block that
		chooses a control for every setting in it.

		It draws the whole section rather than a list of named keys, and that is deliberate: naming
		keys here is how a setting registered into this section would become invisible, with a
		dialog elsewhere telling people to turn it off "in Settings, under Library" and nothing
		there to turn off.

		Nothing may be registered into this section at a given moment, and then this draws nothing.
		It stays, because it is the safety net: the next setting registered here reaches the screen
		without this file learning about it. The headings list is empty for the same reason; a
		heading that names a key nobody registers here is a guess.
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

<!--
	Sift's OWN folders, under the ones it can see.

	Here rather than on a pane of its own: this screen is already the answer to "where are things",
	and somebody looking for where the database lives looks under Folders. It draws nothing at all
	in a browser or in client mode. See the component, which asks the shell rather than guessing.
-->
<StorageFolders />

{#snippet folderMenu(id: string, name: string)}
	<!--
		Asking about what is in this folder: the stash-boxes, or the disk again. A pair, so one group:
		one reads the disk again, the other asks somebody else's stash-box what the files it already
		knows about are. They are named apart for that reason: one word for both would have a menu
		item and a settings button quietly doing different things to different machines.
	-->
	<ContextMenuGroup>
		<!--
			A folder is the obvious scope for asking the catalogs: asking about everything instead, to
			learn about the folder you just filled, is minutes of asking about files settled weeks ago
			on a large library.

			Admin only, and the same reason the wall's row menu gives: what a stash-box fills in is
			shared vocabulary, and asking sends names to somebody else's service.
		-->
		{#if session.isAdmin}
			<ContextMenuItem
				icon="auto_fix_high"
				label="Auto-enrich this folder"
				onselect={() => void enrichFolder(id, name)}
			/>
		{/if}
		<!--
			Scanning ONE folder. The job takes a folder and narrows both halves of its work to that
			subtree: it is what the watcher asks for when a single file lands, so picking up the one
			folder somebody just put something in is not minutes of walking the whole root.
		-->
		<ContextMenuItem
			icon="sync"
			label="Scan this folder"
			onselect={() => void library.rescanFolder(rootOfFolder.get(id), id, name)}
		/>
	</ContextMenuGroup>
	<!--
		Who sees it. Sharing is about which account and hiding is about nobody, but both answer who
		sees this folder, the part every menu in Sift keeps together. A share on a folder reaches
		everything inside it, including what arrives later. Which way hiding reads comes from the
		row: a folder is only ever listed while it is hidden if the vault is already open.
		Alphabetical, so Hide files before Share, and Unhide and Visibility after it. Before it, its
		own part: what may leave this device, Don't enrich and Don't swap.
	-->
	{#each treeParts(id, name) as part (part[0].id)}
		<ContextMenuGroup>
			<VerbMenuItems verbs={part} ids={[id]} />
		</ContextMenuGroup>
	{/each}
{/snippet}

<!-- Escape lets go, and Ctrl+Z takes back the last folder that was picked. On the window because a
     selection is made with the mouse and the keyboard focus is usually nowhere near the tree. -->
<svelte:window onkeydown={letGo} />

<!-- The same bar every other wall has, over the folders that are picked. Hiding is every
     account's; sharing and the rest are an admin's, and the bar leaves out what it was handed no
     function for. -->
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

	/* Indented under the row it belongs to, and quieter than it: this is what is inside that folder
	   rather than another list of libraries. */
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

	/* A folder's name and its path end in an ellipsis where the row runs out, never cut through a
	   letter: the subject clips, and `text-overflow` on it reaches only its own text, not these
	   lines inside it. */
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

	/* Clear of the panel above it. Without this the button's border would meet the panel's border
	   and the two read as one control. */
	.add-folder {
		margin-block-end: var(--space-4);
	}

	/* Only ever drawn on a folder that is somewhere else, so it is a mark rather than a label. */
	/* Round, so it reads as a lamp rather than as another button in a row that already has one. */
	/* 1.5rem, which is 1.75 less fifteen per cent, with a 14px glyph in it. Any bigger and this
	   is the heaviest thing in a row whose actual controls are a switch and a menu, and it is
	   a fact you glance at, not something you press. */
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
	   case: it is here to be READ, not to be noticed.

	   The SECOND ink, not the caption ink. The caption ink is 3.94:1 on this surface at best and
	   3.31:1 on chrome: under the floor for something whose whole job is to be read, and
	   exactly the fault `contrast.test.ts` exists for; its header says the caption ink is
	   deliberately not allowed as far as the chip surface. Still quieter than a colour, so the
	   intent above survives. */
	.badge.local {
		background: var(--sift-surface-4);
		color: var(--sift-ink-2);
	}

	.badge.down {
		background: var(--sift-bad-bg);
		color: var(--sift-bad-text);
	}
</style>
