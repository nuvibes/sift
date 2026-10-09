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
	/* One time of day typed in segments, DateField's twin: hour and minute step separately and
	   nothing free-form is parsed. Shown in the reader's locale, handed back as `HH:MM`. */
	import { onDestroy } from 'svelte';
	import { TimeField } from 'bits-ui';
	import { Time } from '@internationalized/date';
	import { clock } from '$lib/shell/clock.svelte';

	interface Props {
		/** The time, as the setting holds it: `HH:MM`, or empty for none. */
		value?: string;
		/** Given the new `HH:MM` (or empty) once the typing settles, never per keystroke. */
		onchange?: (value: string) => void;
		id?: string;
		/** Names the control where nothing else does. Inside a `Field` or a row the label already has. */
		label?: string;
		/** aria-describedby, for the help text a row draws beside it. */
		describedBy?: string;
		disabled?: boolean;
	}

	let { value = '', onchange, id, label, describedBy, disabled = false }: Props = $props();

	/* Text in, text out; a value that is not a time is no time, not an error. */
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

	/* Held while typed and told once the keys rest or focus leaves: each keystroke is a valid
	   time on the way to the one meant, and saving each could end an evening as a morning. */
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
	<!-- This file's wrapper draws the box; the segments use the shared `.segment` rule. -->
	<div class="date-field" role="group" aria-label={label} onfocusout={left}>
		<TimeField.Input>
			{#snippet children({ segments })}
				<!--
				Keyed by position (see DateField); the hour takes the id, being where focus lands.
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
