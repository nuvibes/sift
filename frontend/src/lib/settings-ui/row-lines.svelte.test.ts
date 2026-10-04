/*
 * The line rule of a settings pane, read from the rendered pane with the compiled stylesheets in
 * place: a line is drawn only BETWEEN two rows, by the lower one; a group's first and last rows
 * draw none; and the group heading's hairline is the only line between two groups. So walking the
 * pane top to bottom never meets two lines with no row between them, which is what a double rule
 * is.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { applyStyles, removeStyles } from '$lib/design/testing-styles';
import labelled from '$lib/components/common/LabelledRow.svelte?raw';
import heading from '$lib/components/common/SectionHeading.svelte?raw';
import separator from '$lib/components/common/Separator.svelte?raw';
import action from './ActionRow.svelte?raw';
import group from './SettingGroup.svelte?raw';
import Probe from './RowLinesProbe.test.svelte';
import DeviceId from '$lib/settings-ui/DeviceId.svelte';

const swapApi = vi.hoisted(() => ({
	swapDevice: vi.fn(),
	swapTunnels: vi.fn(),
	resetDevice: vi.fn()
}));

vi.mock('$lib/components/swap/swap', async (real) => ({
	...(await real<typeof import('$lib/components/swap/swap')>()),
	swapDevice: () => swapApi.swapDevice(),
	swapTunnels: () => swapApi.swapTunnels(),
	resetDevice: () => swapApi.resetDevice()
}));

let drawn: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	removeStyles();
	document.body.innerHTML = '';
});

function drawPane(): HTMLElement {
	drawn = mount(Probe, { target: document.body });
	flushSync();
	const pane = document.body;
	const rows = [...pane.querySelectorAll<HTMLElement>('.row')];
	const plain = rows.find((row) => row.querySelector('.control')?.textContent === 'a');
	const pressed = rows.find((row) => row.querySelector('.press'));
	applyStyles(separator);
	applyStyles(heading, pane.querySelector('.section-heading'));
	applyStyles(group, pane.querySelector('.group'));
	applyStyles(labelled, plain);
	applyStyles(action, pressed);
	return pane;
}

/**
 * The hairline an element draws on one side, from its computed style: a logical border as the
 * rows write it, or a physical one. The unit environment keeps a logical border as written and
 * does not fold it into the physical sides, so both are read.
 */
function ruled(element: Element, side: 'start' | 'end'): boolean {
	const style = getComputedStyle(element);
	const logical = style.getPropertyValue(`border-block-${side}`);
	if (/\bsolid\b/.test(logical) && !/^0/.test(logical)) return true;
	const physical = side === 'start' ? 'top' : 'bottom';
	return (
		style.getPropertyValue(`border-${physical}-style`) === 'solid' &&
		parseFloat(style.getPropertyValue(`border-${physical}-width`)) >= 1
	);
}

/** The pane top to bottom: a `line` for every hairline, a row's name for every row. */
function walk(pane: HTMLElement): string[] {
	const seen: string[] = [];
	for (const element of pane.querySelectorAll('*')) {
		if (element.classList.contains('separator')) {
			if (getComputedStyle(element).display !== 'none') seen.push('line');
			continue;
		}
		if (ruled(element, 'start')) seen.push('line');
		if (element.classList.contains('name')) seen.push(element.textContent ?? '');
		if (ruled(element, 'end')) seen.push('line');
	}
	return seen;
}

describe('the lines on a settings pane', () => {
	it('draws a line only between two rows, and the heading line only between two groups', () => {
		expect(walk(drawPane())).toEqual([
			'One',
			'line',
			'Two',
			'line',
			'Three',
			'line',
			'Four',
			'line',
			'Five',
			'line',
			'Six',
			'line',
			'Seven',
			'line',
			'Eight',
			'line',
			'Nine',
			'line',
			'Ten'
		]);
	});

	it('never meets two lines with no row between them', () => {
		const seen = walk(drawPane());
		const doubled = seen.findIndex((one, at) => one === 'line' && seen[at + 1] === 'line');
		expect(doubled).toBe(-1);
		expect(seen.at(0)).not.toBe('line');
		expect(seen.at(-1)).not.toBe('line');
	});
});

/*
 * A group a pane draws through another component is held to the same rule: the Swaps block on
 * Updates and Info is the pane's own rows, so its lines are the rows' lines and nothing it builds
 * by hand touches the row above it.
 */
describe('the Swaps group on Updates and Info', () => {
	async function drawSwaps(): Promise<HTMLElement> {
		swapApi.swapDevice.mockResolvedValue({ device_id: 'ABCDEFGHIJKLMNOP', locked: false });
		drawn = mount(DeviceId, { target: document.body });
		await vi.waitFor(() => expect(document.body.textContent).toContain('ABCD-EFGH-IJKL-MNOP'));
		flushSync();
		const pane = document.body;
		const rows = [...pane.querySelectorAll<HTMLElement>('.row')];
		applyStyles(separator);
		applyStyles(heading, pane.querySelector('.section-heading'));
		applyStyles(group, pane.querySelector('.group'));
		applyStyles(
			labelled,
			rows.find((row) => !row.querySelector('.press'))
		);
		return pane;
	}

	it('is made of rows, with nothing built beside them', async () => {
		const pane = await drawSwaps();
		const parts = [...(pane.querySelector('.group .rows')?.children ?? [])];
		expect(parts.length).toBeGreaterThanOrEqual(1);
		const handBuilt = parts.filter((part) => !part.matches('.ruled-row, .band'));
		expect(handBuilt.map((part) => part.outerHTML.slice(0, 80))).toEqual([]);
	});

	it('holds the device id alone: the swap starts from the Swap screen', async () => {
		expect(walk(await drawSwaps())).toEqual(['Your device id']);
	});
});

/*
 * The space between two groups is the group's own (its margin, which collapses with the room above
 * the next heading). A pane that also lays its groups out as a column with a gap adds the gap on
 * top, and in a flex column the two stop collapsing, so the bands between its groups come out two
 * or three times the height of every other pane's.
 */
describe('the space between groups', () => {
	/** What draws a group, or a run of them. */
	const GROUPS = /<(SettingGroup|RecognitionPane|PresetGroup)[\s>]/;

	/** Every div or section wearing `name`, as its markup from the opening tag to its own close. */
	function elementsWearing(markup: string, name: string): string[] {
		const found: string[] = [];
		const opening = /<(div|section)\b[^>]*\bclass="([^"]*)"/g;
		for (let open = opening.exec(markup); open; open = opening.exec(markup)) {
			if (!open[2].split(/\s+/).includes(name)) continue;
			const tag = open[1];
			const either = new RegExp(`<${tag}\\b|</${tag}>`, 'g');
			either.lastIndex = open.index + 1;
			let depth = 1;
			let end = markup.length;
			for (let next = either.exec(markup); next; next = either.exec(markup)) {
				depth += next[0].startsWith('</') ? -1 : 1;
				if (depth === 0) {
					end = next.index;
					break;
				}
			}
			found.push(markup.slice(open.index, end));
		}
		return found;
	}

	/** The classes this pane lays out as a column with a gap, and that hold a group somewhere. */
	function gappedOverGroups(file: string): string[] {
		const source = readFileSync(`src/lib/settings-ui/${file}`, 'utf8');
		const markup = source.slice(source.lastIndexOf('</script>'));
		const style = source.match(/<style[^>]*>([\s\S]*?)<\/style>/)?.[1] ?? '';
		const gapped: string[] = [];
		for (const rule of style.matchAll(/\.([\w-]+)\s*\{([^}]*)\}/g)) {
			const [, name, body] = rule;
			if (!/flex-direction:\s*column/.test(body) || !/(^|[\s;])gap:/.test(body)) continue;
			if (elementsWearing(markup, name).some((element) => GROUPS.test(element))) {
				gapped.push(`${file} .${name}`);
			}
		}
		return gapped;
	}

	it('is never a gap laid on top of the groups, at any depth of a pane', () => {
		const panes = readdirSync('src/lib/settings-ui').filter((file) => file.endsWith('.svelte'));
		expect(panes.length).toBeGreaterThan(20);
		expect(panes.flatMap(gappedOverGroups)).toEqual([]);
	});

	it('reads a gapped column wherever it is, not only at the root', () => {
		/* The reader itself, on a planted pane: a column nested two deep, holding a group. */
		const planted = [
			'<section>',
			'\t<div class="outer">',
			'\t\t<div class="fields">',
			'\t\t\t<div class="inner"></div>',
			'\t\t\t<SettingGroup heading="One" />',
			'\t\t</div>',
			'\t</div>',
			'</section>'
		].join('\n');
		expect(elementsWearing(planted, 'fields')[0]).toMatch(GROUPS);
		expect(elementsWearing(planted, 'inner')[0]).not.toMatch(GROUPS);
	});
});
