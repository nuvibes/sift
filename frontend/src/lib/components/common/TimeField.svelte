<script lang="ts" module>
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'TimeField',
		category: 'control',
		role: 'a time of day typed one segment at a time',
		basis: 'bits-ui:TimeField',
		states: ['empty', 'filled', 'disabled']
	} satisfies DesignEntry;
</script>

<script lang="ts">
	/*
	 * ONE time of day, typed in segments. `DateField`'s sibling, and deliberately its twin.
	 *
	 * ## Why this and not a text box
	 *
	 * Quiet hours (when recognition may use the machine) are two settings holding the strings
	 * `23:00` and `07:00`, and the settings screen draws a string as a text field. A text field
	 * accepts `11pm`, `2300`, `23.00` and `tomorrow`, none of which the server takes, with only a
	 * sentence of help underneath to say the format. A field whose rule is written beside it rather
	 * than built into it is a field somebody gets wrong.
	 *
	 * Segments cannot be got wrong in that way: the hour and the minute are separately focusable, an
	 * arrow key steps one without touching the other, typing 25 into the hour rolls rather than
	 * being accepted, and there is nothing to parse because nothing free-form was ever typed.
	 *
	 * ## Why not the browser's own time box
	 *
	 * `<input type="time">` draws the site's clock glyph and the site's popup, in the
	 * operating system's look, at a size and in a place the page has no say over: the same
	 * objection that had `NumberInput` and `DateField` written, and the same answer: keep the
	 * behaviour, draw the control.
	 *
	 * ## Why it hands back a string
	 *
	 * `HH:MM` on a twenty-four hour clock, which is what the setting holds and what the server
	 * validates. A caller given a time object would convert it back, and the conversions are where
	 * the padding gets lost: `9:5` is not a time the server accepts.
	 *
	 * What is DISPLAYED is the reader's own locale, which is not the same question: somebody who
	 * reads a clock as "11:00 PM" sees that and the setting still holds `23:00`. The library handles
	 * the two independently, which is precisely why the control is its rather than ours.
	 */
	import { onDestroy } from 'svelte';
	import { TimeField } from 'bits-ui';
	import { Time } from '@internationalized/date';
	import { clock } from '$lib/shell/clock.svelte';

	interface Props {
		/** The time, as the setting holds it: `HH:MM`, or empty for none. */
		value?: string;
		/** Given the new time as `HH:MM`, or the empty string when it has been cleared. Told once
		 *  the typing has settled (a pause, or focus leaving the field), never once a keystroke. */
		onchange?: (value: string) => void;
		id?: string;
		/** Names the control where nothing else does. Inside a `Field` or a row the label already has. */
		label?: string;
		/** aria-describedby, for the help text a row draws beside it. */
		describedBy?: string;
		disabled?: boolean;
	}

	let { value = '', onchange, id, label, describedBy, disabled = false }: Props = $props();

	/* Text in, text out, with no `Date` in between.
	 *
	 * A value that is not a time is treated as no time rather than as an error: the same reading
	 * `DateField` makes, and for the same reason: what is stored is whatever was put there, by a
	 * version of Sift this control knows nothing about, and a control is not the place to refuse it.
	 * The row above it shows an empty field, which is honest, and saving writes a real time. */
	function toValue(clock: string | undefined): Time | undefined {
		const parts = clock?.match(/^(\d{1,2}):(\d{2})$/);
		if (!parts) return undefined;
		const hour = Number(parts[1]);
		const minute = Number(parts[2]);
		if (hour > 23 || minute > 59) return undefined;
		return new Time(hour, minute);
	}

	function toText(clock: { hour: number; minute: number } | undefined | null): string {
		if (!clock) return '';
		return `${String(clock.hour).padStart(2, '0')}:${String(clock.minute).padStart(2, '0')}`;
	}

	const picked = $derived(toValue(value));

	/* ## A time is told when it has been typed, not while it is being typed
	 *
	 * A time takes several keystrokes (an hour of two digits, the minutes, the half of the day), and
	 * every one of them is a valid time on its way to the one meant: `1` on the way to `11`, `11 AM`
	 * on the way to `11 PM`. Told each as it happened, a setting would be saved five or six times
	 * for one edit, each half-typed time would be in force for a moment, and the answer to an
	 * earlier save could arrive on top of a later keystroke and put the field back: an evening
	 * typed quickly would end as a morning.
	 *
	 * So what is being typed is held here. It is told once the keys have been still for a moment or
	 * focus leaves the field, whichever is first, and while it is held the field shows it whatever
	 * arrives from outside. */
	const SETTLE_MS = 800;
	let typing = $state(false);
	let draft = $state<Time | undefined>(undefined);
	let settling: ReturnType<typeof setTimeout> | undefined;
	const shown = $derived(typing ? draft : picked);

	function typed(next: Time | undefined): void {
		typing = true;
		draft = next;
		clearTimeout(settling);
		settling = setTimeout(tell, SETTLE_MS);
	}

	/** Say what was typed, once, and only if it is not the time already held. */
	function tell(): void {
		clearTimeout(settling);
		settling = undefined;
		if (!typing) return;
		const text = toText(draft);
		if (text !== toText(picked)) onchange?.(text);
	}

	/** Focus leaving the whole field (not moving between its parts) ends the typing. */
	function left(event: FocusEvent): void {
		const within = event.currentTarget as HTMLElement;
		if (event.relatedTarget instanceof Node && within.contains(event.relatedTarget)) return;
		tell();
		typing = false;
	}

	// A field taken off the screen with a time still held (the dialog closed mid-edit) tells it.
	onDestroy(tell);
</script>

<TimeField.Root
	value={shown}
	{disabled}
	onValueChange={(next) => typed(next ?? undefined)}
	granularity="minute"
	hourCycle={clock.hours === '24' ? 24 : 12}
>
	<!-- The wrapper is THIS file's element, exactly as `DateField`'s is, so the rule that draws the
	     box is a rule that can reach it. The segments inside are the library's and are styled by the
	     shared `.segment` rule in the app's stylesheet: one copy, for the three controls that draw
	     a row of them. -->
	<div class="date-field" role="group" aria-label={label} onfocusout={left}>
		<TimeField.Input>
			{#snippet children({ segments })}
				<!--
					Keyed by POSITION and never by the segment's name, for the reason written out in
					`DateField`: the separators are segments too, a time has more than one `literal` in
					it, and keying by name is a duplicate key that unmounts everything above it.

					The hour is what the id and the description land on: it is the first thing focus
					reaches, so it is the segment a label points at.
				-->
				{#each segments as { part, value: text }, at (at)}
					<TimeField.Segment
						{part}
						class="segment"
						id={part === 'hour' ? id : undefined}
						aria-describedby={part === 'hour' ? describedBy : undefined}
						>{part === 'hour' && clock.hours !== '24'
							? text.replace(/^0(?=\d)/, '')
							: text}</TimeField.Segment
					>
				{/each}
			{/snippet}
		</TimeField.Input>
	</div>
</TimeField.Root>
