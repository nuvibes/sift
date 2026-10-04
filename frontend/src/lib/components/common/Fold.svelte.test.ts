/* A folded part of a pane says what is behind it, counted, in the one fold shape. */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import Fold from './Fold.svelte';

let drawn: Record<string, unknown> | null = null;
afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	document.body.innerHTML = '';
});

it('is the browser disclosure, shut, with the words it was given', () => {
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Fold, {
		target: host,
		props: {
			summary: 'Show all 26 Sites',
			children: createRawSnippet(() => ({ render: () => '<p>inside</p>' }))
		}
	}) as Record<string, unknown>;
	flushSync();
	const fold = host.querySelector('details.fold') as HTMLDetailsElement;
	expect(fold.open).toBe(false);
	expect(fold.querySelector('summary')?.textContent).toBe('Show all 26 Sites');
	expect(fold.textContent).toContain('inside');
});

/* The section shape: a band heading with the arrow beside its words, open until shut. */
function section(props: Record<string, unknown> = {}): HTMLElement {
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Fold, {
		target: host,
		props: {
			section: true,
			summary: 'Who is in this',
			children: createRawSnippet(() => ({ render: () => '<p class="held">inside</p>' })),
			...props
		}
	}) as Record<string, unknown>;
	flushSync();
	return host;
}

const KEY = 'sift.test.fold';
afterEach(() => localStorage.removeItem(KEY));

function arrow(host: HTMLElement): HTMLButtonElement {
	return host.querySelector('.section-heading button[aria-expanded]') as HTMLButtonElement;
}

it('heads a section, open, with the words and the arrow one press naming what it opens', () => {
	const host = section();
	expect(host.querySelector('details')).toBeNull();
	expect(host.querySelector('.section-heading h3')?.textContent).toContain('Who is in this');
	// ONE press: the words are inside the button, as the Collapse control under the picture is,
	// so pointing at the words or the arrow lights both and a press on either folds.
	expect(host.querySelector('.section-heading h3 button[aria-expanded]')).toBe(arrow(host));
	expect(arrow(host).textContent).toContain('Who is in this');
	expect(arrow(host).getAttribute('aria-expanded')).toBe('true');
	expect(host.querySelector(`#${arrow(host).getAttribute('aria-controls')}`)?.textContent).toBe(
		'inside'
	);
});

it('shuts on a press, keeps the press in this browser, and says so', async () => {
	const told: boolean[] = [];
	const host = section({ remember: KEY, ontoggle: (open: boolean) => told.push(open) });
	arrow(host).click();
	flushSync();
	await vi.waitFor(() => expect(host.querySelector('.held')).toBeNull());
	expect(arrow(host).getAttribute('aria-expanded')).toBe('false');
	expect(localStorage.getItem(KEY)).toBe('no');
	expect(told).toEqual([false]);
});

it('opens shut where this browser left it shut, and remembers nothing without a key', () => {
	localStorage.setItem(KEY, 'no');
	const host = section({ remember: KEY });
	expect(host.querySelector('.held')).toBeNull();
	unmount(drawn as Record<string, unknown>);
	drawn = null;
	localStorage.removeItem(KEY);
	const plain = section();
	arrow(plain).click();
	flushSync();
	expect(localStorage.getItem(KEY)).toBeNull();
});

it("draws the heading's own controls only while the section is open, and keeps a link", async () => {
	const host = section({
		href: '/browse?like=a-1',
		actions: createRawSnippet(() => ({ render: () => '<button class="edit">Edit</button>' }))
	});
	expect(host.querySelector('.section-heading a')?.getAttribute('href')).toBe('/browse?like=a-1');
	// A heading that leads onwards keeps its arrow a separate press, named by the section.
	expect(arrow(host).getAttribute('aria-label')).toBe('Who is in this');
	expect(host.querySelector('.section-heading a button')).toBeNull();
	expect(host.querySelector('.edit')).not.toBeNull();
	arrow(host).click();
	flushSync();
	await vi.waitFor(() => expect(host.querySelector('.edit')).toBeNull());
	expect(host.querySelector('.section-heading a')).not.toBeNull();
});

it('starts shut when its caller says so and nothing is remembered', () => {
	// The gallery draws the shut state beside the open one; a real screen leaves it to the browser.
	const host = section({ open: false });
	expect(arrow(host).getAttribute('aria-expanded')).toBe('false');
	expect(host.querySelector('.inside')).toBeNull();
	arrow(host).click();
	flushSync();
	expect(arrow(host).getAttribute('aria-expanded')).toBe('true');
});

it('keeps the words shape a disclosure the press turns and remembers', () => {
	const host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Fold, {
		target: host,
		props: {
			summary: 'Show all 26 Sites',
			remember: KEY,
			children: createRawSnippet(() => ({ render: () => '<p>inside</p>' }))
		}
	}) as Record<string, unknown>;
	flushSync();
	const fold = host.querySelector('details.fold') as HTMLDetailsElement;
	(fold.querySelector('summary') as HTMLElement).click();
	flushSync();
	expect(fold.open).toBe(true);
	expect(localStorage.getItem(KEY)).toBe('yes');
});

it('centres a section heading across its row, with its controls at the end, as Collapse stands', async () => {
	// Layout is not measured here (the browser specs do that): what is held is the one
	// rule that places it. Two equal outer columns around the heading put its middle on the row's.
	const source = (await import('./Fold.svelte?raw')).default as string;
	const rules = source.slice(source.indexOf('<style>'));
	expect(rules).toMatch(
		/\.section :global\(\.heading-line\) \{[^}]*grid-template-columns: 1fr auto auto 1fr;/
	);
	expect(rules).toMatch(/\.section :global\(\.heading-line > h3\) \{[^}]*grid-column: 2;/);
	expect(rules).toMatch(
		/\.section :global\(\.heading-line > \.actions\) \{[^}]*grid-column: 4;[^}]*justify-self: end;/
	);
	// A heading leading onwards keeps its arrow beside the words, centred with them as one.
	expect(rules).toMatch(
		/\.section\.onwards :global\(\.heading-line > \.actions\) \{[^}]*grid-column: 3;/
	);
	expect(
		section({ href: '/browse?like=a-1' }).querySelector('.fold.section.onwards')
	).not.toBeNull();
	unmount(drawn!);
	drawn = null;
	document.body.innerHTML = '';
	expect(section().querySelector('.fold.section.onwards')).toBeNull();
});

/*
 * THE HEADING'S WEIGHT: 600 everywhere, 450 only where a caller says so, and only the popout
 * player's sections under the picture say so. Read off the heading with both
 * stylesheets in the document, since the 600 is the band heading's and the 450 is this file's.
 */
it('draws a section heading at 600, and at 450 only where the caller asks', async () => {
	const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
	const own = (await import('./Fold.svelte?raw')).default as string;
	const heading = (await import('./SectionHeading.svelte?raw')).default as string;
	const weightOf = (host: HTMLElement): string => {
		applyStyles(heading, host.querySelector('.section-heading'));
		applyStyles(own, host.querySelector('.fold'));
		return getComputedStyle(host.querySelector('.heading-line > h3') as HTMLElement).fontWeight;
	};
	try {
		expect(weightOf(section())).toBe('600');
		unmount(drawn!);
		drawn = null;
		document.body.innerHTML = '';
		removeStyles();
		expect(weightOf(section({ weight: 450 }))).toBe('450');
		unmount(drawn!);
		drawn = null;
		document.body.innerHTML = '';
		removeStyles();
		expect(weightOf(section({ weight: 450, href: '/browse?like=a-1' }))).toBe('450');
	} finally {
		removeStyles();
	}
});

/* Every section under the picture, whatever their number: Same music is one of them, and must not
   stand at 600 beside four at 450. */
it('is at 450 on every popout section under the picture and on no other fold', () => {
	const sources = import.meta.glob(['/src/lib/**/*.svelte'], {
		query: '?raw',
		import: 'default',
		eager: true
	});
	const lighter: string[] = [];
	for (const [file, text] of Object.entries(sources)) {
		for (const fold of (text as string).match(/<Fold\b[^>]*>/g) ?? []) {
			if (!/weight=\{450\}/.test(fold)) continue;
			lighter.push(`${file.split('/').pop()}: ${fold.match(/summary="([^"]+)"/)?.[1]}`);
		}
	}
	expect(lighter.sort()).toEqual([
		'FacesInThis.svelte: Who is in this',
		'FileBandRows.svelte: Enrichment',
		'FileRecordPanel.svelte: File info',
		'LooksLikeThis.svelte: Similar to this',
		'SameMusic.svelte: Same music'
	]);
});

/*
 * THE 450 IS THE EXPAND CONTROL'S TEXT, and stays it. That control is a small ghost `Button`: set
 * in `--text-body-sm` in the ghost ink. The band heading already wears the same token and ink, so
 * the weight is the one difference, and it is written out in `Fold` because the band's 600
 * overrides the token's own. Held here to the token, so a change to the control's text moves the
 * heading's with it or fails.
 */
it("sets the popout's headings in exactly the Expand control's text", async () => {
	const own = (await import('./Fold.svelte?raw')).default as string;
	const button = (await import('./Button.svelte?raw')).default as string;
	const heading = (await import('./SectionHeading.svelte?raw')).default as string;
	// Read off the disk: a stylesheet imported by the test runner arrives processed, not as written.
	const tokens = readFileSync(resolve('src/app.css'), 'utf8');

	const weight = own.match(
		/\.section\.beside-control :global\(\.heading-line > h3\) \{[^}]*font-weight: (\d+);/
	)?.[1];
	const token = tokens.match(/--text-body-sm: (\d+) /)?.[1];
	expect(weight).toBe('450');
	expect(weight).toBe(token);
	// The control's face and size, and its ink.
	expect(button).toMatch(/\.small \{[^}]*font: var\(--text-body-sm\);/);
	expect(button).toMatch(/\.ghost \{[^}]*color: var\(--sift-ink-2\);/);
	// The band heading's, the same pair.
	expect(heading).toMatch(
		/\.band h3 \{[^}]*font: var\(--text-body-sm\);[^}]*color: var\(--sift-ink-2\);/
	);
});
