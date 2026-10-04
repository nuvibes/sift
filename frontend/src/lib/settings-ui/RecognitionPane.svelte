<script lang="ts" module>
	/*
	 * One page layout for every Recognition section: Faces, Smart Search, Watermarks and Stash-boxes.
	 *
	 * What a person actually decides about any of these is whether it is on, how thorough it is and
	 * when it runs. Everything else is for somebody who wants to tune it, and it goes one page in,
	 * behind More settings, so the four sections read the same way from the top, and the one
	 * that deletes things is always the last thing on the page.
	 *
	 * ## A layout, not a second mechanism
	 *
	 * Nothing here is new machinery. The rows are `SettingGroup` and `ActionRow`, the pages behind
	 * More settings are the one settings sub-page (`drilldown`, drawn by
	 * `DrilldownPage` in the frame), and the When row is the task's own `TaskWhen`: the same row
	 * Tasks draws, so a task's When is one setting with two doors. `PresetGroup` was the other
	 * candidate and is the wrong one: it is a master control that READS its children, and none of
	 * these pages has such a master. Using it would mean inventing a bundle over several dials,
	 * which is the thing this layout refuses: How thorough is a real setting of the feature's own,
	 * never a Quick/Balanced/Thorough invented over the rows behind More settings.
	 *
	 * What this component owns is the ORDER, once, for four sections: the switch with where it
	 * stands in the shaded box under it (`RecognitionNote`); what has to happen before it can run;
	 * how thorough and when it runs; the pages one level in; the lists the section owns; and the
	 * way out last. Four panes each writing the order out is four chances for a delete button to
	 * drift above the switch.
	 */
	import type { Snippet } from 'svelte';
	import { drilldown } from './drilldown.svelte';

	/** A page one level in: More settings. */
	export interface SubPage {
		/** What the page is called at the top of it, and on the row that opens it. */
		title: string;
		/**
		 * The setting keys drawn on the page, so a deep link to one of them opens the page first.
		 * Declared rather than inferred: nothing can read a snippet to find out what it will draw.
		 */
		keys: readonly string[];
		body: Snippet;
		/** The words on the press that opens it, a crumb of the settings path under it. */
		door?: string;
	}

	/** The heading over the way out. A heading of its own, because the group before it can be a
	 *  list (Faces' Folder of people), and a row with no heading continues the group above it. */
	export const DANGER_HEADING = 'Start over';

	/** What the task's row is called here. The section's title already names the thing, so the
	 *  row says what it decides; on Tasks the same row carries the task's own title. */
	const WHEN_LABEL = 'When it runs';

	/** Open one of the pages. The row that calls it is the pane's own, so its anchor is written
	 *  out in the pane's markup where the settings search's gate can read it. */
	export function openPage(page: SubPage): void {
		drilldown.open(page.title, page.body, page.door);
	}

	/**
	 * What a device choice is called, read off the setting's own declaration.
	 *
	 * The status line says "Running on the GPU", and the word is the menu's own label for the value
	 * that is set, so the line and the menu under More settings cannot name one device two ways
	 * (a hand-written table here would say "Cpu").
	 */
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
		/** The top row: the feature's switch, or for a recognition feature the row saying where it is
		    switched (`SwitchPointer`), drawn by the pane from its registered setting. */
		consent: Snippet;
		/** Whether it is switched on. Off, the page is the switch, the status and the way out. */
		on: boolean;
		/**
		 * One line saying where it stands, every number in it read from the feature's own route.
		 * Null while that route has not answered, so nothing is claimed before it has.
		 */
		status: string | null;
		/** Whether the status is good news, drawn with the good-news mark. */
		statusReady?: boolean;
		/** Switched on and unable to run: drawn with the caution mark. */
		statusCaution?: boolean;
		/** What is in flight or worth a warning, inside the status's box: a run's bar, a notice. */
		work?: Snippet;
		/** What has to happen before it can run, as rows under the box: the model download. */
		setup?: Snippet;
		/** How thorough, where the feature has a dial of its own for it. Drawn while on. */
		thorough?: Snippet;
		/** The task this feature's work runs as. Its When row is drawn from `/api/tasks`. */
		task: string;
		/**
		 * The rows that open a page one level in (More settings), written by the
		 * pane, under the When row. Drawn whether or not it is on: the pane decides which it offers.
		 */
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

	/* Claimed in an effect, not once at setup: the keys usually arrive with a request, and a claim
	   made at construction would claim an empty list. The teardown releases every claim, which also
	   covers the pane going away and a page the pane stops offering. */
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

<!-- The When row is the task's own row, drawn here and on Tasks by one component: a task's When is
     one setting with two doors. Its press lives on Tasks, so it is drawn here without one. Nothing
     is drawn for it while the feature is off; the page says what to do instead in its status line. -->
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

<!-- LAST, whatever else the page holds. The one act here that cannot be undone sits where nobody
     reaches it on the way to something else. The pane draws it in a group headed `DANGER_HEADING`,
     since only the pane knows whether there is anything to delete. -->
{@render danger?.()}
