/* A caller's class is added to a shared component's own, never substituted: a `class` in a spread
 * after a literal one replaces it. Rendered for today's components, a static scan for the next. */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createRawSnippet, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';

import Button from './Button.svelte';
import Chip from './Chip.svelte';
import Pressable from './Pressable.svelte';

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

const words = createRawSnippet(() => ({ render: () => '<span>Words</span>' }));

let host: HTMLElement | undefined;
let mounted: Record<string, unknown> | undefined;

afterEach(() => {
	// `unmount`, so the component's window listeners go too.
	if (mounted) unmount(mounted);
	host?.remove();
	mounted = undefined;
	host = undefined;
});

/** A fresh host per component, since a shared helper would need loose prop types. */
function into(): HTMLElement {
	host = document.createElement('div');
	document.body.append(host);
	return host;
}

function drawn(): HTMLElement {
	return host!.firstElementChild as HTMLElement;
}

describe('a class from the caller is added, not substituted', () => {
	it('Button keeps its own look and takes the position it was given', () => {
		mounted = mount(Button, {
			target: into(),
			props: { children: words, class: 'in-the-corner' }
		}) as Record<string, unknown>;

		expect([...drawn().classList]).toEqual(expect.arrayContaining(['btn', 'in-the-corner']));
	});

	it('Pressable keeps the reset that stops it being a site button', () => {
		mounted = mount(Pressable, {
			target: into(),
			props: { children: words, class: 'in-the-corner' }
		}) as Record<string, unknown>;

		expect([...drawn().classList]).toEqual(expect.arrayContaining(['pressable', 'in-the-corner']));
	});

	it('Chip keeps its shape and its tone', () => {
		mounted = mount(Chip, {
			target: into(),
			props: { children: words, class: 'in-the-corner' }
		}) as Record<string, unknown>;

		expect([...drawn().classList]).toEqual(expect.arrayContaining(['chip', 'in-the-corner']));
	});

	it('is asking a question the fault could not answer', () => {
		/*
		 * The component's own class must survive; drawn with no caller class to tell the states
		 * apart.
		 */
		mounted = mount(Button, { target: into(), props: { children: words } }) as Record<
			string,
			unknown
		>;

		expect(drawn().classList.contains('btn')).toBe(true);
		expect(drawn().classList.contains('in-the-corner')).toBe(false);
	});
});

/** Every `.svelte` file under `src`. */
function everyComponent(dir: string): string[] {
	const found: string[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules' || entry.startsWith('.')) continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...everyComponent(path));
		else if (entry.endsWith('.svelte')) found.push(path);
	}
	return found;
}

/** Every opening tag with a spread after a literal class; the reverse order is safe. */
function spreadAfterClass(source: string): string[] {
	const found: string[] = [];
	for (const tag of source.matchAll(/<([a-zA-Z][\w.-]*)((?:[^<>"']|"[^"]*"|'[^']*')*?)\/?>/g)) {
		const [, name, attributes] = tag;
		if (name === 'script' || name === 'style') continue;
		const at = attributes.search(/\sclass="/);
		if (at === -1) continue;
		if (/\{\s*\.\.\.\s*[A-Za-z_$][\w$]*\s*\}/.test(attributes.slice(at))) found.push(name);
	}
	return found;
}

/** `let { class: extra = '', ...rest } = $props()`: the one way `class` leaves the rest object. */
function takesClassOutOfTheRest(source: string): boolean {
	return /\bclass:\s*[A-Za-z_$][\w$]*\s*=/.test(source);
}

describe('nothing spreads over a class it has already written', () => {
	const files = everyComponent(SOURCE).map((path) => ({
		where: relative(SOURCE, path).split('\\').join('/'),
		source: readFileSync(path, 'utf8')
	}));

	it('is reading the interface rather than an empty tree', () => {
		expect(files.length).toBeGreaterThan(150);
	});

	it('finds the five components that do it, so the scan is not matching nothing', () => {
		/* The positive control: these take `class` out of the spread, so the scan must see them. */
		const doing = files.filter((file) => spreadAfterClass(file.source).length > 0);

		/* `TextInput` and `TextArea` do the same. */
		expect(doing.map((file) => file.where).sort()).toEqual([
			'lib/components/common/Button.svelte',
			/* `Chip` handles its class the same way. */
			'lib/components/common/Chip.svelte',
			'lib/components/common/NarrowBox.svelte',
			'lib/components/common/Pressable.svelte',
			'lib/components/common/TextArea.svelte',
			'lib/components/common/TextInput.svelte'
		]);
	});

	it.each([
		'lib/components/common/Button.svelte',
		'lib/components/common/NarrowBox.svelte',
		'lib/components/common/Pressable.svelte'
	])('%s has taken class out of what it spreads', (where) => {
		const file = files.find((one) => one.where === where);
		expect(takesClassOutOfTheRest(file?.source ?? '')).toBe(true);
	});

	it('and any other component that starts doing it has too', () => {
		const bare = files
			.filter((file) => spreadAfterClass(file.source).length > 0)
			.filter((file) => !takesClassOutOfTheRest(file.source))
			.map((file) => file.where);

		expect(
			bare,
			'a spread written after a literal class attribute replaces that attribute outright.\n' +
				"Destructure it out (`let { class: extra = '', ...rest } = $props()`) and write it\n" +
				'into the literal, or move the spread in front of the class.'
		).toEqual([]);
	});
});
