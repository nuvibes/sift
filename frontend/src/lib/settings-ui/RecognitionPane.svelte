<script lang="ts" module>
	/* One page layout for every Recognition section: Faces, Smart Search, Watermarks,
	 * Stash-boxes */
	import type { Snippet } from 'svelte';
	import { drilldown } from './drilldown.svelte';

	/** A page one level in: More settings. */
	export interface SubPage {
		title: string;
		/** The setting keys on the page, so a deep link to one of them opens the page first. */
		keys: readonly string[];
		body: Snippet;
		door?: string;
	}

	/** The heading over the way out, since a row with no heading continues the group above it. */
	export const DANGER_HEADING = 'Start over';

	/** What the task's row is called here: what it decides, since the title names the thing. */
	const WHEN_LABEL = 'When it runs';

	/** Open one of the pages; its anchor is in the pane's markup, where the search gate reads it. */
	export function openPage(page: SubPage): void {
		drilldown.open(page.title, page.body, page.door);
	}

	/** What a device choice is called, read off the setting's own declaration. */
	export function deviceWords(
		entry: { choices?: unknown[] | null; choice_labels?: string[] | null } | undefined,
		value: string | null | undefined
	): string {
		// No device in the answer yet (the settings still loading) is no words, not a throw.
		if (!value) return '';
		const at = (entry?.choices ?? []).map(String).indexOf(value);
		return (at >= 0 ? entry?.choice_labels?.[at] : undefined) ?? value.toUpperCase();
	}
</script>

<script lang="ts">
	import SettingGroup from './SettingGroup.svelte';
	import TaskWhen from './TaskWhen.svelte';
	import RecognitionNote from './RecognitionNote.svelte';

	interface Props {
		/** The anchor on the top block, for the search result that names the feature itself. */
		id: string;
		/** The top row: the feature's switch, or where it is switched (`SwitchPointer`). */
		consent: Snippet;
		/** Whether it is switched on. Off, the page is the switch, the status and the way out. */
		on: boolean;
		/** One line saying where it stands, every number in it read from the feature's own route. */
		status: string | null;
		statusReady?: boolean;
		statusCaution?: boolean;
		/** What is in flight or worth a warning, inside the status's box: a run's bar, a notice. */
		work?: Snippet;
		/** What has to happen before it can run, as rows under the box: the model download. */
		setup?: Snippet;
		/** How thorough, where the feature has a dial of its own for it. Drawn while on. */
		thorough?: Snippet;
		/** The task this feature's work runs as. Its When row is drawn from `/api/tasks`. */
		task: string;
		/** The rows that open a page one level in (More settings), under the When row. */
		rows?: Snippet;
		/** The pages those rows open, claimed here so a deep link to a row on one opens it first. */
		pages?: SubPage[];
		/** Blocks drawn after the rows and before the way out: a list, a form of the pane's own. */
		always?: Snippet;
		/** The act that deletes what the feature collected. Always last. */
		danger?: Snippet;
	}

	let {
		id,
		consent,
		on,
		status,
		statusReady = false,
		statusCaution = false,
		work,
		setup,
		thorough,
		task,
		rows,
		pages = [],
		always,
		danger
	}: Props = $props();

	/* Claimed in an effect: the keys usually arrive with a request, after construction. */
	$effect(() => {
		const releases = pages.map((page) => drilldown.own(page.keys, () => openPage(page)));
		return () => {
			for (const release of releases) release();
		};
	});
</script>

<SettingGroup {id}>
	{@render consent()}
	{#if status !== null}
		<RecognitionNote {status} ready={statusReady} caution={statusCaution} children={work} />
	{:else}
		{@render work?.()}
	{/if}
	{@render setup?.()}
</SettingGroup>

<!-- The When row is the task's own row, drawn here and on Tasks by one component. -->
{#if on || rows}
	<SettingGroup>
		{#if on}
			{@render thorough?.()}
			<TaskWhen {task} label={WHEN_LABEL} press={false} />
		{/if}
		{@render rows?.()}
	</SettingGroup>
{/if}

{@render always?.()}

<!-- LAST: the one act here that cannot be undone sits where nobody reaches it on the way. -->
{@render danger?.()}
