import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import StageBar from './StageBar.svelte';
import stageBarSource from './StageBar.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import { reactiveProps } from '$lib/design/testing.svelte';

/* The bar a filled screen rises from: shut draws nothing, and quiet is mounted but unreachable. */

const words = (text: string) => createRawSnippet(() => ({ render: () => `<span>${text}</span>` }));

let host: HTMLDivElement;
let mounted: Record<string, unknown> | null = null;

function draw(props: Record<string, unknown>) {
	mounted = mount(StageBar, {
		target: host,
		props: {
			open: true,
			label: 'Wall controls',
			children: words('controls'),
			...props
		}
	}) as Record<string, unknown>;
	flushSync();
}

beforeEach(() => {
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	host.remove();
});

it('is a region rather than a dialog, so nothing behind it is blocked', () => {
	// What is on the screen is still playing. A bar that took the keyboard to announce itself would
	// be in the way of exactly the thing somebody is watching.
	draw({});

	const bar = host.querySelector('.stage-bar');
	expect(bar?.getAttribute('role')).toBe('region');
	expect(bar?.getAttribute('aria-label')).toBe('Wall controls');
});

it('draws nothing at all when it is shut, rather than an empty strip', () => {
	draw({ open: false });

	expect(host.querySelector('.stage-bar')).toBeNull();
});

it('is unreachable while it is quiet, not merely invisible', () => {
	/* Faded rather than unmounted, because unmounting would replay its arrival every time a pointer
	   moved. That leaves it in the document, so it has to be taken out of the tab order and out of
	   the accessibility tree at the same moment, or a screen whose chrome has gone quiet still has a
	   row of buttons across it that both the keyboard and a screen reader can reach. */
	draw({ quiet: true });

	const bar = host.querySelector('.stage-bar');
	expect(bar, 'quiet is not the same as shut: it stays mounted').not.toBeNull();
	// Read as a PROPERTY. Svelte sets `inert` on the element rather than as an attribute, and jsdom
	// reports a missing feature as `undefined`, so an attribute check here answers false whether
	// the rule is there or not, which is the shape of a test that cannot fail.
	expect((bar as HTMLElement).inert).toBe(true);
	expect(bar?.getAttribute('aria-hidden')).toBe('true');
});

it('is reachable again the moment it is not quiet', () => {
	draw({});

	const bar = host.querySelector('.stage-bar');
	// Falsy: jsdom has no `inert`, so an unset one reads `undefined`.
	expect((bar as HTMLElement).inert).toBeFalsy();
	expect(bar?.getAttribute('aria-hidden')).toBe('false');
});

it('is visible at once on the way up, so the Tab that raises it can enter it', () => {
	const source = stageBarSource.replace(/\t/g, '');
	expect(source, 'the bar stayed hidden while it faded in').toContain(
		'transition:\ntranslate var(--dur-slow) var(--ease),\nopacity var(--dur-slow) var(--ease);\n}'
	);
	expect(source).toContain('visibility var(--dur-slow) var(--ease-in);');
});

it('puts the picker before the controls, because they are read in that order', () => {
	draw({ lead: words('cell two') });

	// The scope hash rides on every class here, so the names are read off the front of each.
	const inside = [...(host.querySelector('.stage-bar')?.children ?? [])].map(
		(one) => one.className.toString().split(' ')[0]
	);
	expect(inside).toEqual(['lead', 'controls']);
});

it('leaves the picker out entirely when there is nothing to pick between', () => {
	draw({});

	expect(host.querySelector('.lead')).toBeNull();
	expect(host.querySelector('.controls')).not.toBeNull();
});

/*
 * What it reports as its height is its border box.
 *
 * Read off the source, because jsdom lays nothing out and every box is zero, so no mounted test can
 * tell the two bindings apart. The number goes to whatever must not be under the bar, and this bar
 * has a hairline on each edge: a content height leaves both out, and a wall meeting the bar's top
 * edge exactly would meet it two pixels late.
 */
it('reports the room it takes including its own hairlines', () => {
	const source = readFileSync(resolve('src/lib/components/theater/StageBar.svelte'), 'utf8');
	expect(source).toContain('bind:offsetHeight={tall}');
	expect(source, 'the bar went back to reporting its content box').not.toContain(
		'bind:clientHeight'
	);
});

/* The picker at the leading edge stands in the row named for the transport, which the controls
   share through a subgrid, so it stays level with Play when a row opens under the transport. */
it('stands the picker in the transport row, which the controls share', () => {
	draw({ lead: words('picker') });
	const bar = host.querySelector('.stage-bar') as HTMLElement;
	applyStyles(stageBarSource, bar);
	expect(getComputedStyle(bar).gridTemplateRows).toContain('[transport]');
	const lead = host.querySelector('.lead') as HTMLElement;
	expect(getComputedStyle(lead).gridRow).toBe('transport');
	const controls = host.querySelector('.controls') as HTMLElement;
	expect(getComputedStyle(controls).gridTemplateRows).toBe('subgrid');
	expect(getComputedStyle(controls).gridRow).toBe('1 / -1');
	removeStyles();
});

/* Content-width columns, so the space either side of the transport is the one gap whatever the
   picker holds; a phone's row is the shared bar's own. */
it('gives the transport the same gap on both sides', () => {
	const row = createRawSnippet(() => ({
		render: () =>
			'<div class="bar player-bar"><div class="row"></div><div class="row phone"></div></div>'
	}));
	draw({ children: row });
	applyStyles(stageBarSource, host.querySelector('.stage-bar'));
	const [wide, phone] = [...host.querySelectorAll('.row')] as HTMLElement[];
	expect(getComputedStyle(wide).gridTemplateColumns).toBe(
		'minmax(0, max-content) auto minmax(0, max-content)'
	);
	expect(getComputedStyle(wide).columnGap).toBe('var(--space-8)');
	expect(getComputedStyle(phone).gridTemplateColumns).not.toContain('max-content');
	removeStyles();
});

/* The bar sizes to what it holds, so the most it can hold is read from where its widest edges fall. */
it('tells its caller the widest its contents can grow, inside its own padding and edge', () => {
	const real = window.getComputedStyle.bind(window);
	const edges: Record<string, string> = {
		paddingLeft: '12px',
		paddingRight: '12px',
		borderLeftWidth: '1px',
		borderRightWidth: '1px'
	};
	vi.spyOn(Element.prototype, 'clientWidth', 'get').mockImplementation(function (this: Element) {
		return this.classList.contains('widest') ? 500 : 0;
	});
	vi.spyOn(window, 'getComputedStyle').mockImplementation((element, pseudo) => {
		const style = real(element, pseudo);
		if (!element.classList.contains('stage-bar')) return style;
		return new Proxy(style, {
			get: (target, key) =>
				typeof key === 'string' && key in edges ? edges[key] : target[key as never]
		});
	});
	try {
		const props = reactiveProps({
			open: true,
			label: 'Wall controls',
			children: words('x'),
			room: 0
		});
		mounted = mount(StageBar, { target: host, props }) as Record<string, unknown>;
		flushSync();
		expect(props.room).toBe(474);
	} finally {
		vi.restoreAllMocks();
	}
});
