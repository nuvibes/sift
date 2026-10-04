<script lang="ts">
	/* LIVE: nothing moves it (the libraries on this machine change only by a press here or by opening another library, which restarts the server) */
	/*
	 * The Database Switcher: every library the server knows of, making a new one, and opening another.
	 *
	 * ## Why it asks the SERVER, from the app and from a browser alike
	 *
	 * Which library is open is a fact about the server, and the server is on whichever computer
	 * holds the libraries: somebody reading this may be in a browser on another machine. So the
	 * list, "New library", "Open" and "Import" are all requests to the server, which checks the
	 * library, backs up an older one, and asks to be started again on it. In the app, the shell reads
	 * that ask and starts the backend on the other folder; the page is then reloaded by the shell. In
	 * a browser, this page waits for a DIFFERENT run of the server to answer and reloads itself.
	 * Either way everybody using it is signed out, because the new library has its own sign-ins.
	 *
	 * ## What the page may name
	 *
	 * Never a path. A library on the list is named by the id the server handed out, and a new one by
	 * a name the server checks is one folder name; the folder it goes in is the server's. What only
	 * the app can grant (a database file chosen in the machine's own picker, and the libraries the
	 * app remembers from elsewhere) still goes through the app's bridge, which is the one thing
	 * that can name a path. A browser gets "Import a database file" instead: an upload, made into a
	 * NEW library, so the file somebody kept is never the one that is upgraded.
	 *
	 * From ANOTHER computer, where the Sift app on the computer running Sift answers through the
	 * server (`server-shell.ts`), the libraries THAT app remembers are listed too and open through
	 * it, and an older one is refused in words rather than asked about, since its question is a
	 * dialog on that computer's screen. Its file picker is that computer's own, so the screen says
	 * where it is instead of offering it.
	 *
	 * ## Where the upgrade question is
	 *
	 * Here, for the server's list: a library made by an older Sift says so in the list, and opening it
	 * asks first, because opening upgrades it in one direction. The server refuses to upgrade one it
	 * was not told was agreed to, and makes the backup copy before it stops anything.
	 *
	 * ## Duplicate this library
	 *
	 * Beside "New library", and the one thing here that does NOT switch: the copy is a task (minutes
	 * of copying), followed here by its progress, and opened from the list whenever somebody wants
	 * it. The form measures on opening, not with the pane, because the server walks the cache to say
	 * how big the pictures are.
	 */
	import { onMount } from 'svelte';
	import {
		Button,
		ChooseFile,
		ConfirmDialog,
		ContextMenuGroup,
		ContextMenuItem,
		Empty,
		Problem,
		SectionHeading,
		Switch,
		TextInput
	} from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import PathText from '$lib/components/PathText.svelte';
	import { size } from '$lib/library/facts';
	import { DownloadWatch, sayWaiting } from '$lib/jobs/watch-download.svelte';
	import LastRun from '$lib/jobs/LastRun.svelte';
	import { runWords, type RunOnRecord } from '$lib/jobs/last-run';
	import ActionRow from './ActionRow.svelte';
	import FromStash from './FromStash.svelte';
	import {
		DUPLICATE_JOB,
		readDuplicatePlan,
		startDuplicate,
		type DuplicatePlan
	} from './backup-state.svelte';
	import { ApiError, api } from '$lib/api/client';
	import type { components } from '$lib/api/schema';
	import { serverBootId } from '$lib/shell/health';
	import { followSwitch } from './follow-switch';
	import { bridge, type LibraryList } from '$lib/bridge';
	import { thisDevice } from '$lib/desktop/server-shell';
	import {
		actThere,
		offersServer,
		openServerLibrary,
		readServerDesktop,
		readServerLibraries,
		type ServerDesktop
	} from '$lib/desktop/server-shell';
	import { COPY as PANE } from './Backup.search';

	const COPY = PANE.switcher;

	type LibrariesView = components['schemas']['LibrariesView'];
	type ServerLibrary = components['schemas']['LibraryEntry'];
	type SwitchView = components['schemas']['SwitchView'];

	let server = $state<LibrariesView | null>(null);
	let shell = $state<LibraryList | null>(null);
	let busy = $state<string | null>(null);
	let problem = $state<string | null>(null);
	/* The library whose upgrade is being asked about, by id. */
	let upgrading = $state<string | null>(null);
	/* What the server is starting on, while this page waits for it. */
	let switchingTo = $state<string | null>(null);
	let newName = $state('');
	let importing = $state<File | null>(null);
	let importName = $state('');
	/* The library whose delete is being confirmed, by id, and the name typed to confirm it. */
	let deleting = $state<string | null>(null);
	/** The question's own open state: closing it by any way out forgets which library it was. */
	let asking = {
		get value() {
			return deleting !== null;
		},
		set value(on: boolean) {
			if (!on) deleting = null;
		}
	};
	/** The library the delete question is about. Kept while the question closes, so the press that
	   answers it still names the library after the dialog has let go of `open`. */
	let condemned = $state<ServerLibrary | null>(null);
	let typedName = $state('');
	/* What the last delete did, said under the list. */
	let deletedNote = $state<string | null>(null);

	const offered = bridge.canSwitchLibrary();
	/* The computer running Sift, where this window cannot switch through its own app. */
	let desk = $state<ServerDesktop | null>(null);
	const fromAfar = $derived(!offered && offersServer(desk));

	/* How long a switch is given, and how often it is looked at. The same as the server restart on
	   the Performance screen: a first start of a library can create and migrate a database. */

	onMount(() => {
		void read();
		/* A copy already under way (the pane was left and opened again) is followed again. */
		void copying.resume();
		void readLastCopy();
	});

	async function read() {
		try {
			server = await api.get<LibrariesView>('/libraries');
		} catch (error) {
			problem = sayRefusal(error, COPY.cannotList);
		}
		if (offered) {
			shell = await bridge.libraries();
			return;
		}
		desk = await readServerDesktop();
		if (!offersServer(desk)) return;
		const listed = await readServerLibraries();
		shell =
			listed === null
				? null
				: {
						current: listed.current,
						libraries: listed.libraries.map((one) => ({
							dataDir: one.data_dir,
							cacheDir: one.cache_dir,
							name: one.name,
							lastOpened: one.last_opened
						}))
					};
	}

	function sayRefusal(error: unknown, fallback: string): string {
		return error instanceof ApiError ? (error.detail ?? error.message) : fallback;
	}

	/** Whether two folders are one. Case-insensitive: Windows is. */
	function same(a: string, b: string): boolean {
		return a.replace(/[\\/]+$/, '').toLowerCase() === b.replace(/[\\/]+$/, '').toLowerCase();
	}

	/* The libraries the app remembers that the server does not list: kept somewhere of their own
	   and never switched away from by the server. Opened through the app, which can name them. */
	const remembered = $derived(
		(shell?.libraries ?? []).filter(
			(one) => !(server?.libraries ?? []).some((known) => same(known.data_dir, one.dataDir))
		)
	);

	const canSwitch = $derived(server?.can_switch ?? false);

	/** The short word beside a library that cannot simply be opened. */
	function markFor(one: ServerLibrary): string | undefined {
		if (one.current) return COPY.marks.current;
		if (one.verdict === 'older') return COPY.marks.older;
		if (one.verdict === 'newer') return COPY.marks.newer;
		if (one.verdict === 'empty') return COPY.marks.empty;
		if (one.verdict === 'unreadable') return COPY.marks.unreadable;
		return undefined;
	}

	function openable(one: ServerLibrary): boolean {
		return !one.current && (one.verdict === 'current' || one.verdict === 'older');
	}

	/**
	 * Wait until a DIFFERENT run of the server answers, then load the page it serves.
	 *
	 * On the boot id rather than on "does it answer": the server replies to the ask before it goes,
	 * so a poll for any reply would decide it had come back before it had left. In the app the shell
	 * reloads the window first, and this simply never finishes.
	 */
	async function followTheSwitch(before: string | null, name: string) {
		switchingTo = name;
		if (await followSwitch(before)) return;
		switchingTo = null;
		busy = null;
		problem = COPY.stillSwitching;
	}

	/** Ask the server to switch, and follow it. `ask` makes the request and answers the reply. */
	async function switchBy(key: string, name: string, ask: () => Promise<SwitchView>) {
		busy = key;
		problem = null;
		const before = await serverBootId();
		let answer: SwitchView;
		try {
			answer = await ask();
		} catch (error) {
			problem = sayRefusal(error, COPY.cannotSwitch);
			busy = null;
			return;
		}
		if (!answer.switching) {
			busy = null;
			return;
		}
		await followTheSwitch(before, name);
	}

	function open(one: ServerLibrary) {
		if (one.verdict === 'older' && upgrading !== one.id) {
			upgrading = one.id;
			return;
		}
		const upgrade = upgrading === one.id;
		upgrading = null;
		// The device goes with the ask, as it does on every act on the computer running Sift, so
		// History can say which computer the library was switched from.
		void switchBy(one.id, one.name, async () => {
			const device = await thisDevice();
			return api.post<SwitchView>('/libraries/open', {
				body: { library: one.id, upgrade },
				...(device ? { query: { device } } : {})
			});
		});
	}

	const MAKE = 'make';

	function make() {
		const name = newName.trim();
		if (name === '') return;
		void switchBy(MAKE, name, () => api.post<SwitchView>('/libraries', { body: { name } }));
	}

	const IMPORT = 'import';

	function pickImport(file: File) {
		importing = file;
		importName = file.name.replace(/\.[^.]+$/, '');
		problem = null;
	}

	function importNow() {
		const file = importing;
		const name = importName.trim();
		if (file === null || name === '') return;
		const form = new FormData();
		form.append('file', file);
		form.append('name', name);
		void switchBy(IMPORT, name, () => api.post<SwitchView>('/libraries/import', { body: form }));
	}

	/* --- which opens at start, deleting one, taking one off the list ------------------------- */

	/*
	 * Behind each row's door: which library opens when Sift starts, and the way off the list.
	 *
	 * A library in the libraries folder is DELETED (to the Recycle Bin, with its name typed first);
	 * one kept in a folder of its own is only taken off the list, because that folder is somebody's
	 * own. The open library has neither: it is the one running. Each answer is the list again,
	 * which is what is drawn, so the rows say what the server now holds.
	 */
	async function chooseOpening(library: string | null) {
		problem = null;
		try {
			server = await api.put<LibrariesView>('/libraries/opens-at-start', { body: { library } });
		} catch (error) {
			problem = sayRefusal(error, COPY.cannotChoose);
		}
	}

	async function forgetServer(one: ServerLibrary) {
		problem = null;
		try {
			server = await api.post<LibrariesView>('/libraries/forget', { body: { library: one.id } });
		} catch (error) {
			problem = sayRefusal(error, COPY.cannotForget);
		}
	}

	function askToDelete(one: ServerLibrary) {
		deleting = one.id;
		condemned = one;
		typedName = '';
		deletedNote = null;
		problem = null;
	}

	async function deleteNow(one: ServerLibrary) {
		busy = one.id;
		problem = null;
		try {
			server = await api.post<LibrariesView>('/libraries/delete', {
				body: { library: one.id, name: typedName.trim() }
			});
			deleting = null;
			deletedNote = COPY.remove.done(one.name);
			/* The app remembers every library it has opened; one in the Recycle Bin is not one it can
			   offer to open again. */
			if (offered) shell = await bridge.forgetLibrary(one.data_dir);
		} catch (error) {
			problem = sayRefusal(error, COPY.remove.failed);
		} finally {
			busy = null;
		}
	}

	/* --- what only the app can do ----------------------------------------------------------- */

	async function openRemembered(dataDir: string, name: string) {
		busy = dataDir;
		problem = null;
		if (fromAfar) {
			/* Through the app there, which answers before Sift restarts on it; this page then waits
			   for the new run, as a switch the server arranges does. */
			switchingTo = name;
			const outcome = await actThere(
				() => openServerLibrary(dataDir),
				COPY.cannotSwitch,
				COPY.stillSwitching
			);
			if (outcome.ok) return;
			switchingTo = null;
			problem = outcome.problem;
			busy = null;
			return;
		}
		const settled = await bridge.openLibrary(dataDir);
		/* Nothing is put back on `ok`: the shell is already loading the new library's own page. A
		   null refusal is somebody answering no to the shell's own question, which has nothing to say. */
		if (settled.ok) return;
		problem = settled.refusal;
		busy = null;
	}

	const CHOOSE = 'choose';

	async function choose() {
		busy = CHOOSE;
		problem = null;
		try {
			const settled = await bridge.addLibrary();
			if (settled.ok) return;
			problem = settled.refusal;
		} finally {
			busy = null;
		}
	}

	async function forget(dataDir: string) {
		shell = await bridge.forgetLibrary(dataDir);
	}

	/* --- duplicate this library ------------------------------------------------------------- */

	const DUPLICATE = COPY.duplicate;

	let duplicating = $state(false);
	let plan = $state<DuplicatePlan | null>(null);
	let copyName = $state('');
	let copyPictures = $state(true);
	let copyProblem = $state<string | null>(null);
	let copyStarting = $state(false);
	/* The name asked for, so the sentence at the end can say it. */
	let copyAsked = $state<string | null>(null);

	/* The task, followed by the watcher every long task on Settings uses. When it ends the queue's
	   record of it is the line under the press, as after a reload; the watch's own words are for a
	   run with no record. */
	const copying = new DownloadWatch(
		DUPLICATE_JOB,
		async () => {
			await Promise.all([read(), readLastCopy()]);
			if (lastCopy !== null) return '';
			const name = copyAsked;
			const made = name !== null && (server?.libraries ?? []).some((one) => one.name === name);
			return made && name !== null ? DUPLICATE.ready(name) : DUPLICATE.notFinished;
		},
		DUPLICATE.lost
	);

	const LAST_COPY = runWords('The last copy');
	let lastCopy = $state<RunOnRecord | null>(null);

	async function readLastCopy() {
		const page = await api
			.get<components['schemas']['JobsPage']>('/jobs', {
				query: { type: DUPLICATE_JOB, limit: 1 }
			})
			.catch(() => null);
		const job = page?.jobs?.[0];
		lastCopy =
			job && ['done', 'failed', 'canceled'].includes(job.state)
				? {
						ended_at: job.updated_at,
						outcome: job.state,
						said: job.state === 'failed' ? (job.error ?? job.note) : job.note
					}
				: null;
	}

	const copyStatus = $derived(
		copying.waiting
			? sayWaiting(copying.position)
			: DUPLICATE.copying(Math.round(copying.fraction * 100))
	);

	async function openDuplicate() {
		duplicating = true;
		copyProblem = null;
		try {
			plan = await readDuplicatePlan();
			copyProblem = plan.refusal ?? null;
		} catch (error) {
			copyProblem = sayRefusal(error, DUPLICATE.cannotMeasure);
		}
	}

	async function duplicate() {
		const name = copyName.trim();
		if (name === '') return;
		copyStarting = true;
		copyProblem = null;
		try {
			const started = await startDuplicate(name, copyPictures);
			copyAsked = name;
			copying.follow(started.job_id);
			duplicating = false;
			copyName = '';
		} catch (error) {
			copyProblem = sayRefusal(error, DUPLICATE.cannotStart);
		} finally {
			copyStarting = false;
		}
	}
</script>

<SectionHeading id="backup.switcher">{COPY.name}</SectionHeading>
<p class="lede">{COPY.lede}</p>

{#if server !== null && !canSwitch}
	<p class="note">{COPY.byHand}</p>
{/if}

{#if switchingTo !== null}
	<p class="note" role="status">{COPY.starting(switchingTo)}</p>
{/if}

{#if server}
	{#each server.libraries ?? [] as one (one.id)}
		<!-- The reading's own sentence under a library that can't be read, where it has one: the
		     mark alone says the same of a stranger's file and of a library an older Sift can bring
		     up to date. -->
		{#snippet where()}<span class="path"><PathText path={one.data_dir} /></span
			>{#if one.verdict === 'unreadable' && one.detail}<span class="why">{one.detail}</span
				>{/if}{#if one.opens_at_start}<span class="start">{COPY.opensAtStart}</span>{/if}{/snippet}
		<!-- Two parts: which library opens at start, then the row that takes this one away, last
		     and below its own line. -->
		{#snippet more()}
			<ContextMenuGroup>
				{#if one.opens_at_start}
					<ContextMenuItem
						label={COPY.openLastAtStart}
						icon="history"
						onselect={() => void chooseOpening(null)}
					/>
				{:else}
					<ContextMenuItem
						label={COPY.openAtStart}
						icon="keep"
						onselect={() => void chooseOpening(one.id)}
					/>
				{/if}
			</ContextMenuGroup>
			<ContextMenuGroup>
				{#if !one.current && one.in_folder}
					<ContextMenuItem
						label={COPY.remove.begin}
						icon="delete"
						destructive
						onselect={() => askToDelete(one)}
					/>
				{:else if !one.current}
					<ContextMenuItem
						label={COPY.forget}
						icon="close"
						onselect={() => void forgetServer(one)}
					/>
				{/if}
			</ContextMenuGroup>
		{/snippet}
		<ActionRow
			label={one.name}
			note={markFor(one)}
			action={COPY.open}
			icon="folder"
			busy={busy === one.id}
			disabled={busy !== null || !canSwitch}
			actDisabled={!openable(one)}
			onclick={() => open(one)}
			trailingIcon="expand_more"
			trailingLabel={COPY.more(one.name)}
			trailingMenu={more}
			children={where}
		/>
		{#if upgrading === one.id}
			<div class="ask" role="group" aria-label={COPY.upgradeAsk(one.name)}>
				<p class="note">{COPY.upgradeSays(one.name)}</p>
				<div class="buttons">
					<Button onclick={() => open(one)} disabled={busy !== null}>{COPY.upgrade}</Button>
					<Button tone="ghost" onclick={() => (upgrading = null)}>{COPY.cancel}</Button>
				</div>
			</div>
		{/if}
	{/each}
{/if}

{#if offered || fromAfar}
	{#each remembered as one (one.dataDir)}
		{#snippet where()}<span class="path"><PathText path={one.dataDir} /></span>{/snippet}
		{#if offered}
			<ActionRow
				label={one.name}
				action={COPY.open}
				icon="folder"
				busy={busy === one.dataDir}
				disabled={busy !== null}
				trailingIcon="close"
				trailingLabel={COPY.forget}
				ontrailing={() => void forget(one.dataDir)}
				onclick={() => void openRemembered(one.dataDir, one.name)}
				children={where}
			/>
		{:else}
			<!-- From another computer: opened through the app there, and forgotten only there. -->
			<ActionRow
				label={one.name}
				action={COPY.open}
				icon="folder"
				busy={busy === one.dataDir}
				disabled={busy !== null}
				onclick={() => void openRemembered(one.dataDir, one.name)}
				children={where}
			/>
		{/if}
	{/each}
{/if}

{#if server?.libraries?.length === 1 && remembered.length === 0}
	<Empty scope="block">{COPY.none}</Empty>
{/if}

<Problem message={problem} />
{#if deletedNote !== null}
	<p class="note" role="status">{deletedNote}</p>
{/if}

{#if server}
	<!-- Rows, each with its control on the right where every press on the pane is: the name box
	     and Create for a new library, Duplicate, and the file to import. -->
	{@const folder = server.folder}
	{#snippet createdIn()}{COPY.createdIn}<span class="inline-path"><PathText path={folder} /></span
		>.{/snippet}
	<LabelledRow id="backup.new" help={COPY.createdHelp} foot={createdIn} wide besideField>
		<!-- DRESSED BY: .name (LabelledRow styles the label snippet its caller writes) -->
		{#snippet name()}<label class="name" for="switcher-new-name">{COPY.newLabel}</label>{/snippet}
		<TextInput
			id="switcher-new-name"
			bind:value={newName}
			maxlength={64}
			placeholder={COPY.newPlaceholder}
			disabled={busy !== null || !canSwitch}
		/>
		<Button
			icon="add"
			onclick={make}
			disabled={busy !== null || !canSwitch || newName.trim() === ''}>{COPY.create}</Button
		>
	</LabelledRow>

	{#snippet copyState()}<span role="status">{copyStatus}</span>{/snippet}
	{#snippet copyLast()}<span role="status"><LastRun run={lastCopy} words={LAST_COPY} /></span
		>{/snippet}
	<ActionRow
		id="backup.duplicate"
		label={DUPLICATE.name}
		help={DUPLICATE.help}
		action={DUPLICATE.begin}
		icon="content_copy"
		disabled={busy !== null || duplicating || copying.running}
		onclick={() => void openDuplicate()}
		children={copying.running ? copyState : lastCopy && !copying.outcome ? copyLast : undefined}
	/>
	<div class="make">
		{#if duplicating && !copying.running}
			<p class="note">{DUPLICATE.lede}</p>
			<label class="label" for="switcher-duplicate-name">{DUPLICATE.nameLabel}</label>
			<TextInput
				id="switcher-duplicate-name"
				bind:value={copyName}
				maxlength={64}
				disabled={copyStarting}
			/>
			<!-- The whole width, so the switch sits on the pane's control column with the rows above. -->
			<div class="whole">
				<LabelledRow
					label={DUPLICATE.pictures}
					help={DUPLICATE.picturesHelp(plan === null ? null : size(plan.pictures_bytes))}
				>
					<Switch label={DUPLICATE.pictures} bind:checked={copyPictures} disabled={copyStarting} />
				</LabelledRow>
			</div>
			<p class="warning">{DUPLICATE.warning}</p>
			{#if plan !== null}
				<p class="note">{DUPLICATE.room(size(plan.free_bytes) ?? '')}</p>
			{/if}
			<div class="buttons">
				<Button
					icon="content_copy"
					onclick={() => void duplicate()}
					disabled={copyStarting || copyName.trim() === '' || plan?.refusal != null}
					>{DUPLICATE.start}</Button
				>
				<Button tone="ghost" onclick={() => (duplicating = false)}>{DUPLICATE.cancel}</Button>
			</div>
		{/if}
		<Problem message={copyProblem} />
		{#if copying.outcome && !copying.running}
			<p class="note" role="status">{copying.outcome}</p>
		{/if}
	</div>

	<FromStash />
{/if}

{#if offered}
	<ActionRow
		id="backup.import"
		label={COPY.importFile}
		help={COPY.chooseSays}
		action={COPY.chooseFile}
		disabled={busy !== null}
		onclick={() => void choose()}
	/>
{:else if server !== null}
	<LabelledRow id="backup.import" label={COPY.importFile} help={COPY.importSays}>
		<ChooseFile
			accept=".zip,.sqlite3"
			disabled={busy !== null || !canSwitch}
			label={COPY.importFile}
			onchoose={pickImport}
		>
			{COPY.importChoose}
		</ChooseFile>
	</LabelledRow>
	{#if fromAfar}
		<p class="note">{COPY.pickThere(desk?.machine ?? null)}</p>
	{/if}
	{#if importing !== null}
		<div class="make">
			<label class="label" for="switcher-import-name">{COPY.importName(importing.name)}</label>
			<div class="line">
				<TextInput
					id="switcher-import-name"
					bind:value={importName}
					maxlength={64}
					disabled={busy !== null}
				/>
				<Button
					icon="upload"
					onclick={importNow}
					disabled={busy !== null || !canSwitch || importName.trim() === ''}
					>{COPY.importOpen}</Button
				>
			</div>
		</div>
	{/if}
{/if}

<!-- The one destructive act here asks through the shared confirm, as every pane does: the
     consequence named, the verb on the button, and the name typed exactly before it can be pressed.
     Nothing is drawn in the row itself. -->
{#if condemned}
	{@const asked = condemned}
	<ConfirmDialog
		bind:open={asking.value}
		title={COPY.remove.ask(asked.name)}
		consequence={COPY.remove.says(asked.name)}
		confirmLabel={COPY.remove.confirm}
		confirmDisabled={busy !== null || typedName.trim() !== asked.name}
		onconfirm={() => void deleteNow(asked)}
	>
		{#snippet extra()}
			<label class="label" for="switcher-delete-name">{COPY.remove.typeLabel(asked.name)}</label>
			<TextInput
				id="switcher-delete-name"
				bind:value={typedName}
				maxlength={64}
				disabled={busy !== null}
			/>
		{/snippet}
	</ConfirmDialog>
{/if}

<style>
	.lede {
		margin: 0 0 var(--space-4);
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	.make,
	.ask {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: var(--space-2);
		margin-block-start: var(--space-4);
	}

	/* The form's own lines keep their width; a row in it takes the pane's, or its control column
	   lands wherever its words end instead of under the controls of the rows above. */
	.whole {
		align-self: stretch;
	}

	/* A library's folder and, for one that cannot be read, why: each its own line in the row's foot,
	   the path free to break anywhere, since it has no spaces to break at. */
	.path,
	.why,
	.start {
		display: block;
	}

	/* Which library opens when Sift starts, in the row's own ink so it reads as a fact about the row
	   rather than more of its folder. */
	.start {
		color: var(--sift-ink-2);
	}

	.path {
		overflow-wrap: anywhere;
	}

	/* A path inside a sentence ("Created in <path>."): it stays in the line and breaks where it has
	   to, so the full stop after it ends the sentence rather than a line of its own. */
	.inline-path {
		overflow-wrap: anywhere;
	}

	.line,
	.buttons {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-2);
	}

	.label {
		font: var(--text-body);
		color: var(--sift-ink);
	}

	.note {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	/* Said in the reader's own ink rather than the quiet grey: it is the one sentence here about
	   somebody's real files. */
	.warning {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink);
	}
</style>
