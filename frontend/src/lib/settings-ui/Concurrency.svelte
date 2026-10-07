<script lang="ts">
	/*
	 * Performance's settings: Concurrency (one row, its numbers a page behind Edit), and the step
	 * back's two causes with its share, which lower those numbers. One group, one panel.
	 */
	import { onMount } from 'svelte';
	import { Button, LabelledRow, SettingLink } from '$lib/components/common';
	import SettingGroup from './SettingGroup.svelte';
	import SettingRow from './SettingRow.svelte';
	import { drilldown } from './drilldown.svelte';
	import { SettingsPanel } from './panel.svelte';
	import { explainAbsentRows, hiddenBy } from '$lib/settings-ui/settings-anchor.svelte';
	import { COPY, CONCURRENCY_ANCHOR } from './Performance.search';

	/* How much Sift does at the same time, the four numbers on Concurrency's page. */
	const AT_ONCE_KEYS = [
		'performance.worker_count',
		'performance.generation_limit',
		'performance.scan_limit',
		'performance.share_reads_at_once'
	] as const;
	const STEP_BACK = [
		'performance.step_back_while_used',
		'performance.step_back_while_busy',
		'performance.step_back_share'
	];

	const panel = new SettingsPanel();
	onMount(() => void panel.load());

	/* A row drawn only while another setting holds one value: two of the answers under
	   recognition's budget have exactly one row that belongs to them. */
	interface Field {
		key: string;
		when?: { key: string; is: string };
	}
	const BUDGET = 'faces.machine_budget';
	const FACES_FIELDS: Field[] = [
		{ key: BUDGET },
		{ key: 'faces.core_share', when: { key: BUDGET, is: 'share' } },
		{ key: 'faces.thread_count', when: { key: BUDGET, is: 'threads' } }
	];
	/* In quiet-hours mode the range is the one on Tasks, so the page points there. */
	const NIGHTLY = { key: BUDGET, is: 'nightly' };

	/** Whether a row drawn under a condition applies now. A condition on a value not read yet does
	 * not, so a row never flashes in and out while the values load. */
	function holds(when: { key: string; is: string } | undefined): boolean {
		if (!when) return true;
		return String(panel.value(when.key) ?? '') === when.is;
	}

	function open(): void {
		drilldown.open(COPY.much.name, page, COPY.much.edit);
	}
	$effect(() => drilldown.own([...AT_ONCE_KEYS, ...FACES_FIELDS.map((one) => one.key)], open));
	/* A budget row the answer above it leaves out: that answer is rung in its place, with why. */
	$effect(() =>
		explainAbsentRows((key) => {
			const field = FACES_FIELDS.find((one) => one.key === key);
			if (!field?.when || holds(field.when)) return null;
			const because = hiddenBy(
				panel.entry(key),
				panel.entry(field.when.key),
				panel.value(field.when.key)
			);
			return because ? { because, near: field.when.key } : null;
		})
	);
</script>

{#snippet page()}
	<SettingGroup heading={COPY.much.atOnce}>
		{#each panel.pick(...AT_ONCE_KEYS) as entry (entry.key)}
			<SettingRow
				{entry}
				value={panel.value(entry.key)}
				onchange={(next) => panel.save(entry.key, next)}
			/>
		{/each}
	</SettingGroup>
	<SettingGroup heading={COPY.much.faces} help={COPY.much.facesHelp}>
		{#each FACES_FIELDS as field (field.key)}
			{@const entry = holds(field.when) ? panel.entry(field.key) : undefined}
			{#if entry}
				<!-- The group's paragraph stands for a column of numbers; a CHOICE keeps its own
				     sentence, because the paragraph cannot say what each answer means. -->
				<SettingRow
					{entry}
					value={panel.value(entry.key)}
					showHelp={(entry.choices?.length ?? 0) > 0}
					onchange={(next) => panel.save(field.key, next)}
				/>
			{/if}
		{/each}
		{#if holds(NIGHTLY)}
			<p class="note-line" data-testid="quiet-hours-link">
				{COPY.much.quietHours}
				<SettingLink section="schedule" setting="tasks.quiet-hours"
					>{COPY.much.quietHoursLink}</SettingLink
				>
			</p>
		{/if}
	</SettingGroup>
{/snippet}

{#if panel.pick(...AT_ONCE_KEYS, ...STEP_BACK).length > 0}
	<SettingGroup>
		{#if panel.pick(...AT_ONCE_KEYS).length > 0}
			<LabelledRow id={CONCURRENCY_ANCHOR} label={COPY.much.name} help={COPY.much.help}>
				<Button icon="edit" onclick={open} aria-label={COPY.much.editLabel}>{COPY.much.edit}</Button
				>
			</LabelledRow>
		{/if}
		{#each panel.pick(...STEP_BACK) as entry (entry.key)}
			<SettingRow
				{entry}
				value={panel.value(entry.key)}
				onchange={(next) => panel.save(entry.key, next)}
			/>
		{/each}
	</SettingGroup>
{/if}

<style>
	/* The pointer at quiet hours, a paragraph in the pane's measure. */
	.note-line {
		margin: var(--space-2) 0 0;
		color: var(--sift-ink-3);
		font: var(--text-body-sm);
	}
</style>
