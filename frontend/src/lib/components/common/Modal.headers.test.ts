/*
 * A long name in a sheet's heading, or in a card's, breaks inside its box on every surface.
 *
 * A sheet drawing a file's name (`3840x2560_0123abcd...jpg`, one token with no space in it) as its
 * sentence would run the name straight out through the sheet's right edge unless something breaks
 * it. A rule each sheet writes for itself is a rule the next sheet forgets, so the rule is in
 * `Modal`, which draws the heading and the sentence of every sheet, and these checks keep it there:
 *
 *   - `Modal` carries it, on the two elements it draws, and they are where the rule looks.
 *   - No sheet takes it back: nothing restyles a sheet's heading or sentence to stop it breaking.
 *   - Every dialog in the tree is a `Modal`, or is named below with the reason it is not, so a new
 *     hand-built one cannot quietly draw a heading of its own.
 *   - Every heading a card, a sheet or a drawer draws a value into (a name, a sentence built from
 *     one) says in its own stylesheet how a long token is handled: broken anywhere, or cut with an
 *     ellipsis.
 *
 * Static where it has to be: a document that lays nothing out cannot show overflow, so what is
 * read is the stylesheet that decides it, and what this holds is that it stays written.
 */

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount } from 'svelte';

import Modal from './Modal.svelte';
import modalSource from './Modal.svelte?raw';

const SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

/** A file's name with no space in it, as a sheet is handed one. Invented, in a camera's shape. */
const UNBROKEN = '3840\u00d72560_0123456789abcdef0123456789abcdef0123456789abcdef.jpg';

/** Every component source under `dir`, as a path from `src` and its text. Tests and probes are not
 *  screens. */
function svelteFiles(dir: string): { where: string; source: string }[] {
	const found: { where: string; source: string }[] = [];
	for (const entry of readdirSync(dir)) {
		if (entry === 'node_modules') continue;
		const path = join(dir, entry);
		if (statSync(path).isDirectory()) found.push(...svelteFiles(path));
		else if (entry.endsWith('.svelte') && !entry.includes('.test.') && !entry.includes('Probe')) {
			found.push({
				where: relative(SOURCE, path).split('\\').join('/'),
				source: readFileSync(path, 'utf8')
			});
		}
	}
	return found;
}

/** The gallery is a separate repository nested in the tree; its specimens are not screens. */
const SCREENS = svelteFiles(SOURCE).filter((file) => !file.where.startsWith('routes/design/'));

/** One component's own stylesheet, comments taken out the way CSS itself takes them out. */
function styleOf(source: string): string {
	const found = /<style[^>]*>([\s\S]*?)<\/style>/.exec(source);
	return (found?.[1] ?? '').replace(/\/\*[\s\S]*?\*\//g, '');
}

/** Every innermost rule of a stylesheet, as its selector list and its body. */
function rulesOf(css: string): { selectors: string[]; body: string }[] {
	return [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({
		selectors: match[1]
			.split(',')
			.map((one) => one.trim())
			.filter(Boolean),
		body: match[2]
	}));
}

/** Whether a selector names this class as a class, not as part of a longer name. */
function namesClass(selector: string, name: string): boolean {
	return new RegExp(`\\.${name}(?![\\w-])`).test(selector);
}

/** Whether a selector names this element type (`h2`, not `.h2` and not `h2x`). */
function namesTag(selector: string, tag: string): boolean {
	return new RegExp(`(^|[\\s>+~(])${tag}(?![\\w-])`).test(selector);
}

/** A declaration that breaks a long token or cuts it with an ellipsis. */
const HANDLED = /overflow-wrap:\s*anywhere|text-overflow:\s*ellipsis/;

/** A declaration that stops a heading breaking, or cuts it with nothing to say what was cut. */
const UNBREAKING =
	/white-space:\s*nowrap|line-clamp|overflow-wrap:\s*normal|word-break:\s*keep-all/;

afterEach(() => {
	document.body.innerHTML = '';
});

describe("the sheet's own heading and sentence", () => {
	it('break a long name anywhere, in the component that draws them', () => {
		const rules = rulesOf(styleOf(modalSource));
		for (const part of ['title', 'consequence']) {
			const dressing = rules.filter((rule) =>
				rule.selectors.some((one) => /^\.sheet\s*>\s*:global\(/.test(one) && namesClass(one, part))
			);
			expect(dressing.some((rule) => /overflow-wrap:\s*anywhere/.test(rule.body))).toBe(true);
		}
	});

	it('are the direct children of the sheet the rule is written against', () => {
		/* The rule reaches `.title` and `.consequence` only as the sheet's own children, so a heading
		   drawn a level deeper would be missed by it with nothing failing. */
		const host = document.createElement('div');
		document.body.append(host);
		mount(Modal, {
			target: host,
			props: {
				open: true,
				title: UNBROKEN,
				description: UNBROKEN,
				children: createRawSnippet(() => ({ render: () => '<p>rows</p>' }))
			} as never
		});
		flushSync();

		expect(document.querySelector('.sheet > .title')?.textContent).toBe(UNBROKEN);
		expect(document.querySelector('.sheet > .consequence')?.textContent?.trim()).toBe(UNBROKEN);
	});
});

describe('no sheet takes the rule back', () => {
	/** Every class a sheet hands `Modal` for one of its two elements, read off the markup. */
	function handedAs(prop: string): Set<string> {
		const pattern = new RegExp(`\\b${prop}="([\\w-]+)"`, 'g');
		return new Set(SCREENS.flatMap((file) => [...file.source.matchAll(pattern)].map((m) => m[1])));
	}

	/* A class on the sentence itself, and a class on the sheet that holds both. */
	const onSentence = new Set([...handedAs('descriptionClass'), ...handedAs('consequenceClass')]);
	const onSheet = handedAs('sheetClass');

	/** Whether a selector reaches a sheet's heading or its sentence. */
	function reachesHeader(selector: string): boolean {
		if (namesClass(selector, 'consequence')) return true;
		if ([...onSentence].some((name) => namesClass(selector, name))) return true;
		return [...onSheet].some((name) => namesClass(selector, name)) && namesClass(selector, 'title');
	}

	it('surveys the sheets rather than an empty tree', () => {
		expect(SCREENS.length).toBeGreaterThan(100);
		expect(onSheet.size).toBeGreaterThan(5);
		expect(onSentence.size).toBeGreaterThan(0);
	});

	it('leaves no rule on a heading or a sentence that stops a name breaking', () => {
		const refusing = SCREENS.flatMap((file) =>
			rulesOf(styleOf(file.source))
				.filter((rule) => UNBREAKING.test(rule.body))
				.flatMap((rule) => rule.selectors.filter(reachesHeader))
				.map((selector) => `${file.where}: ${selector}`)
		);

		expect(refusing).toEqual([]);
	});
});

describe('every dialog is a Modal', () => {
	/*
	 * The surfaces that announce themselves as a dialog without being a `Modal`, and why. None of
	 * them has a sheet's heading and sentence: each is a whole screen, or a bubble.
	 */
	const NOT_A_SHEET: Record<string, string> = {
		'lib/components/AssetModal.svelte':
			"the file view: a screen of its own, whose heading is the file's name row (one line, cut with an ellipsis, drawn by AssetView)",
		'lib/components/SettingsModal.svelte':
			'the settings screen: a page of panes, each headed by its own page title',
		'lib/components/shell/SearchOverlay.svelte':
			'the search box and its results: no heading at all',
		'lib/components/common/Popover.svelte': 'a bubble beside the control that opened it: no heading'
	};

	it('draws its heading through Modal, or is named here with why it has none', () => {
		const dialogs = SCREENS.filter(
			(file) =>
				file.where !== 'lib/components/common/Modal.svelte' &&
				/role="(?:alert)?dialog"|aria-modal=/.test(file.source)
		).map((file) => file.where);

		expect(dialogs.filter((where) => !(where in NOT_A_SHEET))).toEqual([]);
		// The other way round, so the list shrinks when one of them is folded into `Modal`.
		expect(Object.keys(NOT_A_SHEET).filter((where) => !dialogs.includes(where))).toEqual([]);
	});
});

describe('a heading a card draws a value into', () => {
	/*
	 * Page chrome (`shell/`) is the page's own title and is not a card; everything else under
	 * `lib/components` is a card, a sheet, a drawer or a row's heading.
	 */
	const CARDS = SCREENS.filter(
		(file) =>
			file.where.startsWith('lib/components/') && !file.where.startsWith('lib/components/shell/')
	);

	const headings = CARDS.flatMap((file) =>
		[...file.source.matchAll(/<(h[1-6])\b([^>]*)>([\s\S]*?)<\/\1>/g)]
			.filter((match) => match[3].includes('{'))
			.map((match) => ({
				where: file.where,
				tag: match[1],
				classes: [...(/\bclass="([^"]*)"/.exec(match[2])?.[1] ?? '').matchAll(/[\w-]+/g)].map(
					(one) => one[0]
				),
				css: styleOf(file.source)
			}))
	);

	it('surveys the headings rather than none', () => {
		expect(headings.length).toBeGreaterThan(5);
	});

	it('breaks a long name or cuts it with an ellipsis, in its own stylesheet', () => {
		const loose = headings
			.filter(
				(heading) =>
					!rulesOf(heading.css).some(
						(rule) =>
							HANDLED.test(rule.body) &&
							rule.selectors.some(
								(one) =>
									namesTag(one, heading.tag) ||
									heading.classes.some((name) => namesClass(one, name))
							)
					)
			)
			.map(
				(heading) =>
					`${heading.where}: <${heading.tag}${heading.classes.map((c) => '.' + c).join('')}>`
			);

		expect(loose).toEqual([]);
	});
});
