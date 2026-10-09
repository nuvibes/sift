<script lang="ts" module>
	import type { Snippet } from 'svelte';
	import type { DesignEntry } from '$lib/design/entry';

	export const design = {
		name: 'DateRange',
		category: 'control',
		role: 'a span between two dates, picked from one calendar',
		basis: 'bits-ui:DateRangePicker',
		states: ['empty', 'filled']
	} satisfies DesignEntry;

	/** A span of days, as the address writes them: `YYYY-MM-DD`, or nothing for an open end. */
	export interface DaySpan {
		from?: string;
		to?: string;
	}
</script>

<script lang="ts">
	/* Two dates picked off a calendar, for the query language's date ranges: bits-ui's segments and
	 * grid, handed back as `YYYY-MM-DD` strings so no `toISOString()` shifts the day. */
	import { DateRangePicker, Portal } from 'bits-ui';
	import { CalendarDate, type DateValue } from '@internationalized/date';

	import Icon from '$lib/components/Icon.svelte';
	import PageShield from './PageShield.svelte';

	interface Props {
		/** A control beside the box, on the box's line rather than the label's. */
		beside?: Snippet;
		/** The span, as the address writes it. */
		value?: DaySpan;
		/** Given the new span. An end left open is simply absent. */
		onchange?: (value: DaySpan) => void;
		/** Names the control, for a picker with no visible label beside it. */
		label?: string;
	}

	let { value = {}, onchange, label = 'A range of days', beside }: Props = $props();

	/* Held here, so pressing the date opens the calendar too. */
	let open = $state(false);

	function openFromField(event: MouseEvent) {
		// Not the trigger, which toggles.
		if ((event.target as HTMLElement | null)?.closest('.open')) return;
		open = true;
	}

	/* Text in and out; a malformed value is no value. */
	function toValue(day: string | undefined): DateValue | undefined {
		const parts = day?.match(/^(\d{4})-(\d{2})-(\d{2})$/);
		if (!parts) return undefined;
		return new CalendarDate(Number(parts[1]), Number(parts[2]), Number(parts[3]));
	}

	function toText(day: DateValue | undefined): string | undefined {
		if (!day) return undefined;
		return `${String(day.year).padStart(4, '0')}-${String(day.month).padStart(2, '0')}-${String(day.day).padStart(2, '0')}`;
	}

	const picked = $derived({ start: toValue(value.from), end: toValue(value.to) });
</script>

<!-- `child` throughout, so scoped styles reach the elements. -->
<DateRangePicker.Root
	weekdayFormat="short"
	bind:open
	value={picked}
	onValueChange={(next) =>
		onchange?.({ from: toText(next?.start ?? undefined), to: toText(next?.end ?? undefined) })}
>
	<DateRangePicker.Label class="date-label">{label}</DateRangePicker.Label>

	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<!-- A shortcut to controls already reachable by keyboard. `data-unfinished` while the calendar
	is up, so the panel's hover close does not shut under it. -->
	<div class="line">
		<div class="date-field" data-unfinished={open ? '' : undefined} onclick={openFromField}>
			{#each ['start', 'end'] as const as part (part)}
				<DateRangePicker.Input type={part}>
					{#snippet children({ segments })}
						<!-- Keyed by position: a date has several `literal` segments. -->

						{#each segments as { part: segment, value: text }, at (at)}
							<DateRangePicker.Segment part={segment} class="segment"
								>{text}</DateRangePicker.Segment
							>
						{/each}
					{/snippet}
				</DateRangePicker.Input>
				{#if part === 'start'}<span class="to" aria-hidden="true">to</span>{/if}
			{/each}

			<DateRangePicker.Trigger aria-label="Open the calendar">
				{#snippet child({ props })}
					<button {...props} class="open" type="button">
						<Icon name="calendar_month" size={16} />
					</button>
				{/snippet}
			</DateRangePicker.Trigger>
		</div>
		{@render beside?.()}
	</div>

	<!-- Portalled, or the facet column would clip it. -->
	<Portal>
		<PageShield up={open} />
		<DateRangePicker.Content sideOffset={6} class="date-content">
			<DateRangePicker.Calendar>
				{#snippet children({ months, weekdays })}
					<div class="head">
						<DateRangePicker.PrevButton aria-label="The month before">
							{#snippet child({ props })}
								<button {...props} class="step" type="button">
									<Icon name="chevron_left" size={16} />
								</button>
							{/snippet}
						</DateRangePicker.PrevButton>
						<DateRangePicker.Heading class="month" />
						<DateRangePicker.NextButton aria-label="The month after">
							{#snippet child({ props })}
								<button {...props} class="step" type="button">
									<Icon name="chevron_right" size={16} />
								</button>
							{/snippet}
						</DateRangePicker.NextButton>
					</div>

					{#each months as month (month.value)}
						<DateRangePicker.Grid class="grid">
							<DateRangePicker.GridHead>
								<DateRangePicker.GridRow>
									{#each weekdays as day (day)}
										<DateRangePicker.HeadCell class="weekday"
											>{day.slice(0, 2)}</DateRangePicker.HeadCell
										>
									{/each}
								</DateRangePicker.GridRow>
							</DateRangePicker.GridHead>
							<DateRangePicker.GridBody>
								{#each month.weeks as week (week[0])}
									<DateRangePicker.GridRow>
										{#each week as date (date)}
											<DateRangePicker.Cell {date} month={month.value} class="cell">
												<DateRangePicker.Day class="day" />
											</DateRangePicker.Cell>
										{/each}
									</DateRangePicker.GridRow>
								{/each}
							</DateRangePicker.GridBody>
						</DateRangePicker.Grid>
					{/each}
				{/snippet}
			</DateRangePicker.Calendar>
		</DateRangePicker.Content>
	</Portal>
</DateRangePicker.Root>

<style>
	/* The box and what stands beside it, centred on one line. */
	.line {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/* Global on everything the library renders, each rule starting at `.date-content`. */
	:global(.date-label) {
		display: block;
		margin-block-end: var(--space-1);
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	/* The box is app.css's (shared with DateField); it wraps rather than overflowing the column. */

	.to {
		margin-inline: var(--space-1);
		color: var(--sift-ink-3);
	}

	.open,
	.step {
		display: inline-grid;
		place-items: center;
		inline-size: var(--control-height-sm);
		block-size: var(--control-height-sm);
		padding: 0;
		border: 0;
		border-radius: var(--radius-sm);
		background: none;
		color: var(--sift-ink-2);
		cursor: pointer;
	}

	.open,
	.step {
		/* The Light register: the ground steps, over --dur-instant, and nothing moves. */
		transition:
			background var(--dur-instant) var(--ease),
			color var(--dur-instant) var(--ease);
	}

	/* The hover layer over no ground. */
	.open:hover,
	.step:hover {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
		color: var(--hover-ink);
	}

	.open:focus-visible,
	.step:focus-visible {
		outline: none;
		box-shadow: var(--focus-ring);
	}

	/* The floating surface every popup wears. */
	:global(.date-content) {
		z-index: var(--z-menu);
		padding: var(--space-3);
		border: 1px solid var(--sift-line);
		border-radius: var(--radius-md);
		background: var(--sift-surface-3);
		box-shadow: var(--elev-2);
	}

	.head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-2);
		margin-block-end: var(--space-2);
	}

	:global(.date-content .month) {
		font: var(--text-label);
		color: var(--sift-ink);
	}

	:global(.date-content .grid) {
		border-collapse: collapse;
	}

	:global(.date-content .weekday) {
		padding-block-end: var(--space-1);
		color: var(--sift-ink-3);
		font: var(--text-micro);
		font-weight: 400;
	}

	:global(.date-content .cell) {
		padding: 0;
	}

	:global(.date-content .day) {
		display: grid;
		place-items: center;
		inline-size: 32px;
		block-size: 32px;
		border-radius: var(--radius-sm);
		color: var(--sift-ink);
		font: var(--text-body-sm);
		cursor: pointer;
	}

	:global(.date-content .day) {
		transition: background var(--dur-instant) var(--ease);
	}

	/* The layer, not a surface step: the calendar is already on the step a day would go to. */
	:global(.date-content .day:hover) {
		background-color: color-mix(in srgb, currentColor var(--layer-hover), transparent);
	}

	/* Days outside the month drawn, in the quiet ink (the fourth fails 3:1). */
	:global(.date-content .day[data-outside-month]) {
		color: var(--sift-ink-3);
	}

	/* Unavailable days take the decoration ink. */
	:global(.date-content .day[data-disabled]) {
		color: var(--sift-ink-4);
		cursor: default;
	}

	/* Solid ends, a quiet tint between. */
	:global(.date-content .day[data-selected]) {
		background: var(--sift-accent-bg);
	}

	:global(.date-content .day[data-selection-start]),
	:global(.date-content .day[data-selection-end]) {
		background: var(--sift-accent);
		color: var(--primary-foreground);
	}

	:global(.date-content .day[data-today]:not([data-selected])) {
		box-shadow: inset 0 0 0 1px var(--sift-line-strong);
	}

	:global(.date-content .day:focus-visible) {
		outline: none;
		box-shadow: var(--focus-ring);
	}
</style>
