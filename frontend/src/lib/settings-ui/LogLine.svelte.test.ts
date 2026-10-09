/* A log line is read whole: cut at its column's end with an ellipsis, nothing past
 * `route=/downloads/glanc...` could be read. */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { compile } from 'svelte/compiler';

import LogLine, { fields, rest } from './LogLine.svelte';
import lineSource from './LogLine.svelte?raw';
import listSource from './ApplicationLog.svelte?raw';

/* A long request, as the server writes one: the tail is the part a cut line hid. */
const PATH = '/downloads/glance/01J8ZQ4W3N7V5K2M6P8R9T0X1Y/pieces/that-go-on-past-the-column';
const RAW = JSON.stringify({
	timestamp: '2026-09-30T19:21:04.117Z',
	level: 'info',
	event: 'http.request',
	method: 'GET',
	route: PATH,
	status: 200,
	statement: 'SELECT a.id FROM assets a WHERE a.folder_id = ? ORDER BY a.added_at DESC LIMIT 48'
});
const LINE = { raw: RAW, at: '2026-09-30T19:21:04.117Z', level: 'info', event: 'http.request' };

let shown: ReturnType<typeof mount> | null = null;
let host: HTMLElement;

afterEach(() => {
	if (shown) unmount(shown);
	shown = null;
	host?.remove();
});

function draw(line = LINE) {
	host = document.createElement('div');
	document.body.append(host);
	shown = mount(LogLine, { target: host, props: { line, at: '7:21:04.117 PM' } });
	flushSync();
	return host.querySelector('.line') as HTMLElement;
}

const css = (compile(lineSource, { filename: 'LogLine.svelte', css: 'external' }).css?.code ?? '')
	.replace(/\.svelte-[a-z0-9]+/g, '')
	.replace(/\s+/g, ' ');

/** Every rule whose selector names `name`, joined. */
function rulesFor(name: string): string {
	return [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)]
		.filter(([, selector]) => selector.split(/[\s,>]+/).includes(name))
		.map(([, , body]) => body)
		.join(' ');
}

describe('a long line', () => {
	it('is in the document whole: the name, then every field with its full value', () => {
		const line = draw();
		const said = line.querySelector('.said')?.textContent ?? '';
		expect(said).toBe(
			`http.request method=GET route=${PATH} status=200 statement=${JSON.parse(RAW).statement}`
		);
		expect(line.querySelector('.what')?.textContent).toBe('http.request');
		expect([...line.querySelectorAll('.key')].map((one) => one.textContent)).toEqual([
			'method=',
			'route=',
			'status=',
			'statement='
		]);
	});

	it('keeps three columns: the time, the level, the line', () => {
		const line = draw();
		expect([...line.children].map((one) => one.className.split(' ')[0])).toEqual([
			'when',
			'level',
			'said'
		]);
		expect(rulesFor('.line')).toMatch(
			/grid-template-columns: var\(--log-time\) 4\.5rem minmax\(0, 1fr\)/
		);
	});

	it('is never cut off: no ellipsis, no single line, no hidden overflow on the line', () => {
		for (const name of ['.said', '.what', '.key', '.value']) {
			const rules = rulesFor(name);
			expect(rules, name).not.toMatch(/text-overflow/);
			expect(rules, name).not.toMatch(/white-space: nowrap/);
			expect(rules, name).not.toMatch(/overflow: hidden/);
		}
		expect(listSource).not.toMatch(/text-overflow/);
	});

	it("takes the list's whole width, never a pane's reading measure", () => {
		expect(rulesFor('.line')).toMatch(/max-inline-size: none/);
	});

	it('breaks a value too long for its column at any character, rather than past the edge', () => {
		expect(rulesFor('.said')).toMatch(/overflow-wrap: anywhere/);
		expect(rulesFor('.said')).toMatch(/min-inline-size: 0/);
	});
});

describe('a line that is not a record', () => {
	it('shows its raw text whole, with no fields', () => {
		const odd = 'Traceback (most recent call last): a line the writer did not shape';
		const line = draw({
			raw: odd,
			at: null as unknown as string,
			level: null,
			event: null
		} as never);
		expect(line.querySelector('.what')?.textContent).toBe(odd);
		expect(line.querySelectorAll('.key')).toHaveLength(0);
		expect(line.querySelector('.what')?.classList.contains('raw')).toBe(true);
		expect(fields(odd)).toEqual([]);
		expect(rest(odd)).toBe(odd);
	});
});

describe('what the narrowing reads', () => {
	it('is every field as key=value, two spaces apart, a non-string value as JSON', () => {
		expect(rest(RAW)).toBe(
			`method=GET  route=${PATH}  status=200  statement=${JSON.parse(RAW).statement}`
		);
	});
});
