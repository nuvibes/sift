<script lang="ts" module>
	import type { Column } from '$lib/components/common/DataRows.svelte';

	/**
	 * A record's two fact columns: a fixed label track and a wrapping value, shared with the ids.
	 */
	export const FACT_COLUMNS: readonly Column[] = [
		{ id: 'label', width: '9rem' },
		{ id: 'value', width: 'minmax(0, 1fr)' }
	];
</script>

<script lang="ts">
	/* The record facts that need their label, as two aligned columns beside the name; which ones is
	 * the server's (`shown: 'record'`), and an empty field is left out. */
	import DataRows from '$lib/components/common/DataRows.svelte';
	import DataRow from '$lib/components/common/DataRow.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import RecordValue from '$lib/components/record/RecordValue.svelte';
	import { fields, linkFor, type RecordSubject } from '$lib/entity/records.svelte';

	interface Props {
		subject: RecordSubject;
		values: Record<string, unknown>;
		/** Whose facts these are, for a screen reader. */
		label: string;
		/** Where each field came from, said on the value's hover (see `givenBy`). */
		given?: Readonly<Record<string, string>>;
	}

	let { subject, values, label, given = {} }: Props = $props();

	/** The kinds drawn under the name by their shape, with no label. Never here. */
	const BY_SHAPE: ReadonlySet<string> = new Set(['names', 'links']);

	function has(key: string): boolean {
		const held = values[key];
		if (held === null || held === undefined || held === '') return false;
		return !Array.isArray(held) || held.length > 0;
	}

	const shown = $derived(
		fields.onRecord(subject).filter((one) => !BY_SHAPE.has(one.kind) && has(one.key))
	);
</script>

{#if shown.length > 0}
	<DataRows items={shown} key={(one) => one.key} {label} columns={FACT_COLUMNS} edges>
		{#snippet row(one)}
			<DataRow compact cells={{ label: named, value: said }} />
			{#snippet named()}<span class="label">{one.label}</span>{/snippet}
			{#snippet said()}
				{#if given[one.key]}
					<Tooltip label={given[one.key]} placement="top" shrinks>
						<RecordValue kind={one.kind} value={values[one.key]} link={linkFor(one, values)} />
					</Tooltip>
				{:else}
					<RecordValue kind={one.kind} value={values[one.key]} link={linkFor(one, values)} />
				{/if}
			{/snippet}
		{/snippet}
	</DataRows>
{/if}

<style>
	/* The quieter of the two: the label is read past, the value is the fact. */
	.label {
		font: var(--text-label);
		color: var(--sift-ink-3);
	}
</style>
