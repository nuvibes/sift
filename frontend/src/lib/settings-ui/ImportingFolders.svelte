<script lang="ts">
	/*
	 * One folder answering differently from the rest of the library.
	 *
	 * ## Why three answers and not a switch
	 *
	 * "Follow the library" is a real third answer and it is the one nearly every folder is in. A
	 * switch has nowhere to put it: a folder stored as `false` and a folder that simply follows a
	 * library set to `false` look identical today and stop looking identical the moment somebody
	 * changes the library's answer: one moves with it and the other does not. So the control has
	 * three options and an absent override is shown as what it is.
	 *
	 * ## Why the list of what can be answered comes from the server
	 *
	 * It is the same map that gates the jobs, minus the two consent switches. See
	 * `ImportPolicy.overridable`. Written out here as well, it would be a second list to keep true,
	 * and the failure would be a control that saves a value nothing ever reads.
	 *
	 * ## And so does what each one is called
	 *
	 * Several of those keys are retired into the task Whens (a folder's answer is still stored
	 * under the old key, which is what the gates read), and a retired key has no registered row,
	 * so a label read off the rows the pane has loaded would show it as a raw key. The list carries
	 * each key's name (`labels`), and a retired key is called by the task it became; the key itself
	 * is only the last resort for a list from an older server.
	 */
	import { onMount } from 'svelte';
	import { settingChanges, whenChanged } from '$lib/library/changes.svelte';
	import { Fold, Select } from '$lib/components/common';
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
			// Left empty. The library's own switches above are unaffected by this failing, and a
			// folder list that could not be read is better absent than drawn as "every folder follows
			// the library", which would be a statement, and might be false.
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

	/* The folder being edited, read out of the list rather than copied into a field of its own, so
	   an answer saved on the sub-page is the same object the row behind it reads. */
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

{#if folders.length > 0}
	<!-- The folders fold under the heading, so Importing reads as its own rows first and a list of
	     every library folder is opened on purpose; the same fold as the Sites under a tunnel. -->
	<SettingGroup heading={COPY.heading} help={COPY.intro}>
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
	</SettingGroup>
{/if}
