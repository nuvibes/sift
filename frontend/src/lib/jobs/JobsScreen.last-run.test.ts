/*
 * A housekeeping row's last run is the Done column's fact. Its cell spans Progress and Done, so the
 * words are set against the cell's end: they finish under Done's heading, as the counts above them
 * do, rather than starting under Progress. Read from the compiled rules.
 */
import { compile } from 'svelte/compiler';
import { expect, it } from 'vitest';

import source from './JobsScreen.svelte?raw';

it("ends a chore's last run under the Done heading", () => {
	const css = compile(source, { filename: 'JobsScreen.svelte', css: 'external' })
		.css!.code.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/\.svelte-[\w-]+/g, '');
	const rule = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].find(
		(one) => one[1].trim() === '.pass-last'
	);
	expect(rule?.[2]).toContain('display: block');
	expect(rule?.[2]).toContain('text-align: end');
});

/* A failed run's phrase sends the reader to the failed ones in the list. Scrolled before that page
   has drawn, the list is still a skeleton and the rows arrive below the fold. */
it('brings the failed list into view only once it has landed and drawn', () => {
	const at = source.indexOf('function showFailed()');
	const body = source.slice(at, source.indexOf('\n\t}', at));
	const landed = body.indexOf("await queue.setFilter('failed')");
	const drawn = body.indexOf('await tick()');
	const scrolled = body.indexOf('scrollIntoView');
	expect(landed).toBeGreaterThan(-1);
	expect(drawn).toBeGreaterThan(landed);
	expect(scrolled).toBeGreaterThan(drawn);
});
