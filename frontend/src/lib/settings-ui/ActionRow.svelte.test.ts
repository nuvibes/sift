/*
 * A row's button wears its act's glyph from the verb it is handed, so a verb that arrives from a
 * copy table (which the markup gate cannot read) still wears the same glyph as everywhere else.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, expect, it } from 'vitest';

import generated from '$lib/generated/icon-codepoints.json';
import ActionRow from './ActionRow.svelte';
import source from './ActionRow.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';

/** The name of the glyph a button draws, read back from the character the icon font is given. */
const names = new Map(
	Object.entries(generated as Record<string, string>).map(([name, hex]) => [
		String.fromCodePoint(parseInt(hex, 16)),
		name
	])
);

let host: HTMLElement | undefined;
let instance: Record<string, unknown> | undefined;

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	host?.remove();
});

function glyphOf(props: Record<string, unknown>): string | null {
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(ActionRow, {
		target: host,
		props: { label: 'A row', onclick: () => {}, ...props } as never
	}) as Record<string, unknown>;
	flushSync();
	const icon = host.querySelector('button .icon');
	return icon === null ? null : (names.get(icon.textContent ?? '') ?? icon.textContent);
}

it('draws Delete with the bin and Edit with the pencil when the caller names no glyph', () => {
	expect(glyphOf({ action: 'Delete face data', destructive: true })).toBe('delete');
	unmount(instance as Record<string, unknown>);
	instance = undefined;
	expect(glyphOf({ action: 'Edit' })).toBe('edit');
});

it('replaces a glyph the act does not allow, and keeps one it does', () => {
	expect(glyphOf({ action: 'Create', icon: 'search' })).toBe('add');
	unmount(instance as Record<string, unknown>);
	instance = undefined;
	expect(glyphOf({ action: 'Add a person', icon: 'person_add' })).toBe('person_add');
});

it('leaves a verb that is not an act as the caller drew it', () => {
	expect(glyphOf({ action: 'Open' })).toBeNull();
	unmount(instance as Record<string, unknown>);
	instance = undefined;
	expect(glyphOf({ action: 'Count now', icon: 'search' })).toBe('search');
});

it('keeps a long figure and its press on one line, the column growing to hold both', () => {
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(ActionRow, {
		target: host,
		props: {
			label: 'Leftover thumbnails and previews',
			action: 'Delete',
			destructive: true,
			note: '459 files, 344 MB to free',
			onclick: () => {}
		} as never
	}) as Record<string, unknown>;
	flushSync();
	const row = host.querySelector<HTMLElement>('.row')!;
	applyStyles(source, row);
	try {
		expect(getComputedStyle(row.querySelector('.control')!).flexWrap).toBe('nowrap');
		expect(getComputedStyle(row).gridTemplateColumns).toMatch(
			/minmax\(var\(--settings-control-col[^)]*\), max-content\)/
		);
		expect(getComputedStyle(row.querySelector('.press')!).flexShrink).toBe('0');
	} finally {
		removeStyles();
	}
});

it('turns its arc while busy, on a plain press and on a split one', () => {
	glyphOf({ action: 'Save a backup', icon: 'save', busy: true });
	expect(host!.querySelector('button .spinner')).not.toBeNull();
	unmount(instance as Record<string, unknown>);
	instance = undefined;
	glyphOf({
		action: 'Generate now',
		busy: true,
		trailingIcon: 'schedule',
		trailingLabel: 'Tonight',
		ontrailing: () => {}
	});
	expect(host!.querySelector('.half.lead button .spinner')).not.toBeNull();
});
