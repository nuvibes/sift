<script lang="ts" module>
	import type { Column } from '$lib/components/common/DataRows.svelte';

	/**
	 * The two columns every labelled fact of a record stands on: the label a fixed track, so the
	 * values start on one line down the list; the value takes the rest and wraps inside it. Declared
	 * once and exported, because a second list under the facts (the stash-box ids) stands on the same
	 * two columns, and two copies of one width drift.
	 */
	export const FACT_COLUMNS: readonly Column[] = [
		{ id: 'label', width: '9rem' },
		{ id: 'value', width: 'minmax(0, 1fr)' }
	];
</script>

<script lang="ts">
	/*
	 * The facts a record shows without being asked, the ones that need their label (a birthdate, a
	 * nationality), as two aligned columns: the label, then the value.
	 *
	 * Beside the name in an entity's header, in the column the rest of the record opens into. Under
	 * the name are the facts whose shape already says what they are (other names as chips, links as
	 * marks); a fact that needs its label is a line of a list, and five of them laid out each by its
	 * own length read as five sentences rather than one list. So the columns are declared once, on
	 * the list, and every row obeys them.
	 *
	 * Which fields these are is the server's (`shown: 'record'`). A field with nothing in it is left
	 * out, as it is under the name: a column of dashes above the files reads as a page that failed.
	 * The whole record, empty fields and all, is the panel beside it.
	 */
	import DataRows from '$lib/components/common/DataRows.svelte';
	import DataRow from '$lib/components/common/DataRow.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import RecordValue from '$lib/components/record/RecordValue.svelte';
	import { fields, linkFor, type RecordSubject } from '$lib/entity/records.svelte';

	interface Props {
		subject: RecordSubject;
		/** The values, by field key. */
		values: Record<string, unknown>;
		/** Whose facts these are, for a screen reader. */
		label: string;
		/** Where a field came from, by key: "From StashDB, fetched Sep 25, 2026", said on the
		 *  value's hover. A field a person typed, or no box gave, has none. See `givenBy`. */
		given?: Readonly<Record<string, string>>;
	}

	let { subject, values, label, given = {} }: Props = $props();

	/** The kinds drawn under the name by their shape, with no label. Never here. */
	const BY_SHAPE: ReadonlySet<string> = new Set(['names', 'links']);

	/** Whether a field has anything to say. */
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
