/*
 * The group heading: its outline, its anchor, the control beside it, and, the part that is easy to
 * get wrong without anything saying so, which groups get the hairline.
 *
 * The hairline is decided by the component's own stylesheet, and the unit environment applies no
 * stylesheet. So the rule is read out of the COMPILED component rather than restated here: the
 * selector the browser will run is taken from what Svelte produced for this file, its scoping hash
 * is dropped, and the headings mounted below are asked whether it matches them. A test that wrote
 * its own copy of the selector would pass while the component's drifted.
 */
import { compile } from 'svelte/compiler';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it } from 'vitest';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import SectionHeading from './SectionHeading.svelte';
import source from './SectionHeading.svelte?raw';

const words = (text: string) => createRawSnippet(() => ({ render: () => `<span>${text}</span>` }));

let mounted: ReturnType<typeof mount>[] = [];

afterEach(() => {
	for (const one of mounted) unmount(one);
	mounted = [];
	removeStyles();
	document.body.innerHTML = '';
});

function draw(target: Element, props: Record<string, unknown> = {}, text = 'Group') {
	const one = mount(SectionHeading, { target, props: { children: words(text), ...props } });
	mounted.push(one);
	flushSync();
	return target.lastElementChild as HTMLElement;
}

/** The selector that SHOWS the rule, exactly as compiled, less its scoping class. */
function ruleShownBy(): string {
	const css = compile(source, { filename: 'SectionHeading.svelte', css: 'external' }).css?.code;
	if (!css) throw new Error('the component compiled to no stylesheet');
	const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
	const shown = [...withoutComments.matchAll(/([^{}]+)\{([^}]*)\}/g)].find((rule) =>
		/display:\s*block/.test(rule[2])
	);
	if (!shown) throw new Error('no rule in the stylesheet shows the hairline');
	return shown[1].replace(/\.svelte-[\w-]+/g, '').trim();
}

/** The selector that takes the rule off again under a parent heading, as compiled. */
function ruleHiddenBy(): string {
	const css = compile(source, { filename: 'SectionHeading.svelte', css: 'external' }).css?.code;
	if (!css) throw new Error('the component compiled to no stylesheet');
	const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, '');
	const hidden = [...withoutComments.matchAll(/([^{}]+)\{([^}]*)\}/g)].find(
		(rule) => /^\s*display:\s*none;?\s*$/.test(rule[2]) && rule[1].includes('+')
	);
	if (!hidden) throw new Error('no rule in the stylesheet keeps a parent heading clear');
	return hidden[1].replace(/\.svelte-[\w-]+/g, '').trim();
}

describe('the heading', () => {
	it('is a level-2 heading carrying the anchor a search result lands on', () => {
		const block = draw(document.body, { id: 'backup.now' }, 'Back up now');

		const heading = block.querySelector('h2');
		expect(heading?.id).toBe('backup.now');
		expect(heading?.textContent).toBe('Back up now');
		expect(block.querySelector('h3')).toBeNull();
	});

	it('is a level-3 heading when it heads a block inside a group', () => {
		const block = draw(document.body, { level: 3 });

		expect(block.querySelector('h3')?.textContent).toBe('Group');
		expect(block.querySelector('h2')).toBeNull();
	});

	it('puts a control on its own line, after the words', () => {
		const block = draw(document.body, {
			actions: createRawSnippet(() => ({ render: () => '<button type="button">Copy</button>' }))
		});

		const line = block.querySelector('.heading-line');
		expect(line?.firstElementChild?.tagName).toBe('H2');
		expect(line?.querySelector('.actions button')?.textContent).toBe('Copy');
	});
});

describe('a heading that is a way onwards', () => {
	it('makes its words a link that reads as the heading at rest', () => {
		const block = draw(document.body, { band: true, href: '/browse?like=one' }, 'Similar');

		const link = block.querySelector('h3 a.heading-link') as HTMLAnchorElement;
		expect(link.getAttribute('href')).toBe('/browse?like=one');
		expect(link.textContent).toBe('Similar');
		/* A browser underlines a link and paints it blue before any rule of ours; the unit
		   environment does neither, so that default is put in first for the rule to beat. */
		const browser = document.createElement('style');
		browser.textContent = 'a { text-decoration: underline; color: rgb(0, 0, 238); }';
		document.head.prepend(browser);
		applyStyles(source, link);
		const drawn = getComputedStyle(link);
		expect(drawn.textDecoration).toMatch(/^none/);
		expect(drawn.color).toBe(getComputedStyle(link.parentElement as Element).color);
		browser.remove();
	});

	it('runs the press before the link is followed, and draws no link without an address', () => {
		let pressed = 0;
		const block = draw(document.body, {
			href: '/browse',
			onclick: (event: MouseEvent) => {
				event.preventDefault();
				pressed += 1;
			}
		});
		(block.querySelector('a') as HTMLElement).click();
		expect(pressed).toBe(1);

		expect(draw(document.body, {}, 'Plain').querySelector('a')).toBeNull();
	});
});

describe('the hairline between groups', () => {
	it('is drawn over every group after the first on a page, and never over the first', () => {
		const shown = ruleShownBy();
		const page = document.createElement('div');
		page.className = 'section-stack';
		document.body.append(page);

		/* The shapes a pane really has: a group whose heading is inside a wrapper, a later group
		   nested one level deeper, and a heading standing directly beside a previous one. */
		const first = document.createElement('section');
		const deeper = document.createElement('div');
		const later = document.createElement('section');
		deeper.append(later);
		page.append(first, deeper);

		const one = draw(first, {}, 'First');
		const two = draw(later, {}, 'Second');
		const three = draw(page, {}, 'Third');

		const rule = (block: HTMLElement) => block.querySelector('.section-rule');
		expect(rule(one)?.matches(shown)).toBe(false);
		expect(rule(two)?.matches(shown)).toBe(true);
		expect(rule(three)?.matches(shown)).toBe(true);
	});

	it('counts a first group of rows with no heading, and never the lede under the title', () => {
		const shown = ruleShownBy();
		const page = document.createElement('div');
		page.className = 'section-stack';
		document.body.append(page);

		/* A feature's switch and its rows straight under the title, then a headed group: the
		   heading opens the SECOND group and wears the line. A paragraph alone is the page's lede. */
		const lede = document.createElement('p');
		lede.className = 'lede';
		const pane = document.createElement('div');
		const switchRows = document.createElement('div');
		const row = document.createElement('div');
		row.className = 'row ruled-row';
		switchRows.append(row);
		const later = document.createElement('section');
		pane.append(switchRows, later);
		page.append(lede, pane);

		const second = draw(later, {}, 'Confirmed faces');
		expect(second.querySelector('.section-rule')?.matches(shown)).toBe(true);

		const other = document.createElement('div');
		other.className = 'section-stack';
		const intro = document.createElement('p');
		intro.className = 'lede';
		other.append(intro);
		document.body.append(other);
		const first = draw(other, {}, 'Save a backup now');
		expect(first.querySelector('.section-rule')?.matches(shown)).toBe(false);
	});

	it('counts only groups on the SAME page, so a screen underneath cannot put a rule over the first', () => {
		const shown = ruleShownBy();
		const underneath = document.createElement('div');
		underneath.className = 'section-stack';
		const page = document.createElement('div');
		page.className = 'section-stack';
		document.body.append(underneath, page);

		draw(underneath, {}, 'On another screen');
		const first = draw(page, {}, 'First of this page');

		expect(first.querySelector('.section-rule')?.matches(shown)).toBe(false);
	});

	it('is not drawn between a heading and its own first subheading', () => {
		const shown = ruleShownBy();
		const hidden = ruleHiddenBy();
		const drawn = (block: HTMLElement) => {
			const rule = block.querySelector('.section-rule');
			return Boolean(rule?.matches(shown)) && !rule?.matches(hidden);
		};
		const page = document.createElement('div');
		page.className = 'section-stack';
		document.body.append(page);

		/* A parent heading with its groups in blocks after it: the first group's heading sits
		   right under the parent and takes no line; the second group's heading does. */
		const section = document.createElement('section');
		page.append(section);
		draw(section, {}, 'Parent');
		const firstGroup = document.createElement('div');
		const secondGroup = document.createElement('div');
		section.append(firstGroup, secondGroup);
		const first = draw(firstGroup, { level: 3 }, 'First group');
		const row = document.createElement('div');
		row.className = 'row ruled-row';
		firstGroup.append(row);
		const second = draw(secondGroup, { level: 3 }, 'Second group');

		expect(drawn(first)).toBe(false);
		expect(drawn(second)).toBe(true);

		// A heading standing straight after another, with nothing between, is the same pairing.
		const other = document.createElement('div');
		other.className = 'section-stack';
		document.body.append(other);
		draw(other, {}, 'Parent');
		const child = draw(other, { level: 3 }, 'Child');
		expect(drawn(child)).toBe(false);
	});

	it('is not drawn at all outside a page of groups', () => {
		const shown = ruleShownBy();
		draw(document.body, {}, 'One');
		const second = draw(document.body, {}, 'Two');

		expect(second.querySelector('.section-rule')?.matches(shown)).toBe(false);
	});
});

describe('a subheading, the heading over a block inside a group', () => {
	it('is drawn one step down the scale from the group heading, in the same face and weight', () => {
		/* Read from the compiled rules: a shorthand holding a variable, then a longhand after it, is
		   resolved by a browser and left blank by the unit environment. */
		const css = compile(source, { filename: 'SectionHeading.svelte', css: 'external' })
			.css!.code.replace(/\/\*[\s\S]*?\*\//g, '')
			.replace(/\.svelte-[\w-]+/g, '');
		const rules = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)].map((rule) => ({
			selector: rule[1].trim(),
			body: rule[2]
		}));
		const own = (selector: string) =>
			rules
				.filter((rule) => rule.selector.split(',').some((one) => one.trim() === selector))
				.map((rule) => rule.body)
				.join(';');

		expect(own('h2')).toContain('font: var(--text-h2)');
		const sub = rules
			.filter((rule) => rule.selector === 'h3')
			.map((rule) => rule.body)
			.join(';');
		/* The rung's own token, the body's size in the display face at the heading's weight
		   (`app.css`); `heading-rungs.test.ts` holds a form card's title to the same one. */
		expect(sub).toContain('font: var(--text-h3)');
	});
});

describe('the band, the heading inside a page', () => {
	it('is a level-3 heading with no hairline of its own', () => {
		const block = draw(document.body, { band: true }, 'Who is in this');

		expect(block.querySelector('h3')?.textContent).toBe('Who is in this');
		expect(block.querySelector('.section-rule')).toBeNull();
	});

	it('wears one face and one ink, quieter than a group heading', () => {
		const band = draw(document.body, { band: true }, 'Band');
		const group = draw(document.body, {}, 'Group');
		applyStyles(source, band);

		const banded = getComputedStyle(band.querySelector('h3') as Element);
		const grouped = getComputedStyle(group.querySelector('h2') as Element);
		expect(banded.getPropertyValue('font-weight')).toBe('600');
		expect(banded.getPropertyValue('color')).toContain('--sift-ink-2');
		expect(grouped.getPropertyValue('color')).toContain('--sift-ink)');
	});

	it('does not count as a heading before the next group, so it never puts a rule over the first', () => {
		const shown = ruleShownBy();
		const page = document.createElement('div');
		page.className = 'section-stack';
		document.body.append(page);

		draw(page, { band: true }, 'A band');
		const first = draw(page, {}, 'The first group');

		expect(first.querySelector('.section-rule')?.matches(shown)).toBe(false);
	});
});
