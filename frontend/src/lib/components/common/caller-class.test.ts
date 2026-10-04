/*
 * A caller's class is ADDED to a shared component's own, never substituted for them.
 *
 * ## What goes wrong
 *
 * A spread is applied in the order it is written. A `class` arriving inside `{...rest}` written
 * after a literal `class` attribute replaces that attribute outright, so `class="btn secondary
 * medium"` becomes `class="row"`, and the control keeps the caller's layout and loses every rule
 * that makes it a button, the reset included. What is left is the site's own grey slab with
 * somebody's positioning on it.
 *
 * In a shared component it draws every call site bare at once (in `Pressable`: the facet panel's
 * value rows, the organize board's cards, the folder rows and tiles, the face tiles, the settings
 * nav), and only a browser measuring pixels can see it.
 *
 * ## Why both halves
 *
 * The rendered half proves the three components that take the prop today really merge it. The
 * static half is the one that matters for the NEXT component: it refuses a spread written after a
 * literal class attribute unless `class` has been taken out of what is spread. That is the exact
 * shape of both faults, and no other check in this folder can see it: the markup is valid, the
 * types are satisfied, the stylesheet is untouched and every rule in it still matches something.
 */

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
	// `unmount`, not `host.remove()`: removing the host hides the markup and leaves every listener
	// the component put on the window still answering.
	if (mounted) unmount(mounted);
	host?.remove();
	mounted = undefined;
	host = undefined;
});

/** A fresh host for one component, and the element it put in it.
 *
 * Written per component rather than through one helper taking a component: a helper wide enough to
 * take all three has to type its props as a bare record, and Svelte's own component type refuses
 * that, which is a type error in a TEST file, where nothing else would ever have noticed. */
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
		/* The other direction. A component that substituted would still carry the caller's class:
		   that is the half that keeps working and the reason the fault is invisible at the call site,
		   so an assertion naming only the caller's class passes against the bug. What has to be
		   true is that the component's OWN class survived. Drawn with no caller class at all, so the
		   two states can be told apart. */
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

/**
 * Every opening tag whose attributes hold a spread written AFTER a literal class attribute.
 *
 * The order is the whole of it. `{...props} class="veil"` is safe and is what the library's own
 * snippets produce everywhere in this app: the literal is written last and wins. The reverse is
 * the fault.
 */
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
		/*
		 * The positive control, and not decoration: this scan is a regular expression over markup,
		 * and it fails by finding nothing and passing for ever. These write a spread after their
		 * class on purpose and take `class` out of it, exactly the shape looked for; if the scan
		 * stops seeing them it has stopped seeing anything. `NarrowBox` takes the caller's class
		 * out of `...rest` and writes its own after it.
		 */
		const doing = files.filter((file) => spreadAfterClass(file.source).length > 0);

		/*
		 * `TextInput` and `TextArea` likewise take the caller's class out of the spread and write
		 * their own after it.
		 */
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
