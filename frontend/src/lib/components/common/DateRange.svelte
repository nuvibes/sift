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
	/*
	 * Two dates, picked off a calendar.
	 *
	 * The query language takes a date range (`added:<from>..<to>` with ISO dates, and the spans
	 * that go with it), and without this the only way to enter one is to type it, which is a dead
	 * end for "everything from that week in August".
	 *
	 * bits-ui supplies what is invisible when done by hand: each part of the date is its own
	 * segment, so arrow keys step the month without touching the year and typing `13` in a month
	 * rolls into the next field; the grid is a real calendar with arrow keys, page keys and a live
	 * region announcing the month; and it knows about locales and time zones rather than assuming
	 * the machine's.
	 *
	 * It hands back strings, not date objects: a filter lives in the address, which is text. A
	 * `CalendarDate` would make every caller convert it back, and the first to use `toISOString()`
	 * would shift the day by the browser's offset for anybody east of UTC.
	 */
	import { DateRangePicker, Portal } from 'bits-ui';
	import { CalendarDate, type DateValue } from '@internationalized/date';

	import Icon from '$lib/components/Icon.svelte';
	import PageShield from './PageShield.svelte';

	interface Props {
		/**
		 * A control drawn beside the box, on the box's own line rather than the label's (the filter
		 * panel's Clear). Here rather than beside the whole field, because the label sits above the
		 * box and anything aligned to the field's edge lands level with the label's bottom, not the
		 * box's middle.
		 */
		beside?: Snippet;
		/** The span, as the address writes it. */
		value?: DaySpan;
		/** Given the new span. An end left open is simply absent. */
		onchange?: (value: DaySpan) => void;
		/** Names the control, for a picker with no visible label beside it. */
		label?: string;
	}

	let { value = {}, onchange, label = 'A range of days', beside }: Props = $props();

	/*
	 * Whether the calendar is showing, held here so that pressing the DATE opens it.
	 *
	 * The library opens it from the trigger button and from nothing else, which is correct for a
	 * field somebody is typing into, and wrong for this one, where the date is the thing being
	 * pressed and the small glyph beside it is a second target for the same intention. Pressing a
	 * segment still focuses that segment, so typing works exactly as it did; the calendar simply
	 * comes up with it.
	 */
	let open = $state(false);

	function openFromField(event: MouseEvent) {
		// Not the trigger: that one toggles, and answering its press by forcing `true` would make the
		// button open a calendar that is already open and never close it.
		if ((event.target as HTMLElement | null)?.closest('.open')) return;
		open = true;
	}

	/* `YYYY-MM-DD` in and out, with no `Date` anywhere in between. See the note above about the
	   browser's offset. A malformed value is treated as no value rather than throwing: what is in the
	   address is whatever somebody typed there, and a picker is not the place to refuse it. */
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

<!--
	`child` throughout, so this file's scoped styles reach the elements. Rendered by the library they
	would be a stranger's elements and every rule below would match nothing, with no warning.
-->
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
	<!-- The press is a shortcut to a control that is already here and already reachable: the trigger
	     beside it is a real button, in the tab order, and every segment takes the keyboard on its own.
	     This adds nothing a keyboard cannot already do. -->
	<!-- `data-unfinished` while the calendar is up: the calendar is portalled out of the panel, so
	     pointing at it reads to the panel as leaving, and the panel would fall shut under it. The panel's
	     hover close vetoes anything it holds that is unfinished, and an open calendar is that. -->
	<div class="line">
		<div class="date-field" data-unfinished={open ? '' : undefined} onclick={openFromField}>
			{#each ['start', 'end'] as const as part (part)}
				<DateRangePicker.Input type={part}>
					{#snippet children({ segments })}
						<!--
						Keyed by POSITION, not by the segment's name.

						A date field has more than one `literal` (the separators between day, month and year
						are segments too) so keying by name is a duplicate key, which Svelte throws on, and a
						keyed-each failure unmounts everything above it: every screen that draws a date
						picker would go blank together. The segments are positional and their order never
						changes, so the index is
						both correct and stable here.
					-->
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

	<!-- Portalled: inside the facet panel's scrolling column the calendar would be laid out in the
	     flow of the column and clipped by its overflow, far below the field and cut off.
	     The picker has no portal of its own in this version of the library; the generic one is the
	     same element every other floating surface in the app goes through. -->
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
	/* The box and whatever stands beside it, centred on one line. The box may wrap onto two lines
	   in a narrow column; what is beside it stays centred on the whole of it. */
	.line {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	/*
	 * `:global` on everything the library renders. A scoped rule aimed at a component's own element
	 * matches nothing at all, silently, which is the trap the `child` snippets above exist to
	 * avoid wherever the library offers one, and this is where it does not. Every rule inside the
	 * calendar starts at `.date-content`, a name only this file writes: `.day`, `.cell` and `.grid`
	 * are names other screens use for their own elements, and a bare global rule would dress those.
	 */
	:global(.date-label) {
		display: block;
		margin-block-end: var(--space-1);
		color: var(--sift-ink-2);
		font: var(--text-body-sm);
	}

	/* The box and the segments inside it are in the app's stylesheet, shared with `DateField`, which
	   draws one date out of the same library and the same parts. It wraps rather than overflowing:
	   this sits in one column of the filter panel, and `mm/dd/yyyy to mm/dd/yyyy` plus a button is
	   wider than that with both ends empty, so the second date drops to its own line instead of
	   running out over the column beside it.
	 */

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

	/* The hover layer (see `--layer-hover`) over no ground of its own, so it answers on the field
	   and inside a filled bar's translucent panel alike. */
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

	/* The floating calendar. Same surface and shadow as the select's menu and the tag list, because
	   three things that float over the page must not be three designs. */
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

	/*
	 * A day outside the month on show. Drawn rather than hidden, so the grid keeps its shape and the
	 * weeks stay in the same place as the months change.
	 *
	 * The QUIET ink, not the decoration one. These are dates a person reads and can click, so the
	 * fourth ink is not allowed here: it is under 3:1 on every surface above the canvas, which the
	 * contrast gate refuses.
	 */
	:global(.date-content .day[data-outside-month]) {
		color: var(--sift-ink-3);
	}

	/* Genuinely unavailable, so the decoration ink IS right here: an unavailable day is not something
	   a reader has to read. */
	:global(.date-content .day[data-disabled]) {
		color: var(--sift-ink-4);
		cursor: default;
	}

	/* The two ends of the span are solid; everything between them is the quiet tint. A range drawn
	   entirely in the accent is a block of colour with no way to see where it starts. */
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
