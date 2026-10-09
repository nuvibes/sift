<script lang="ts">
	/* Where a download lands, what downloads it, and what the file is called, for everything,
	 * and per Site. */
	import { onMount } from 'svelte';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import NameTemplateField from './NameTemplateField.svelte';
	import { Button, Fold, Problem, Select } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import FolderPicker from '$lib/library/FolderPicker.svelte';
	import { Picker } from '$lib/library/picker.svelte';
	import { api, ApiError } from '$lib/api/client';
	import { bridge } from '$lib/bridge';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { COPY } from './NamingTemplate.search';
	import { folderFor, LIBRARY_STEPS } from './download-folder';
	import { movable } from '$lib/library/movable.svelte';
	import { saveDownloadFolder } from '$lib/library/destinations.svelte';
	import { disambiguate } from '$lib/library/folder-names';
	import type { components } from '$lib/api/schema';

	type SiteOption = components['schemas']['SiteOptionItem'];
	type SiteOptions = components['schemas']['SiteOptionsResponse'];

	/* `SupportedSite`, which is what `/supported-sites` actually returns. */
	type SupportedSite = components['schemas']['SupportedSite'];
	type Downloader = components['schemas']['DownloaderChoice'];

	/** The reserved scope everything follows unless a Site says otherwise. The server's word. */
	const EVERYTHING = '*default*';
	/** What "nothing chosen" is on the wire. A `Select` deals in strings, so it needs a value. */
	const UNSET = '';

	/** The rule for every address Sift has no Site for. */
	let template = $state<string | null>(null);
	let destination = $state(UNSET);
	let tokens = $state<Record<string, string>>({});
	/* The tools come from the server for the reason the tokens beside them do: which tools Sift
	   ships is a fact about the build, and a second list in the browser is one that goes on
	   offering a tool after it has gone. */
	let downloaders = $state<Downloader[]>([]);
	let tool = $state('');
	let sites = $state<SiteOption[]>([]);
	let known = $state<SupportedSite[]>([]);
	let problem = $state<string | null>(null);
	/** Which Site the "give one its own" chooser is pointed at. */
	let adding = $state<string | undefined>(undefined);

	/* The template as the server last said it, so a re-read can tell a box somebody is typing in
	   (it differs) from one nobody touched (it does not), and leave the first alone. */
	let storedTemplate: string | null = null;

	async function readOptions(): Promise<void> {
		try {
			const [options, supported] = await Promise.all([
				api.get<SiteOptions>('/site-options'),
				api.get<SupportedSite[]>('/supported-sites')
			]);
			if (template === storedTemplate) template = options.default.naming ?? null;
			storedTemplate = options.default.naming ?? null;
			destination = options.default.dest_folder_id ?? UNSET;
			tool = options.default.downloader ?? '';
			tokens = options.tokens;
			downloaders = options.downloaders;
			sites = options.sites;
			known = supported;
		} catch {
			problem = COPY.cannotLoad;
		}
	}

	onMount(() => {
		void (async () => {
			await readOptions();
			// Only an admin sees this panel, and only somewhere Sift may write can be a destination.
			await movable.ensure();
		})();
	});
	/* A Site's rule or the default saved in another window, or by another admin, is said on the
	   settings bell (`site_options`); the pane follows it. */
	whenChanged(settingChanges, () => void readOptions());

	/* The folders Sift may write into, named and then told apart. */
	const placed = $derived(
		disambiguate(
			movable.folders.map((folder) => ({
				value: folder.id,
				label: folder.name,
				path: folder.path || folder.name
			}))
		)
	);

	/* The first row is handed in whole rather than as a label, so the one that names the default
	   can carry the same quiet phrase `disambiguate` gave that folder further down the list. */
	function destinations(first: { label: string; detail?: string }) {
		return [{ value: UNSET, ...first }, ...placed];
	}

	/* What "follow the default" says on a Site's own chooser, and it NAMES the folder. */
	const followsDefault = $derived.by(() => {
		/* Nothing set for everything, and none for this Site: the same honest phrase the default row
		   wears, because "Default download folder" there would promise a folder that does not exist. */
		if (destination === UNSET) return { label: COPY.notSet };
		const named = placed.find((one) => one.value === destination);
		if (!named) return { label: COPY.followFolder };
		return { label: COPY.followFolder, detail: named.label };
	});

	/** The tools, as a chooser takes them. Order is the server's, and Sift's own answer is first. */
	const toolOptions = $derived(downloaders.map((one) => ({ value: one.value, label: one.label })));

	/** The name to show for a Site, which the Site list is the authority on. */
	function nameOf(scope: string): string {
		return known.find((site) => site.key === scope)?.name ?? scope;
	}

	/** What Sift names a Site's files when it has no rule of its own, EMPTY for keep. */
	function shippedOf(scope: string): string {
		return known.find((site) => site.key === scope)?.default_naming ?? '';
	}

	/** The words a Site can fill, in the order to offer them. */
	function wordsOf(scope: string): string[] {
		return known.find((site) => site.key === scope)?.name_words ?? Object.keys(tokens);
	}

	/* What can be given its own answers, and what deliberately cannot. */
	const addable = $derived(
		known
			.filter((site) => site.supported && !sites.some((one) => one.scope === site.key))
			.map((site) => ({ value: site.key, label: site.name }))
	);

	/* One write, and it carries ALL THREE fields. They are stored in one row, so sending one and
	 * leaving the others out is not a partial save. */
	async function store(
		scope: string,
		naming: string | null,
		folderId: string,
		downloader: string
	): Promise<boolean> {
		try {
			await api.put(`/site-options/${encodeURIComponent(scope)}`, {
				body: {
					naming,
					dest_folder_id: folderId || null,
					downloader: downloader || null
				}
			});
			return true;
		} catch (error) {
			const said = error instanceof ApiError ? error.detail : null;
			toasts.show(said ?? COPY.notSaved, { tone: 'error' });
			return false;
		}
	}

	const saveDefault = () => void store(EVERYTHING, template, destination, tool);

	/* The folder through the one writer every chooser shares; a refusal puts the row back. */
	async function chooseDefault(id: string): Promise<void> {
		const previous = destination;
		destination = id;
		const named = placed.find((one) => one.value === id)?.label;
		if (!(await saveDownloadFolder(id || null, named))) destination = previous;
	}

	/* Choosing a folder that is not in the list yet. */
	const canPick = bridge.canChooseFolder();
	let choosing = $state(false);
	const picker = new Picker();
	let sheetOpen = $state(false);
	let pressedAtTop = $state(false);
	const uid = $props.id();
	const listId = `download-folder-${uid}`;

	async function useFolderAt(path: string): Promise<void> {
		const wanted = path.trim();
		if (!wanted || choosing) return;
		choosing = true;
		try {
			const id = await folderFor(wanted, LIBRARY_STEPS);
			// Read again, so the list offers the folder that was just made or added.
			movable.forget();
			await movable.ensure();
			await chooseDefault(id);
		} catch (error) {
			const said =
				error instanceof ApiError ? error.detail : error instanceof Error ? error.message : null;
			toasts.show(said || COPY.notSaved, { tone: 'error' });
		} finally {
			choosing = false;
		}
	}

	async function pickFolder(): Promise<void> {
		if (!canPick) {
			pressedAtTop = false;
			await picker.open();
			sheetOpen = true;
			return;
		}
		const chosen = await bridge.chooseFolder();
		// Closing the picker without choosing is not an error and must not draw one.
		if (chosen !== null) await useFolderAt(chosen);
	}

	/* The sheet's Use this folder: a folder, never the list of places itself. */
	function usePicked(event: SubmitEvent): void {
		event.preventDefault();
		const chosen = picker.selected;
		if (picker.atTopLevel || !chosen) {
			pressedAtTop = true;
			return;
		}
		sheetOpen = false;
		void useFolderAt(chosen.path);
	}

	function onSite(scope: string, changes: Partial<SiteOption>): void {
		const before = sites.find((one) => one.scope === scope);
		const after = {
			...(before ?? { scope, naming: null, dest_folder_id: null, downloader: null }),
			...changes
		};
		sites = sites.map((one) => (one.scope === scope ? after : one));
		void (async () => {
			const stored = await store(
				scope,
				after.naming ?? null,
				after.dest_folder_id ?? UNSET,
				after.downloader ?? ''
			);
			// Refused: the row goes back to what the server holds, so the line under it describes
			// the rule that will actually name the next file rather than the one that was refused.
			if (!stored && before) sites = sites.map((one) => (one.scope === scope ? before : one));
		})();
	}

	/** Give a Site its own answers, starting from the default's. */
	function add(scope: string): void {
		if (!scope || sites.some((one) => one.scope === scope)) return;
		adding = undefined;
		sites = [...sites, { scope, naming: null, dest_folder_id: null, downloader: null }];
	}

	/** Put a Site back to following the default. */
	async function follow(scope: string): Promise<void> {
		try {
			await api.del(`/site-options/${encodeURIComponent(scope)}`);
			sites = sites.filter((one) => one.scope !== scope);
		} catch {
			toasts.show(COPY.notUndone, { tone: 'error' });
		}
	}
</script>

<!-- Below what gets downloaded, the limits and the sounds: where a download lands and what it is
     called are set once, and the per-Site cards are a list the section owns. -->
<SettingGroup id="downloads.name_template" heading={COPY.heading} />

<section class="group">
	<Problem message={problem} />

	<LabelledRow id="downloads.default_folder" label={COPY.folder} help={COPY.folderHelp} besideField>
		<div class="folder">
			{#if movable.foldersRead === 'failed' && !problem}
				<Problem message={COPY.cannotLoad} />
			{:else}
				<Select
					label={COPY.defaultFolder}
					value={destination}
					options={destinations({ label: COPY.notSet })}
					disabled={choosing}
					onValueChange={(chosen: string) => void chooseDefault(chosen)}
				/>
			{/if}
			<Button disabled={choosing} icon="folder" onclick={() => void pickFolder()}>
				{COPY.choose}
			</Button>
		</div>
	</LabelledRow>

	<Modal bind:open={sheetOpen} title={COPY.picker.title}>
		<form class="folder-sheet" onsubmit={usePicked}>
			<div class="sheet-body">
				<span class="sheet-label" id={listId}>{COPY.picker.list}</span>
				<FolderPicker {picker} labelledBy={listId} describedBy="{listId}-help" />
				<p class="sheet-help" id="{listId}-help">{COPY.picker.help}</p>
				{#if pressedAtTop && picker.atTopLevel}
					<p class="sheet-warn" role="alert">{COPY.picker.atTop}</p>
				{/if}
			</div>
			<div class="sheet-actions">
				<Button onclick={() => (sheetOpen = false)}>{COPY.picker.cancel}</Button>
				<Button type="submit" tone="primary" icon="check">{COPY.picker.use}</Button>
			</div>
		</form>
	</Modal>

	<!-- The rule for everything, drawn by the same field every Site row uses, so a rule stored for
	     it is always on screen. -->
	<NameTemplateField
		value={template}
		words={Object.keys(tokens)}
		{tokens}
		label={COPY.otherAddresses}
		help={COPY.otherAddressesHelp}
		onsave={(next: string | null) => {
			template = next;
			saveDefault();
		}}
	/>

	<!-- Absent until there is a Site list to choose from, because an empty chooser beside a
	     heading reads as something that failed to load. -->
	{#if known.length > 0}
		<SettingGroup heading={COPY.perSite.name} help={COPY.perSite.help} />

		{#if sites.length === 0}
			<p class="lede">{COPY.noneYet}</p>
		{:else}
			<!--
				One card per Site, with the SAME three answers the default has, in the same order.
			-->
			<ul class="sites">
				{#each sites as site (site.scope)}
					<li>
						<!--
							Folded, because a Site's answers are three controls AND the whole name
							builder: four Sites open together is a pane nobody can find anything on.
						-->
						<Fold summary={nameOf(site.scope)}>
							<div class="who">
								<p class="after">{COPY.afterReset(nameOf(site.scope))}</p>
								<Button
									size="small"
									onclick={() => void follow(site.scope)}
									aria-label={COPY.resetSite(nameOf(site.scope))}
								>
									{COPY.reset}
								</Button>
							</div>

							<LabelledRow label={COPY.siteFolder}>
								<Select
									value={site.dest_folder_id ?? UNSET}
									options={destinations(followsDefault)}
									label={COPY.folderFor(nameOf(site.scope))}
									onValueChange={(chosen: string) =>
										onSite(site.scope, { dest_folder_id: chosen || null })}
								/>
							</LabelledRow>

							<LabelledRow label={COPY.downloader}>
								<Select
									value={site.downloader ?? ''}
									options={toolOptions}
									label={COPY.downloaderFor(nameOf(site.scope))}
									onValueChange={(chosen: string) =>
										onSite(site.scope, { downloader: chosen || null })}
								/>
							</LabelledRow>

							<NameTemplateField
								value={site.naming ?? null}
								shipped={shippedOf(site.scope)}
								site={nameOf(site.scope)}
								words={wordsOf(site.scope)}
								{tokens}
								scope={site.scope}
								label={COPY.name}
								onsave={(next: string | null) => onSite(site.scope, { naming: next })}
							/>
						</Fold>
					</li>
				{/each}
			</ul>
		{/if}

		{#if addable.length > 0}
			<LabelledRow label={COPY.addSite}>
				<!-- Drawn again for every Site added, so it goes back to asking. -->
				{#key sites.length}
					<Select
						value={adding}
						options={addable}
						label={COPY.selectSite}
						placeholder={COPY.selectSite}
						onValueChange={add}
					/>
				{/key}
			</LabelledRow>
		{/if}
	{/if}
</section>

<style>
	.group {
		margin-block-end: var(--space-6);
	}

	.lede {
		margin-block-end: var(--space-3);
	}

	.sites {
		list-style: none;
		margin: 0;
		padding: 0;
	}

	/* A Site per row of the pane: its fold, and under it its answers. */
	.sites li {
		padding-block: var(--space-3);
	}

	.sites li + li {
		border-block-start: 1px solid var(--sift-line);
	}

	.who {
		display: flex;
		align-items: center;
		justify-content: var(--row-pack, flex-end);
		gap: var(--space-4);
		margin-block: var(--space-2);
	}

	/* What the press leaves the Site following, said before it is pressed: the folder, the name
	   and the downloader each fall back to something different. */
	.after {
		flex: 1;
		margin: 0;
		/* The sentence starts where the Site's rows start: it is bounded by the reading measure,
		   and the room it leaves goes between it and the press rather than before it. */
		margin-inline-end: auto;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* The chooser and the press beside it, ending at the row's right edge like every control. */
	.folder {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: var(--row-pack, flex-end);
		gap: var(--space-2);
	}

	/* The browser's folder sheet, laid out as `FolderChoice`'s: the list, its help, then the way
	   out and the act at the sheet's far edge. */
	.folder-sheet {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		margin-block-start: var(--space-4);
	}

	.sheet-body {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.sheet-label {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.sheet-help {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.sheet-warn {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-warn);
	}

	.sheet-actions {
		display: flex;
		gap: var(--space-2);
	}

	.sheet-actions > :global(:first-child) {
		margin-inline-start: auto;
	}
</style>
