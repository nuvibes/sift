/*
 * Run task on the SELECTION BAR: the same group of groups the right-click menu draws, opened from
 * one button, and a pass two levels down acts on everything picked.
 *
 * The bar draws a group as a door whose rows are `VerbMenuItems` (the right-click renderer) so
 * a stage inside Run task opens out exactly as it does on a tile's menu. This pins that it does, at
 * the depth Run task needs: a renderer that drew only one level would put "Generate now" in the bar
 * with nothing behind it.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { runNowVerb } from '$lib/grid/verbs';
import VerbButtons from './VerbButtons.svelte';

let host: HTMLElement;

afterEach(() => {
	host?.remove();
	document.body.innerHTML = '';
});

const GROUPS = [
	{
		family: 'generate',
		label: 'Generate now',
		every: { key: 'generate:all', label: 'Generate all', help: '' },
		passes: [{ key: 'thumbnails', label: 'Thumbnails', help: '' }]
	},
	{
		family: 'identify',
		label: 'Identify now',
		every: { key: 'identify:all', label: 'Identify all', help: '' },
		passes: [{ key: 'faces', label: 'Faces', help: '' }]
	}
];

function words(row: Element): string {
	return (row.textContent ?? '').replace(/[\uE000-\uF8FF]/g, '').trim();
}

function row(named: string): HTMLElement | undefined {
	return [...document.querySelectorAll<HTMLElement>('[role="menuitem"]')].find(
		(one) => words(one) === named
	);
}

async function press(named: string): Promise<ReturnType<typeof vi.fn>> {
	const ran = vi.fn();
	const verb = runNowVerb(GROUPS, ran);
	if (!verb) throw new Error('nothing to offer');
	host = document.createElement('div');
	document.body.append(host);
	mount(VerbButtons, { target: host, props: { verbs: [verb], ids: ['a', 'b'] } });
	flushSync();

	const door = [...host.querySelectorAll('button')].find((one) => words(one) === 'Run task');
	expect(door, 'the bar has no Run task').toBeTruthy();
	/* The library opens on POINTERDOWN. See `MenuButton.svelte.test.ts`. */
	door?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
	flushSync();

	const stage = await vi.waitFor(
		() => {
			const found = row('Generate now');
			if (!found) throw new Error('no stage row');
			return found;
		},
		{ timeout: 5000 }
	);
	expect(row('Identify now'), 'every stage is a row').toBeTruthy();
	stage.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
	stage.dispatchEvent(new MouseEvent('pointerup', { bubbles: true, button: 0 }));
	stage.click();
	flushSync();

	const pass = await vi.waitFor(
		() => {
			const found = row(named);
			if (!found) throw new Error('no pass row');
			return found;
		},
		{ timeout: 3000 }
	);
	pass.click();
	flushSync();

	return ran;
}

describe('Run task on the selection bar', () => {
	it('opens onto the stages, each stage onto its passes, and a pass runs for every file picked', async () => {
		expect(await press('Thumbnails')).toHaveBeenCalledWith(['a', 'b'], 'thumbnails');
	});

	it("offers the stage's every pass as one press naming the stage", async () => {
		expect(await press('Generate all')).toHaveBeenCalledWith(['a', 'b'], 'generate:all');
	});
});
