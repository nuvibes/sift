<script lang="ts">
	/* One folder answering differently from the rest of the library. */
	import { onMount } from 'svelte';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { Fold, Note, Select } from '$lib/components/common';
	import LabelledRow from '$lib/components/common/LabelledRow.svelte';
	import ActionRow from './ActionRow.svelte';
	import SettingGroup from './SettingGroup.svelte';
	import { drilldown } from './drilldown.svelte';
	import { refusalOf } from '$lib/settings-ui/settings';
	import { fetchFolderAnswers, setFolderAnswers, type FolderAnswers } from '$lib/library/importing';
	import { toasts } from '$lib/shell/toasts.svelte';
	import { COPY } from './ImportingFolders.search';

	const FOLLOW = '__follow__';

	const CHOICES = [
		{ value: FOLLOW, label: COPY.follow },
		{ value: 'on', label: COPY.on },
		{ value: 'off', label: COPY.off }
	];

	let folders = $state<FolderAnswers[]>([]);
	let keys = $state<string[]>([]);
	let labels = $state<Record<string, string>>({});
	let helps = $state<Record<string, string>>({});
	let open = $state<string | null>(null);

	onMount(() => void load());
	/* A folder's answer changed on another tab: the settings bell (`importing.store`). */
	whenChanged(settingChanges, () => void load());

	async function load() {
		try {
			const answered = await fetchFolderAnswers();
			folders = answered.folders;
			keys = answered.keys;
			labels = answered.labels ?? {};
			helps = answered.helps ?? {};
		} catch {
			// Left empty.
		}
	}

	/* The server's words for each key on a folder's page: for a key retired into a task's When, what
	   a folder's answer does (only what happens as a file arrives), declared where it was retired. */
	function labelFor(key: string): string {
		return labels[key] ?? key;
	}

	function answerOf(folder: FolderAnswers, key: string): string {
		if (!(key in folder.answers)) return FOLLOW;
		return folder.answers[key] ? 'on' : 'off';
	}

	/** How many switches this folder answers for itself. What its row says without opening it. */
	function differences(folder: FolderAnswers): number {
		return Object.keys(folder.answers).length;
	}

	async function answer(folder: FolderAnswers, key: string, choice: string) {
		const was = { ...folder.answers };
		const next: Record<string, boolean | null> = {
			[key]: choice === FOLLOW ? null : choice === 'on'
		};
		// Shown immediately and put back on a refusal, the same way the pane's own switches are: a
		// control that waits for the server before moving reads as broken on a slow disk.
		if (choice === FOLLOW) delete folder.answers[key];
		else folder.answers[key] = choice === 'on';
		folders = [...folders];
		try {
			const saved = await setFolderAnswers(folder.root_id, next);
			folder.answers = saved.answers;
			folders = [...folders];
		} catch (error) {
			folder.answers = was;
			folders = [...folders];
			toasts.show(refusalOf(error), { tone: 'error' });
		}
	}

	function edit(folder: FolderAnswers) {
		open = folder.root_id;
		drilldown.open(folder.name, page, COPY.edit);
	}

	/* The folder being edited, read out of the list rather than copied into a field of its own,
	   so an answer saved on the sub-page is the same object the row behind it reads. */
	const editing = $derived(folders.find((one) => one.root_id === open) ?? null);
</script>

{#snippet page()}
	{#if editing}
		<SettingGroup help={COPY.pageHelp}>
			{#each keys as key (key)}
				<LabelledRow label={labelFor(key)} help={helps[key]}>
					<Select
						value={answerOf(editing, key)}
						options={CHOICES}
						label={labelFor(key)}
						onValueChange={(choice) => answer(editing, key, choice)}
					/>
				</LabelledRow>
			{/each}
		</SettingGroup>
	{/if}
{/snippet}

<!-- Always drawn, so its address lands on a library with no folders yet; the folders fold under
     the heading, so Import tasks reads as its own rows first, as the Sites under a tunnel do. -->
<SettingGroup id="importing.folders" heading={COPY.heading} help={COPY.intro}>
	{#if folders.length === 0}
		<Note>{COPY.none}</Note>
	{:else}
		<Fold summary={COPY.fold(folders.length)} id="importing-folders">
			{#each folders as folder (folder.root_id)}
				<ActionRow
					label={folder.name}
					action={COPY.edit}
					actionLabel={COPY.editNamed(folder.name)}
					note={differences(folder) === 0 ? COPY.follows : COPY.own(differences(folder))}
					onclick={() => edit(folder)}
				/>
			{/each}
		</Fold>
	{/if}
</SettingGroup>
