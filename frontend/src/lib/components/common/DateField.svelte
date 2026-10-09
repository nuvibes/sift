<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'DateField',
		category: 'control',
		role: 'a date typed one segment at a time, with a calendar behind it',
		basis: 'bits-ui:DateField',
		states: ['empty', 'filled', 'disabled']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/* One date, typed in segments: the library's segments, no calendar (a birthdate is not pointed
	   at), and a `YYYY-MM-DD` string so no `toISOString()` shifts the day. */
	import { DateField } from 'bits-ui';
	import { CalendarDate, type DateValue } from '@internationalized/date';

	interface Props {
		/** The day, as the column holds it: `YYYY-MM-DD`, or empty for no date. */
		value?: string;
		/** Given the new day, or the empty string when it has been cleared. */
		onchange?: (value: string) => void;
		id?: string;
		/** Names the control where nothing else does. Inside a `Field` the label already has. */
		label?: string;
		/** aria-describedby, for the help text a `Field` draws beside it. */
		describedBy?: string;
		/** Shown and not changeable. The library marks every segment, and the box dims for it. */
		disabled?: boolean;
	}

	let { value = '', onchange, id, label, describedBy, disabled = false }: Props = $props();

	/* Text in, text out; a value that is not a date is no date, not an error. */
	function toValue(day: string | undefined): DateValue | undefined {
		const parts = day?.match(/^(\d{4})-(\d{2})-(\d{2})$/);
		if (!parts) return undefined;
		return new CalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]));
	}

	function toText(day: DateValue | undefined | null): string {
		if (!day) return '';
		return `${String(day.year).padStart(4, '0')}-${String(day.month).padStart(2, '0')}-${String(day.day).padStart(2, '0')}`;
	}

	const picked = $derived(toValue(value));
</script>

<DateField.Root value={picked} {disabled} onValueChange={(next) => onchange?.(toText(next))}>
	<!-- This file's wrapper draws the box; the segments use the shared `.segment` rule. -->
	<div class="date-field" role="group" aria-label={label}>
		<DateField.Input>
			{#snippet children({ segments })}
				<!-- Keyed by position: a date has several `literal` segments. -->
				{#each segments as { part, value: text }, at (at)}
					<DateField.Segment
						{part}
						class="segment"
						id={part === 'day' ? id : undefined}
						aria-describedby={part === 'day' ? describedBy : undefined}>{text}</DateField.Segment
					>
				{/each}
			{/snippet}
		</DateField.Input>
	</div>
</DateField.Root>
