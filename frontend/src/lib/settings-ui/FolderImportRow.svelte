<script lang="ts">
	/*
	 * The one row that imports a folder of people, from the press to the task's end.
	 *
	 * Choosing the folder IS the press. On the desktop app it is the operating system's folder
	 * dialog; in a browser it is the folder picker over the folders Sift has, as every folder
	 * setting's is, with a way to send a folder from the device the browser runs on. Either way the
	 * press answers straight away and the row follows the task Sift runs (`folderImport`), the way the
	 * face scan's row does: its bar, its count, Cancel, and its report when it ends.
	 */
	import { onMount } from 'svelte';
	import {
		Button,
		ChooseFile,
		Fold,
		Modal,
		Problem,
		ProgressBar,
		Scroller
	} from '$lib/components/common';
	import FolderPicker from '$lib/library/FolderPicker.svelte';
	import { Picker } from '$lib/library/picker.svelte';
	import { Grants } from '$lib/library/grants-state.svelte';
	import { bridge } from '$lib/bridge';
	import { api } from '$lib/api/client';
	import {
		READING_THE_FOLDER,
		folderImport,
		importFolderByPath,
		importFolderFromDevice,
		refusalOfImport
	} from '$lib/people/folder-import.svelte';
	import ActionRow from './ActionRow.svelte';

	interface Props {
		/** The row's address. */
		id: string;
		label: string;
		help: string;
		/** The press while nothing runs. */
		action: string;
		/** The press while the folder is being handed to Sift. */
		busy: string;
		/** Told once a task has been queued, so the screen can read what changes when it ends. */
		onstarted?: () => void;
	}

	let { id, label, help, action, busy, onstarted }: Props = $props();

	const COPY = {
		title: 'Choose the folder of people',
		list: 'Folders Sift has',
		pickHelp: 'Click through to the folder that holds a subfolder for each person, then import it.',
		use: 'Import this folder',
		close: 'Cancel',
		atTop: 'Click into a folder first: this is the list of folders Sift has, not a folder itself.',
		device: 'Import from this device',
		deviceHelp:
			'For a folder on the device you are using rather than on the computer Sift runs on. Every photo is sent to Sift first, which takes longer.',
		sending: 'Sending the folder to Sift\u2026',
		cancel: 'Cancel import',
		canceling: 'Canceling\u2026',
		bar: 'Importing a folder of people',
		leave:
			'The import continues if you leave this screen, and you can follow or cancel it in Activity. Canceling keeps every face already imported.'
	} as const;

	const picker = new Picker();
	const grants = new Grants();
	const uid = $props.id();
	const listId = `folder-import-${uid}`;

	let open = $state(false);
	let pressedAtTop = $state(false);
	/** The folder is being handed over: a path a moment, an upload as long as its photos take. */
	let sending = $state<'path' | 'upload' | null>(null);
	/** The device's own dialog has closed and the browser is reading the folder it was given. */
	let reading = $state(false);
	let stopping = $state(false);
	let problem = $state<string | null>(null);

	onMount(() => {
		void folderImport.resume();
	});

	async function start(kind: 'path' | 'upload', send: () => Promise<string>): Promise<void> {
		sending = kind;
		problem = null;
		folderImport.outcome = null;
		try {
			folderImport.follow(await send());
			onstarted?.();
		} catch (error) {
			problem = refusalOfImport(error);
		} finally {
			sending = null;
		}
	}

	async function press(): Promise<void> {
		problem = null;
		if (folderImport.running) {
			await cancel();
			return;
		}
		if (bridge.canChooseFolder()) {
			const chosen = await bridge.chooseFolder();
			// Closing the dialog without choosing is not an error and changes nothing.
			if (chosen === null) return;
			// The dialog is the consent: the folder joins the folders Sift has, as a library's does.
			await grants.ensure(chosen);
			await start('path', () => importFolderByPath(chosen));
			return;
		}
		pressedAtTop = false;
		await picker.open();
		open = true;
	}

	function useChosen(event: SubmitEvent): void {
		event.preventDefault();
		const chosen = picker.selected;
		if (picker.atTopLevel || !chosen) {
			pressedAtTop = true;
			return;
		}
		open = false;
		void start('path', () => importFolderByPath(chosen.path));
	}

	/*
	 * A browser hands over nothing until it has listed every file in the folder, which for a
	 * gallery on a network share is half a minute with no event at all. Its dialog closing gives
	 * focus back to the page, so that is when the row starts saying it is reading.
	 */
	function armReading(): void {
		const settle = () => {
			window.removeEventListener('focus', onFocus);
			window.removeEventListener('cancel', onCancel, true);
		};
		const onFocus = () => {
			reading = true;
			settle();
		};
		const onCancel = () => {
			reading = false;
			settle();
		};
		window.addEventListener('focus', onFocus);
		window.addEventListener('cancel', onCancel, true);
	}

	function fromDevice(files: File[]): void {
		reading = false;
		open = false;
		void start('upload', () => importFolderFromDevice(files));
	}

	async function cancel(): Promise<void> {
		const jobId = folderImport.jobId;
		if (!jobId || stopping) return;
		stopping = true;
		try {
			await api.post(`/jobs/${jobId}/cancel`);
		} catch {
			problem = "Couldn't cancel the import. Cancel it in Activity.";
		} finally {
			stopping = false;
		}
	}

	const pressSays = $derived(
		folderImport.running
			? stopping
				? COPY.canceling
				: COPY.cancel
			: sending !== null || reading
				? busy
				: action
	);
</script>

<ActionRow
	{id}
	{label}
	{help}
	action={pressSays}
	icon={folderImport.running ? undefined : 'upload'}
	busy={sending !== null || reading || stopping}
	onclick={() => void press()}
>
	{#if reading}
		<p class="status" role="status">{READING_THE_FOLDER}</p>
	{:else if sending === 'upload'}
		<p class="status" role="status">{COPY.sending}</p>
	{:else if folderImport.running}
		{#if !folderImport.waiting}
			<ProgressBar value={folderImport.fraction * 100} label={COPY.bar} />
		{/if}
		<p class="status" role="status">{folderImport.status}</p>
		<p class="small">{COPY.leave}</p>
	{:else if folderImport.outcome}
		<p class="status" class:ready={folderImport.succeeded}>{folderImport.outcome}</p>
		{#each folderImport.folds as fold (fold.summary)}
			<Fold summary={fold.summary}>
				<div class="files">
					<Scroller>
						<ul>
							{#each fold.files as file (file)}
								<li>{file}</li>
							{/each}
						</ul>
					</Scroller>
				</div>
			</Fold>
		{/each}
	{/if}
	<Problem message={problem} />
</ActionRow>

<Modal bind:open title={COPY.title}>
	<form class="folder-import" onsubmit={useChosen}>
		<div class="body">
			<span class="label" id={listId}>{COPY.list}</span>
			<FolderPicker {picker} labelledBy={listId} describedBy="{listId}-help" />
			<p class="help" id="{listId}-help">{COPY.pickHelp}</p>
			{#if pressedAtTop && picker.atTopLevel}
				<p class="warn" role="alert">{COPY.atTop}</p>
			{/if}
			{#if reading}
				<p class="help" role="status">{READING_THE_FOLDER}</p>
			{/if}
		</div>
		<div class="device">
			<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
			<span onclickcapture={armReading}>
				<ChooseFile
					directory
					label={COPY.device}
					icon="upload"
					busy={reading}
					onchooseAll={fromDevice}
				>
					{COPY.device}
				</ChooseFile>
			</span>
			<p class="help">{COPY.deviceHelp}</p>
		</div>
		<div class="dialog-actions">
			<Button onclick={() => (open = false)}>{COPY.close}</Button>
			<Button type="submit" tone="primary" icon="upload">{COPY.use}</Button>
		</div>
	</form>
</Modal>

<style>
	/* The row's lines under its help, as the other rows of Settings > Faces draw theirs. */
	.small {
		margin: 0;
		font: var(--text-body-sm);
	}

	.status {
		margin: 0;
		color: var(--sift-ink-3);
	}

	.status.ready {
		color: var(--sift-ink);
	}

	/* The files under one count of the report, one to a line, held to the list's height. */
	.files :global(.scroll-root) {
		max-block-size: var(--settings-list-cap);
	}

	.files ul {
		margin: 0;
		padding: 0;
		list-style: none;
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		font: var(--text-body-sm);
	}

	.folder-import {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		margin-block-start: var(--space-4);
	}

	.body,
	.device {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
	}

	.device {
		align-items: flex-start;
	}

	.label {
		font: var(--text-label);
		color: var(--sift-ink-2);
	}

	.help {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-ink-3);
	}

	.warn {
		margin: 0;
		font: var(--text-body-sm);
		color: var(--sift-warn);
	}

	/* The way out, then the act, at the sheet's far edge, as every sheet's foot. */
	.dialog-actions {
		display: flex;
		gap: var(--space-2);
	}

	.dialog-actions > :global(:first-child) {
		margin-inline-start: auto;
	}
</style>
