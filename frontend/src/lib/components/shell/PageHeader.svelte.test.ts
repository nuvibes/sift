/* The row at the top of every screen.
 *
 * One component rather than one per screen, since a header written where it is needed cannot see
 * the others. What is asserted here is the part that matters about that: one heading
 * per screen, at one level, with the page's own controls in one place, so a screen cannot quietly
 * grow a second idea of what a title is.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount, type ComponentProps } from 'svelte';
import PageHeader from './PageHeader.svelte';
import headerSource from './PageHeader.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';

let host: HTMLElement;

afterEach(() => host?.remove());

/** A snippet of plain markup, for standing in as a page's controls. */
function markup(html: string) {
	return createRawSnippet(() => ({ render: () => html }));
}

type Props = ComponentProps<typeof PageHeader>;

function render(props: Props) {
	host = document.createElement('div');
	document.body.append(host);
	mount(PageHeader, { target: host, props });
	flushSync();
	return host;
}

describe('the title', () => {
	it('names the screen, once, as the page heading', () => {
		render({ title: 'Browse', icon: 'browse' });

		expect(host.querySelectorAll('h1')).toHaveLength(1);
		expect(host.querySelector('h1')?.textContent?.trim()).toContain('Browse');
	});

	it('steps down a level where the row is a section of a page rather than the page', () => {
		/* Two `h1`s on one document leave a screen reader with two answers to "what is this page",
		 * and an asset grid embedded under a person is not the one that should win. */
		render({ title: "Mara's files", level: 2 });

		expect(host.querySelector('h1')).toBeNull();
		expect(host.querySelector('h2')?.textContent?.trim()).toBe("Mara's files");
	});

	it("carries the rail's glyph, silently", () => {
		// The heading beside it already names the page; an icon that repeats a word out loud is that
		// word said twice to anybody listening.
		render({ title: 'Tags', icon: 'shoppingmode' });

		const icon = host.querySelector('h1 .icon');
		expect(icon).not.toBeNull();
		expect(icon?.getAttribute('aria-hidden')).toBe('true');
	});
});

describe('what sits beside it', () => {
	it('shows a count when there is one', () => {
		render({ title: 'People', icon: 'person', count: 12 });

		expect(host.querySelector('.count')?.textContent).toBe('12');
	});

	it('groups a count the way every other count on screen is grouped', () => {
		render({ title: 'Browse', icon: 'browse', count: 25000 });

		expect(host.querySelector('.count')?.textContent).toBe((25000).toLocaleString());
		expect(host.querySelector('.count')?.textContent).not.toBe('25000');
	});

	it('and nothing at all when there is nothing to count', () => {
		// A screen with nothing on it saying "0" is a screen telling somebody a number they can see.
		render({ title: 'People', icon: 'person', count: 0 });

		expect(host.querySelector('.count')).toBeNull();
	});

	it("renders the page's own controls", () => {
		render({
			title: 'Browse',
			icon: 'browse',
			controls: markup('<button type="button">Order</button>')
		});

		expect(host.querySelector('.controls button')?.textContent).toBe('Order');
	});

	it('keeping the controls region even when a screen has none', () => {
		/* It is what pushes the title left and holds the row's height steady. Without it, a screen
		 * with controls and a screen without sit at two heights and the title moves between them. */
		render({ title: 'Profile', icon: 'account_circle' });

		expect(host.querySelector('.controls')).not.toBeNull();
	});

	it('and putting a sentence under the title rather than on the page', () => {
		// The gap between a title and its own sentence is not the gap between a header and a screen.
		render({
			title: 'Tags',
			icon: 'shoppingmode',
			lede: markup('<span>Drag clips onto a tag.</span>')
		});

		expect(host.querySelector('.page-header .lede')?.textContent).toBe('Drag clips onto a tag.');
	});
});

/*
 * At a phone's width the controls take the line under the title, starting where it starts, in one
 * line; beside it they would wrap into a stack of right-aligned lines with the count between them.
 * The unit environment answers only a plain `screen` rule, so the phone rule is read as one.
 */
describe("the header at a phone's width", () => {
	afterEach(removeStyles);

	it('puts the controls on one line of their own under the title and its count', () => {
		render({ title: 'Downloads', count: 837, controls: markup('<button>Edit cookies</button>') });
		const row = host.querySelector('.row') as HTMLElement;
		const phone = '@media (max-width: 767px)';
		expect(headerSource, 'the header has no phone rule').toContain(phone);
		applyStyles(headerSource.replace(phone, '@media screen'), row);

		const controls = host.querySelector('.controls') as HTMLElement;
		expect(getComputedStyle(row).flexWrap).toBe('wrap');
		expect(getComputedStyle(controls).flexBasis).toBe('100%');
		// Packed to the end: the one right edge every phone control that acts ends at, so a form's
		// header pair and its foot pair read as one.
		expect(getComputedStyle(controls).justifyContent).toBe('flex-end');
		expect(getComputedStyle(controls).flexWrap).toBe('wrap');
		// The count stays on the title's line, before the controls.
		expect(row.querySelector('.count')?.nextElementSibling).toBe(controls);
	});
});

/* The unit environment answers only a plain `screen` rule, so the desk rule is read as one. */
describe('a row of tabs with a search box beside it', () => {
	afterEach(removeStyles);

	it('gives the controls only what the tabs leave, so the box narrows before the tabs wrap', () => {
		render({
			title: 'Tags',
			titleHidden: true,
			beside: markup('<nav>Files Tags History</nav>'),
			controls: markup('<input placeholder="Search tags" />')
		});
		const row = host.querySelector('.row') as HTMLElement;
		const desk = '@media (min-width: 768px)';
		expect(headerSource, 'the header has no desk rule').toContain(desk);
		applyStyles(headerSource.replace(desk, '@media screen'), row);

		const controls = host.querySelector('.controls') as HTMLElement;
		expect(getComputedStyle(controls).flexGrow).toBe('1');
		expect(parseFloat(getComputedStyle(controls).flexBasis)).toBe(0);
		expect(getComputedStyle(controls).minBlockSize).toBe('var(--control-height)');
	});

	it("keeps the box's room on a tab with no box, so the tabs wrap the same on every tab", () => {
		render({ title: 'History', titleHidden: true, beside: markup('<nav>Files History</nav>') });
		const row = host.querySelector('.row') as HTMLElement;
		applyStyles(headerSource.replace('@media (min-width: 768px)', '@media screen'), row);

		const controls = host.querySelector('.controls') as HTMLElement;
		expect(getComputedStyle(controls).minInlineSize).toBe('var(--tab-box-floor)');
		expect(getComputedStyle(controls).minBlockSize).toBe('var(--control-height)');
	});

	it("keeps the box's line on a phone too", () => {
		render({ title: 'History', titleHidden: true, beside: markup('<nav>Files History</nav>') });
		const row = host.querySelector('.row') as HTMLElement;
		applyStyles(headerSource.replace('@media (max-width: 767px)', '@media screen'), row);

		const controls = host.querySelector('.controls') as HTMLElement;
		expect(getComputedStyle(controls).display).toBe('flex');
		expect(getComputedStyle(controls).minBlockSize).toBe('var(--control-height)');
	});

	it('leaves a titled row to size its controls by what they hold', () => {
		render({ title: 'Downloads', controls: markup('<button>Edit cookies</button>') });
		const row = host.querySelector('.row') as HTMLElement;
		applyStyles(headerSource.replace('@media (min-width: 768px)', '@media screen'), row);

		expect(getComputedStyle(host.querySelector('.controls') as HTMLElement).flexGrow).not.toBe('1');
	});
});
