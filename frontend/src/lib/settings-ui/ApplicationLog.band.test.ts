/*
 * The Log's pinned day never stands over a line, and its filter stands in the controls column.
 *
 * Read from the compiled stylesheet: the unit environment lays nothing out and knows no scroll
 * snapping, so what is held is the arrangement a real window depends on. A line's time and level
 * never wrap (the line itself does, and is held whole in `LogLine.svelte.test.ts`), each line snaps
 * its top to the edge of the band, and the band is a height of its own that the snapping leaves
 * room for.
 */
import { describe, expect, it } from 'vitest';
import { compile } from 'svelte/compiler';

import source from './ApplicationLog.svelte?raw';
import lineSource from './LogLine.svelte?raw';

/** A component's compiled stylesheet, its scoping hashes taken out. */
function styles(code: string, filename: string): string {
	return (compile(code, { filename, css: 'external' }).css?.code ?? '')
		.replace(/\.svelte-[a-z0-9]+/g, '')
		.replace(/\s+/g, ' ');
}

/* The list's own rules, and the rules of the line it draws (`LogLine`), read as one sheet. */
const css = `${styles(source, 'ApplicationLog.svelte')} ${styles(lineSource, 'LogLine.svelte')}`;

/** The declarations of the first rule whose selector is exactly `selector`. */
function rule(selector: string): string {
	const at = css.indexOf(`${selector} {`);
	expect(at, `no ${selector} rule`).toBeGreaterThan(-1);
	return css.slice(at, css.indexOf('}', at));
}

describe('the pinned day', () => {
	it('is a band of its own height, drawn over what scrolls under it', () => {
		const day = rule('.day');
		expect(day).toMatch(/position: sticky/);
		expect(day).toMatch(/min-block-size: var\(--day-band\)/);
		expect(rule('.lines')).toMatch(/--day-band: \d/);
	});

	it('leaves every line whole under it: the list snaps each line to the edge of the band', () => {
		expect(rule('.lines [data-scroll-area-viewport]')).toMatch(
			/scroll-padding-block-start: var\(--day-band\)/
		);
		expect(rule('.line')).toMatch(/scroll-snap-align: start/);
	});

	it('keeps the time, the level and the log s mark to one line each at the head of a row', () => {
		expect(rule('.when, .level, .from')).toMatch(/white-space: nowrap/);
	});

	it('sizes the time column in the line s own figures to the widest time', () => {
		/* "12:58:54.332 AM" measures 103px in the data face, where one figure is 8px: 13 figures.
		   Fourteen leaves one to spare, and a length in `ch` follows the face if it changes. */
		const line = rule('.line');
		expect(line).toMatch(/--log-time: 14ch/);
		expect(line).toMatch(/grid-template-columns: var\(--log-time\) /);
		expect(rule('.when')).toMatch(/font-variant-numeric: tabular-nums/);
	});
});

describe('the Log settings', () => {
	it('are the rows of the group the heading opens, so nothing is drawn over its sentence', async () => {
		const logs = (await import('./Logs.svelte?raw')).default;
		const group = logs.slice(logs.indexOf('<SettingGroup id="activity.log"'));
		expect(group.indexOf('<SettingsList')).toBeGreaterThan(-1);
		expect(group.indexOf('<SettingsList')).toBeLessThan(group.indexOf('</SettingGroup>'));
	});
});

describe('the filter', () => {
	it('stands in the row s control column, beside the level', () => {
		const row = source.slice(source.indexOf('<LabelledRow label={COPY.level}'));
		const closed = row.indexOf('</LabelledRow>');
		expect(row.indexOf('<NarrowBox')).toBeGreaterThan(-1);
		expect(row.indexOf('<NarrowBox')).toBeLessThan(closed);
	});
});
