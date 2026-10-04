<script lang="ts">
	/* Backup.
	 *
	 * The paragraph at the top is not decoration and is not there to be tidied away. Somebody who
	 * reads "backup" and believes their videos are safe, and then loses a drive, has been failed by
	 * this screen. So what is and is not copied is the first thing said, in the plainest words the
	 * screen has, and it is repeated at the moment of restoring, which is the other moment the
	 * wrong belief costs something.
	 */
	import { onMount } from 'svelte';
	import {
		Button,
		ChooseFile,
		ConfirmDialog,
		LabelledRow,
		Problem,
		SectionHeading,
		Skeleton
	} from '$lib/components/common';
	import ActionRow from './ActionRow.svelte';
	import { Backup } from './backup-state.svelte';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import DatabaseSwitcher from './DatabaseSwitcher.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import FolderChoice from './FolderChoice.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { showSettingsSection } from '$lib/settings-ui/settings-view';
	import { COPY, keptSaid } from './Backup.search';
	import { onRecord } from '$lib/shell/when';
	import type { UnmarkedBackup } from './backup-state.svelte';
	import { filesSaid } from '$lib/entity/entity-counts';
	import LastRun from '$lib/jobs/LastRun.svelte';
	import { runWords } from '$lib/jobs/last-run';
	import { bridge } from '$lib/bridge';
	import { explainAbsentRows } from '$lib/settings-ui/settings-anchor.svelte';

	/* The automatic backup's last run, as its row on Tasks says it: the task's record, so a reload
	   still says when the last one was. */
	const BACKUP_TASK = 'backup';
	const LAST_BACKUP = runWords('The last automatic backup');

	const view = new Backup();

	/* The backups no rule keeps are listed only while there are any. */
	const NONE_UNMARKED = "Every backup in the folder is one a rule keeps, so there's none to list.";
	$effect(() =>
		explainAbsentRows((key) =>
			key === 'backup.unmarked' && view.loaded && view.unmarked.length === 0
				? { because: NONE_UNMARKED, near: 'backup.now' }
				: null
		)
	);

	/* Where Save a backup put its file, the way this device can reach it. The Sift app on the
	   computer running Sift shows it in its folder; a browser, or the app onto a library on another
	   computer, cannot reach that computer's folders, so it reads the folder as text and is offered
	   a copy instead. One press saves, and the second is only for a copy somewhere else. */
	const showsFolder = bridge.canShowInFolder();

	/* The declarations for the rows. The VALUES stay on the view: where they go and how many to
	   keep are saved together by the button below rather than one at a time, because a schedule
	   with nowhere to write to is a half-applied change. */
	const declarations = new SettingsPanel();

	const KEEP_KEY = 'backup.keep';
	const KEEP_DAYS_KEY = 'backup.keep_days';
	const FOLDER_KEY = 'backup.folder';
	const INCLUDE_DETECTED_KEY = 'backup.include_detected_faces';

	/* Save where they go and how many to keep. How often and the time of day are not sent: they
	 * are the task's rows on Scheduled tasks, and the backup route keeps what is stored for a field
	 * left out, so a Save here never puts back a cadence this pane loaded before one was changed
	 * there. */
	async function applySchedule() {
		await view.saveSchedule();
	}

	let chosen = $state<File | null>(null);
	let restoreOpen = $state(false);

	onMount(() => {
		void view.load();
		void view.loadContents();
		void view.loadUnmarked();
		void declarations.load();
	});
	/* The schedule, the folder and what the file holds are settings: moved in another window or by
	   another admin, the pane follows. Nothing on it holds an unsent edit but the chosen file to
	   restore from, which a re-read leaves alone. */
	whenChanged(settingChanges, () => {
		void view.load();
		void view.loadContents();
		void view.loadUnmarked();
	});

	/* The backup a Delete was pressed on, while its confirm is open. */
	let deleting = $state<UnmarkedBackup | null>(null);
	let deleteOpen = $state(false);

	function askToDelete(one: UnmarkedBackup) {
		deleting = one;
		deleteOpen = true;
	}

	async function doDelete() {
		if (!deleting) return;
		await view.deleteUnmarked(deleting.name);
		deleting = null;
	}

	/* The one switch about what the file holds. Written through the settings hub like every other
	   switch, then the sizes are read again so the sentence below says what the next file holds. */
	async function setIncludeDetected(next: unknown) {
		await declarations.save(INCLUDE_DETECTED_KEY, Boolean(next));
		await view.loadContents();
	}

	function sayBytes(bytes: number): string {
		if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
		if (bytes < 1024 * 1024 * 1024) return `${Math.round(bytes / (1024 * 1024))} MB`;
		return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
	}

	/* One sentence, read from the disk: what the next file holds beside the database. The count is
	   `filesSaid`, grouped like every other count: a library's detected faces must not read
	   "24000 files" beside "500 files" on the same line. */
	const holds = $derived.by(() => {
		const taken = view.parts.filter((one) => one.included);
		if (taken.length === 0) return '';
		return taken
			.map((one) => `${one.label.toLowerCase()} (${filesSaid(one.files)}, ${sayBytes(one.bytes)})`)
			.join(', ');
	});

	function pickFile(file: File) {
		chosen = file;
		restoreOpen = true;
	}

	async function doRestore() {
		if (!chosen) return;
		await view.restore(chosen);
		chosen = null;
	}
</script>

<section>
	<p class="lede">
		{COPY.holds} <strong>{COPY.notMedia}</strong>
		{COPY.mediaYours}
	</p>
	<p class="lede">{COPY.comesBack}</p>

	<Problem message={view.problem} />
	{#if view.done}
		<p class="done" role="status">{view.done}</p>
	{/if}

	<!-- ------------------------------------------------------------------- export now -->
	<SectionHeading id="backup.now">{COPY.now.name}</SectionHeading>
	<!-- The press is a row like every act on a settings pane: what it does on the left, the button
	     on the right, and the switch about what the file holds under it in the same group. -->
	<SettingGroup>
		<ActionRow
			label={COPY.now.row}
			help={COPY.fileHolds(holds)}
			action={COPY.now.button}
			icon="save"
			busy={view.busy}
			onclick={() => void view.exportNow()}
		/>
		{#if view.saved}
			{@const saved = view.saved}
			<div class="saved" role="status">
				<p>{COPY.now.savedIn(saved.folder)}</p>
				{#if showsFolder}
					<Button
						tone="secondary"
						size="small"
						icon="folder"
						onclick={() => void bridge.showInFolder(saved.path)}>{COPY.now.show}</Button
					>
				{:else}
					<Button
						tone="secondary"
						size="small"
						icon="download"
						aria-label={COPY.now.copyLabel(saved.name)}
						onclick={() => view.copyOf(saved.name)}>{COPY.now.copy}</Button
					>
				{/if}
			</div>
		{/if}
		{#if !declarations.loading}
			{@const detected = declarations.entry(INCLUDE_DETECTED_KEY)}
			{#if detected}
				<SettingRow
					entry={detected}
					value={declarations.value(INCLUDE_DETECTED_KEY)}
					disabled={view.busy}
					onchange={(next: unknown) => void setIncludeDetected(next)}
				/>
			{/if}
		{/if}
	</SettingGroup>

	<!-- -------------------------------------------------------------------- scheduled -->
	<SectionHeading>{COPY.automatic}</SectionHeading>
	{#if view.loading}
		<Skeleton lines={3} />
	{:else if !view.loaded}
		<!-- Deliberately not "automatic backups are off". A request that failed says nothing about
		     the schedule, and the reassuring reading of silence here is the wrong one. -->
		<Problem message={COPY.notLoaded} />
	{:else}
		<!-- The cadence is not here: How often and Time of day are drawn on Tasks, beside every other
		     thing Sift does on a clock, and nowhere else, since two copies of one control is how two screens
		     come to disagree about what is switched on. What stays is what is about the FILE rather
		     than about when: how many to keep, and where to put them, live whatever the cadence. -->
		<p class="hint">
			<LastRun task={BACKUP_TASK} words={LAST_BACKUP} />
			{COPY.howOften.before}
			<a
				href="/settings/tasks"
				onclick={(event) => {
					event.preventDefault();
					showSettingsSection('tasks');
				}}>{COPY.howOften.link}</a
			>{COPY.howOften.after}
		</p>
		<SettingGroup>
			{@const keep = declarations.entry(KEEP_KEY)}
			{#if keep}
				<!-- Never greyed by the cadence: that is edited under Tasks, and a row dimmed by a
				     choice on another page reads as broken here. The line above says where it is. -->
				<SettingRow
					entry={keep}
					value={view.keep}
					disabled={view.busy}
					onchange={(next: unknown) => (view.keep = Number(next))}
				/>
			{/if}
			{@const keepDays = declarations.entry(KEEP_DAYS_KEY)}
			{#if keepDays}
				<!-- Saved with the count by the button below: the two rules decide one folder. -->
				<SettingRow
					entry={keepDays}
					value={view.keepDays}
					disabled={view.busy}
					onchange={(next: unknown) => (view.keepDays = Number(next) || 0)}
				/>
			{/if}
			{@const folder = declarations.entry(FOLDER_KEY)}
			{#if folder}
				<!-- Chosen by pointing at it, never typed: the folder picker, or in the application
				     the operating system's own folder dialog. -->
				<FolderChoice
					entry={folder}
					value={view.folder}
					empty={COPY.folderEmpty}
					reset={COPY.folderReset}
					onchange={(next) => (view.folder = next)}
				/>
			{/if}
		</SettingGroup>

		<!-- How the count and the age read together, from the values above as they stand. -->
		<p class="hint" data-kept>{keptSaid(view.keep, view.keepDays)}</p>

		{#if view.besideSiftData}
			<!-- True only because a chosen folder is confined to the folders Sift has been given, and
			     kept out of the libraries in them (`BackupService.resolve_folder`). So the advice names
			     where a folder is given, rather than suggesting one the save would refuse. -->
			<p class="hint">
				{COPY.besideData}
			</p>
		{/if}

		<!-- The two above are saved together rather than one at a time: a schedule with nowhere
		     to write to is a half-applied change. -->
		<ActionRow
			label={COPY.apply.label}
			help={COPY.apply.help}
			action={COPY.apply.action}
			busy={view.busy}
			onclick={() => void applySchedule()}
		/>

		{#if view.unmarked.length > 0}
			<!-- The backups no rule takes: why the folder holds more than the number kept. Each is
			     deleted only by its own press, behind a confirm that says where it goes. -->
			<SectionHeading id="backup.unmarked">{COPY.unmarked.name}</SectionHeading>
			<p class="hint">{COPY.unmarked.says}</p>
			<SettingGroup>
				{#each view.unmarked as one (one.name)}
					{@const day = onRecord(one.taken_at)}
					<ActionRow
						label={day}
						help={one.name}
						note={sayBytes(one.size_bytes)}
						action={COPY.unmarked.action}
						actionLabel={COPY.unmarked.actionLabel(day)}
						destructive
						busy={view.busy}
						onclick={() => askToDelete(one)}
					/>
				{/each}
			</SettingGroup>
		{/if}
	{/if}

	<!-- ---------------------------------------------------------------------- restore -->
	<SectionHeading id="backup.restore">{COPY.restore.name}</SectionHeading>
	<SettingGroup>
		<LabelledRow label={COPY.restore.row} help={COPY.restore.help}>
			<ChooseFile
				accept=".zip,.sqlite3"
				disabled={view.busy}
				label={COPY.restore.choose}
				onchoose={pickFile}
			>
				{COPY.restore.choose}
			</ChooseFile>
		</LabelledRow>
	</SettingGroup>

	<!-- ------------------------------------------------------------ database switcher -->
	<!-- Opening a library rather than replacing this one's contents: a backup copy can be looked
	     at in a library of its own, beside the file, without restoring it over this one. -->
	<DatabaseSwitcher />
</section>

<ConfirmDialog
	bind:open={restoreOpen}
	title={COPY.confirm.title}
	consequence={chosen ? COPY.confirm.consequence(chosen.name) : ''}
	confirmLabel={COPY.confirm.confirm}
	destructive
	onconfirm={doRestore}
/>

<ConfirmDialog
	bind:open={deleteOpen}
	title={COPY.unmarked.ask}
	consequence={deleting
		? view.recycleBin
			? COPY.unmarked.binned(deleting.name)
			: COPY.unmarked.forGood(deleting.name)
		: ''}
	confirmLabel={COPY.unmarked.confirm}
	destructive
	onconfirm={doDelete}
/>

<style>
	.lede {
		margin: 0 0 var(--space-4);
		color: var(--sift-ink-2);
		font: var(--text-body);
	}

	.done {
		margin: 0 0 var(--space-4);
		color: var(--sift-ink);
		font: var(--text-body);
	}

	/* Where the press put its file, under the row that made it, with the one way this device has to
	   reach it on the right, as every act on a settings pane sits. */
	.saved {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		/* On the row's own edges: the words start where the row's name does and the button ends
		   where every button on the pane ends. */
		padding: 0 0 var(--space-3);
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	.saved p {
		margin: 0;
		min-inline-size: 0;
		overflow-wrap: anywhere;
	}

	.hint {
		margin: var(--space-2) 0 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}
</style>
