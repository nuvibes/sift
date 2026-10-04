/* Several lists of keys on one page share one key column: every list is handed every key on the
 * page, laid unseen in its first key cell, and each list reads key and sentence on one baseline. */
import { afterEach, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { compile } from 'svelte/compiler';
import KeyRows from './KeyRows.svelte';
import source from './KeyRows.svelte?raw';
import { shortcutsByArea } from '$lib/shell/shortcuts';

let drawn: Record<string, unknown> | null = null;
afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	document.body.innerHTML = '';
});

it('lays every key on the page, unseen, in the first key cell', () => {
	const [first, second] = shortcutsByArea();
	const widest = [...first.shortcuts, ...second.shortcuts].map((one) => one.shown);
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(KeyRows, { target: host, props: { shortcuts: first.shortcuts, widest } }) as Record<
		string,
		unknown
	>;
	flushSync();
	const sizers = host.querySelectorAll('.sizer');
	expect(sizers).toHaveLength(1);
	expect(sizers[0].getAttribute('aria-hidden')).toBe('true');
	expect([...sizers[0].children].map((one) => one.textContent)).toEqual(widest);
	expect(host.querySelector('dt')?.contains(sizers[0])).toBe(true);
});

it('puts the key before the sizer, so the first row keeps its baseline', () => {
	const [first] = shortcutsByArea();
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(KeyRows, {
		target: host,
		props: { shortcuts: first.shortcuts, widest: [first.shortcuts[0].shown] }
	}) as Record<string, unknown>;
	flushSync();
	// A block ahead of the key would set the cell's first baseline and push the key a line down.
	const cell = host.querySelector('dt');
	const kinds = [...(cell?.childNodes ?? [])]
		.filter((node) => node.nodeType === Node.ELEMENT_NODE || node.textContent?.trim())
		.map((node) => (node.nodeType === Node.TEXT_NODE ? 'key' : (node as Element).className));
	expect(kinds[0]).toBe('key');
	expect(kinds.at(-1)).toMatch(/\bsizer\b/);
	expect(cell?.textContent?.startsWith(first.shortcuts[0].shown)).toBe(true);
});

it('reads a key and its sentence on one baseline', () => {
	const css = compile(source, { filename: 'KeyRows.svelte', css: 'external' }).css?.code ?? '';
	expect(css).toMatch(/dl(?:\.svelte-[a-z0-9]+)? \{[^}]*align-items: baseline/);
});
