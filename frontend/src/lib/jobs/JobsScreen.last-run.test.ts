/* A housekeeping row's last run is the Done column's fact. */
import { compile } from 'svelte/compiler';
import { expect, it } from 'vitest';

import source from './JobsScreen.svelte?raw';
import summary from './ActivitySummary.svelte?raw';

it("ends a chore's last run under the Done heading", () => {
	const css = compile(summary, { filename: 'ActivitySummary.svelte', css: 'external' })
		.css!.code.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/\.svelte-[\w-]+/g, '');
	const rule = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].find(
		(one) => one[1].trim() === '.pass-last'
	);
	expect(rule?.[2]).toContain('display: block');
	expect(rule?.[2]).toContain('text-align: end');
});

/* A failed run's phrase sends the reader to the failed ones in the list. */
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
