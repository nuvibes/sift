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
	/*
	 * ONE date, typed in segments.
	 *
	 * ## Why this and not the browser's own date box
	 *
	 * `<input type="date">` draws the site's calendar glyph and the site's popup, in the
	 * operating system's look, at a size and in a place the page has no say over: the same
	 * objection `NumberInput` answers, and the same answer: keep the behaviour, draw the
	 * control.
	 *
	 * ## Why there is no calendar on it
	 *
	 * `DateRange` has one because "that week in August" is a thing somebody points at. A birthdate
	 * is not: reaching 1991 from a calendar that steps a month at a time is four hundred presses,
	 * and every one of them is somebody who knew the answer before they started. What is worth
	 * having from the library is the SEGMENTS: day, month and year each focusable on their own, so
	 * arrow keys step the month without touching the year, typing `13` into a month rolls into the
	 * next part rather than being accepted, and the order of the parts follows the reader's locale
	 * instead of an assumption.
	 *
	 * ## Why it hands back a string
	 *
	 * `YYYY-MM-DD`, which is what the column holds and what every source of a date sends. A caller
	 * given a date object would convert it back, and the first one to reach for `toISOString()`
	 * shifts the day by the browser's offset: a birthdate that reads as the day before for anybody
	 * east of UTC.
	 */
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

	/* Text in, text out, with no `Date` in between. See the note above about the offset.
	 *
	 * A value that is not a date is treated as no date rather than as an error. What is in the
	 * column is whatever was put there, and a control is not the place to refuse it. */
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
	<!-- The wrapper is THIS file's element, exactly as `DateRange`'s is, so the rule that draws the
	     box is a rule that can reach it. The segments inside are the library's and are styled by the
	     shared `.segment` rule in the app's stylesheet: one copy, for the two controls that draw
	     one. -->
	<div class="date-field" role="group" aria-label={label}>
		<DateField.Input>
			{#snippet children({ segments })}
				<!--
					Keyed by POSITION and never by the segment's name. A date has more than one `literal`
					in it (the separators are segments too) so keying by name is a duplicate key, and a
					keyed-each failure unmounts everything above it, every screen that draws a date
					going blank immediately.
				-->
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
