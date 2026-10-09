<script lang="ts">
	/* A setting that holds a folder, chosen by pointing at it rather than typed. */
	import { Button } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import Modal from '$lib/components/common/Modal.svelte';
	import PathText from '$lib/components/PathText.svelte';
	import FolderPicker from '$lib/library/FolderPicker.svelte';
	import { Picker } from '$lib/library/picker.svelte';
	import { bridge } from '$lib/bridge';
	import type { SettingEntry } from '$lib/settings-ui/settings';

	interface Props {
		/** The setting's declaration: its key (the row's address), its name and its help. */
		entry: SettingEntry;
		/** The folder chosen now, or empty for the default place. */
		value: string;
		/** What an empty value means, said in the row: "Beside Sift's own data". */
		empty: string;
		/** The press that goes back to the default place, where there is one. */
		reset?: string;
		onchange: (folder: string) => void;
	}

	let { entry, value, empty, reset, onchange }: Props = $props();

	const COPY = {
		choose: 'Choose\u2026',
		title: (name: string) => `Choose the ${name.toLowerCase()}`,
		list: 'Folders Sift has',
		help: 'Click through to the folder you want, then choose it.',
		use: 'Use this folder',
		cancel: 'Cancel',
		atTop: 'Click into a folder first: this is the list of folders Sift has, not a folder itself.'
	} as const;

	const picker = new Picker();
	let open = $state(false);
	let pressedAtTop = $state(false);
	const uid = $props.id();
	const listId = `folder-choice-${uid}`;

	async function choose(): Promise<void> {
		if (bridge.canChooseFolder()) {
			const chosen = await bridge.chooseFolder();
			// Closing the dialog without choosing is not an error and changes nothing.
			if (chosen !== null) onchange(chosen);
			return;
		}
		pressedAtTop = false;
		await picker.open();
		open = true;
	}

	function use(event: SubmitEvent): void {
		event.preventDefault();
		const chosen = picker.selected;
		if (picker.atTopLevel || !chosen) {
			pressedAtTop = true;
			return;
		}
		onchange(chosen.path);
		open = false;
	}
</script>

<LabelledRow
	id={entry.key}
	label={entry.label ?? entry.key}
	help={entry.help ?? undefined}
	wide
	besideField
>
	<span class="chosen">
		{#if value}<PathText path={value} />{:else}{empty}{/if}
	</span>
	{#if value && reset}
		<Button tone="ghost" onclick={() => onchange('')}>{reset}</Button>
	{/if}
	<Button tone="secondary" icon="folder" onclick={() => void choose()}>{COPY.choose}</Button>
</LabelledRow>

<Modal bind:open title={COPY.title(entry.label ?? entry.key)}>
	<form class="folder-choice" onsubmit={use}>
		<div class="body">
			<span class="label" id={listId}>{COPY.list}</span>
			<FolderPicker {picker} labelledBy={listId} describedBy="{listId}-help" />
			<p class="help" id="{listId}-help">{COPY.help}</p>
			{#if pressedAtTop && picker.atTopLevel}
				<p class="warn" role="alert">{COPY.atTop}</p>
			{/if}
		</div>
		<div class="dialog-actions">
			<Button onclick={() => (open = false)}>{COPY.cancel}</Button>
			<Button type="submit" tone="primary" icon="check">{COPY.use}</Button>
		</div>
	</form>
</Modal>

<style>
	/* The folder, as long as it is, cut at the start of the column rather than widening it. */
	.chosen {
		flex: 1 1 0;
		min-inline-size: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		color: var(--sift-ink-2);
		font: var(--text-body);
		text-align: end;
	}

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
