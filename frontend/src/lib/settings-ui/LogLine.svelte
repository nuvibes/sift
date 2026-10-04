<script lang="ts" module>
	/*
	 * One line of a log, as `Settings > Tasks and Activity > Logs` draws it: the time, the level, which
	 * log it is from where there are two, and the line.
	 *
	 * ## A LINE IS READ WHOLE
	 *
	 * The line is the record's name in bold and then its fields, wrapping as a sentence wraps, so a
	 * long record is a taller row rather than a row cut off at the column's end. The answer is
	 * usually in the last field (the route, the path, the statement), and an ellipsis there would hide it
	 * from exactly the person who opened the log to find it. A value too long for the column (a path,
	 * a statement) breaks at any character rather than running past the edge.
	 *
	 * Nothing has to be pressed to read a line: a row that opened on a press would still show the
	 * cut line first, and somebody scanning for one route would have to open every row to find it.
	 *
	 * Its own component so the design gallery can draw the same line the Log draws, from lines
	 * written for the purpose, without a log to read.
	 */
	import type { components } from '$lib/api/schema';

	type LogLine = components['schemas']['LogLine'];

	/** One raw record as an object, or null where the line is not one. */
	export function record(raw: string): Record<string, unknown> | null {
		try {
			const held: unknown = JSON.parse(raw);
			if (!held || typeof held !== 'object' || Array.isArray(held)) return null;
			return held as Record<string, unknown>;
		} catch {
			return null;
		}
	}

	/** The fields a record carries beyond its time, its level and its name, as `[key, value]`. */
	export function fields(raw: string): [string, string][] {
		const one = record(raw);
		if (!one) return [];
		return Object.entries(one)
			.filter(([key]) => key !== 'timestamp' && key !== 'level' && key !== 'event')
			.map(([key, value]) => [key, typeof value === 'string' ? value : JSON.stringify(value)]);
	}

	/** The same fields as one string, `key=value` apart by two spaces: what the narrowing reads. */
	export function rest(raw: string): string {
		if (!record(raw)) return raw;
		return fields(raw)
			.map(([key, value]) => `${key}=${value}`)
			.join('  ');
	}

	export type { LogLine };
</script>

<script lang="ts">
	interface Props {
		line: LogLine;
		/** The time as the column shows it: the clock time under its day's heading. */
		at: string;
		from?: string;
	}

	let { line, at, from }: Props = $props();

	const said = $derived(line.event ? fields(line.raw) : []);
</script>

<p
	class="line"
	class:marked={from !== undefined}
	class:bad={line.level === 'error' || line.level === 'critical'}
	class:warn={line.level === 'warning'}
>
	<span class="when">{at}</span>
	<span class="level">{line.level ?? ''}</span>
	{#if from !== undefined}<span class="from">{from}</span>{/if}
	<span class="said"
		><span class="what" class:raw={!line.event}>{line.event ?? line.raw}</span
		>{#each said as [key, value], index (index)}{' '}<span class="key">{key}=</span><span
				class="value">{value}</span
			>{/each}</span
	>
</p>

<style>
	/* Three columns, and the third wraps. The time and the level stay one line each at the top of
	   their row, so a taller row still reads left to right from its time. The time column is sized
	   in the line's own figures to the widest time a line carries ("12:58:54.332 AM", thirteen
	   digit widths in tabular figures), with a digit to spare. */
	.line {
		--log-time: 14ch;
		scroll-snap-align: start;
		display: grid;
		grid-template-columns: var(--log-time) 4.5rem minmax(0, 1fr);
		/* The whole width of the list. A settings pane gives its prose a reading measure
		   (`SettingsPane.svelte`), which is right for a sentence and wrong for a row: under it a line
		   would stop at two thirds of the box with the rest of the box empty. */
		max-inline-size: none;
		align-items: start;
		gap: var(--space-2);
		margin: 0;
		padding: var(--space-1) var(--space-3);
		font: var(--text-data);
		color: var(--sift-ink-2);
	}

	.line.marked {
		grid-template-columns: var(--log-time) 4.5rem 5.5rem minmax(0, 1fr);
	}

	.when,
	.level,
	.from {
		color: var(--sift-ink-3);
		white-space: nowrap;
	}

	/* Every digit one width, so the column measured above holds any time. */
	.when {
		font-variant-numeric: tabular-nums;
	}

	/* The line itself: wraps at the spaces between fields first, and inside a value only where one
	   value alone is wider than the column. Never cut, never past the edge. */
	.said {
		min-inline-size: 0;
		overflow-wrap: anywhere;
	}

	.what {
		font-weight: 600;
		color: var(--sift-ink);
	}

	/* A line that is not a record (a traceback, a line another writer left) has no name to stand
	   out: it reads as it was written. */
	.what.raw {
		font-weight: inherit;
	}

	/* The key quieter than its value, so the eye lands on the values when it scans a field list. */
	.key {
		color: var(--sift-ink-3);
	}

	.value {
		color: var(--sift-ink-2);
	}

	.warn .level,
	.warn .what {
		color: var(--sift-warn);
	}

	/* The TEXT red, not the fill. The fill is a background colour and does not clear the contrast
	   floor for words on its own tint, which on a log is every word somebody is reading. */
	.bad .level,
	.bad .what {
		color: var(--sift-bad-text);
	}

	/* A phone: the time and the level head the row and the line takes the whole width under them,
	   since a third of a phone's width would wrap a request into a column of single words. */
	@media (max-width: 767px) {
		.line {
			grid-template-columns: var(--log-time) minmax(0, 1fr);
			row-gap: 0;
		}

		.line.marked {
			grid-template-columns: var(--log-time) 4.5rem minmax(0, 1fr);
		}

		.said {
			grid-column: 1 / -1;
		}
	}
</style>
