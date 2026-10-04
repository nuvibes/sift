<script lang="ts">
	/*
	 * Where Sift keeps its two folders on the computer running it, and moving them, from another
	 * computer.
	 *
	 * The same two rows `StorageFolders` draws in the app on that computer, asked through the server,
	 * which asks the Sift app there. The folder to move into is chosen in the server's own folder
	 * browser over that computer's drives (the `machine` walk every browser already uses to add a
	 * folder), never typed: every path sent is one the server handed back. The app there refuses a
	 * folder that is not empty, not writable, on a network drive or inside the one in use, before
	 * anything stops.
	 *
	 * Taken on, Sift stops there, moves and starts again, which takes as long as copying the library
	 * does across drives, so the page waits for a new run of the server for a long while and says
	 * what is happening. A move that failed after it started is said when Sift is back, from the
	 * app's own record of it.
	 */
	import { onMount } from 'svelte';
	import { Button, Note, Problem } from '$lib/components/common';
	import Modal from '$lib/components/common/Modal.svelte';
	import FolderPicker from '$lib/library/FolderPicker.svelte';
	import { Picker } from '$lib/library/picker.svelte';
	import {
		actThere,
		moveServerStorage,
		readServerStorage,
		type ServerStorage,
		type Wait
	} from '$lib/desktop/server-shell';
	import ActionRow from './ActionRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { formatBytes } from './maintenance-state.svelte';
	import { COPY } from './StorageFolders.search';

	/** Half an hour: a copy across drives of a large library is minutes, not seconds. */
	const MOVE_WAIT_MS = 30 * 60_000;

	interface Props {
		machine: string | null;
		/** How long to wait for Sift to come back, and how the page is loaded then; for the tests. */
		wait?: Wait;
	}

	let { machine, wait = { limitMs: MOVE_WAIT_MS, arrive: () => location.reload() } }: Props =
		$props();

	const WORDS = COPY.server;
	const picker = new Picker();
	const uid = $props.id();
	const listId = `server-storage-${uid}`;

	let report = $state<ServerStorage | null>(null);
	let choosing = $state(false);
	let pressedAtTop = $state(false);
	let moving = $state(false);
	let problem = $state<string | null>(null);

	onMount(() => {
		void readServerStorage().then((answer) => (report = answer));
	});

	/* A move that was taken on and then failed: said once Sift is back, from the app's record. */
	const lastFailed = $derived(
		report?.last_move && !report.last_move.ok ? WORDS.lastFailed(report.last_move.refusal) : null
	);

	async function choose() {
		pressedAtTop = false;
		problem = null;
		/* That computer's drives, from the top each time: the folders Sift was given are where the
		   media is, and the empty folder a library moves into is usually somewhere else. */
		picker.scope = 'machine';
		await picker.open();
		choosing = true;
	}

	async function moveHere(event: SubmitEvent) {
		event.preventDefault();
		const chosen = picker.selected;
		if (picker.atTopLevel || !chosen) {
			pressedAtTop = true;
			return;
		}
		choosing = false;
		moving = true;
		problem = null;
		const outcome = await actThere(
			() => moveServerStorage(chosen.path),
			WORDS.cannot,
			WORDS.slow,
			wait
		);
		/* On ok the page is already loading again from the new run; nothing here is put back. */
		if (outcome.ok) return;
		moving = false;
		problem = outcome.problem;
	}
</script>

<SettingGroup id="library.storage_folders" heading={COPY.name} help={WORDS.help(machine)} />

{#if report}
	<ActionRow
		label={COPY.library.label}
		help={COPY.library.help(report.data_dir)}
		note={formatBytes(report.data_bytes)}
		action={COPY.move}
		icon="folder"
		busy={moving}
		disabled={moving}
		onclick={() => void choose()}
	/>

	<ActionRow
		label={COPY.generated.label}
		help={COPY.generated.help(report.cache_dir)}
		note={formatBytes(report.cache_bytes)}
		action={COPY.move}
		icon="folder"
		busy={moving}
		disabled={moving}
		onclick={() => void choose()}
	/>
{/if}

{#if moving}
	<Note>{WORDS.moving(machine)}</Note>
{/if}
<Problem message={problem ?? lastFailed} />

<Modal bind:open={choosing} title={WORDS.title}>
	<form class="folder-choice" onsubmit={moveHere}>
		<div class="body">
			<span class="label" id={listId}>{WORDS.drives}</span>
			<FolderPicker {picker} labelledBy={listId} describedBy="{listId}-help" />
			<p class="help" id="{listId}-help">{WORDS.pickerHelp}</p>
			{#if pressedAtTop && picker.atTopLevel}
				<p class="warn" role="alert">{WORDS.atTop}</p>
			{/if}
		</div>
		<div class="dialog-actions">
			<Button onclick={() => (choosing = false)}>{WORDS.cancel}</Button>
			<Button type="submit" tone="primary" icon="check">{WORDS.here}</Button>
		</div>
	</form>
</Modal>

<style>
	/* The sheet `FolderChoice` draws for a folder setting: the list, its help, and the foot. */
	.folder-choice {
		display: flex;
		flex-direction: column;
		gap: var(--space-4);
		margin-block-start: var(--space-4);
	}

	.body {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
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
