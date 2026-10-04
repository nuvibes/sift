/*
 * How the Now tab is laid out: its lists on the pane's edges, the state of every pass a pill like
 * the rows' own, and the pile strip set off from the list above with the pile's actions at its end.
 *
 * Read from the source, the way the header's test reads it: what is asserted is what the screen
 * declares, whatever today's queue happens to draw.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const source = readFileSync('src/lib/jobs/JobsScreen.svelte', 'utf8');
const markup = source.slice(source.lastIndexOf('</script>'), source.indexOf('<style>'));
const style = source.slice(source.indexOf('<style>'));

describe('the Now tab', () => {
	it('stands both lists on the pane edges, where the tabs and the strip start', () => {
		const lists = [...markup.matchAll(/<DataRows\b[\s\S]*?[^=]>\s*$/gm)].map((one) => one[0]);
		const declared = lists.filter((tag) => /columns=\{[^}]*ACTIVITY_COLUMNS[^}]*\}/.test(tag));
		expect(declared).toHaveLength(2);
		for (const tag of declared) expect(tag).toMatch(/\sedges[\s>]/);
	});

	it('says a pass state as a pill, the shape Done and Failed wear', () => {
		const said = markup.slice(markup.indexOf('{#snippet nowWord()}'));
		// The pill inside the app's tooltip, which says the whole of words the column cuts short.
		expect(said.slice(0, 120)).toMatch(
			/^\{#snippet nowWord\(\)\}<Tooltip label=\{one\.now\} stretch\s*><Badge/
		);
		expect(style).not.toMatch(/\.pass-now/);
	});

	it('sets the task list off from the groups above by the space between two groups', () => {
		/* The list's heading and Type row stand first now, so they take the group's space and the
		   strip under them is set off from them by the space inside one. */
		const head = style.slice(
			style.indexOf('.list-head {'),
			style.indexOf('}', style.indexOf('.list-head {'))
		);
		expect(head).toMatch(/margin-block-start: var\(--space-6\)/);
		const filters = style.slice(
			style.indexOf('.filters {'),
			style.indexOf('}', style.indexOf('.filters {'))
		);
		expect(filters).toMatch(/margin-block: var\(--space-4\)/);
		expect(markup.indexOf('<div class="list-head">')).toBeLessThan(
			markup.indexOf('<div class="filters">')
		);
	});

	it('puts the pile actions at the end of the strip, not alone above the list', () => {
		const strip = markup.slice(
			markup.indexOf('<div class="filters">'),
			markup.indexOf('<div id={listId}')
		);
		expect(strip.indexOf('<Tabs')).toBeGreaterThan(-1);
		expect(strip.indexOf('<MenuButton')).toBeGreaterThan(strip.indexOf('<Tabs'));
		expect(markup).not.toMatch(/<header>/);
	});

	it('says an empty task list as a block: it is one list among the others on the tab', () => {
		const empty = markup.slice(markup.indexOf('COPY.list.noneOfType') - 200);
		expect(empty.slice(0, 260)).toMatch(/<Empty scope="block">/);
		expect(markup).toContain("'No tasks are running or waiting.'");
	});
});
