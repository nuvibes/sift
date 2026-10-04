/* A long run's last finished run, said from the record: the words, the hover, and both sources. */

import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { exactly } from '$lib/shell/when';
import { hoverSaid, lineSaid, runWords, sayRun } from './last-run';

const rows = vi.hoisted(() => ({
	last: null as null | { ended_at: number; outcome: string; said: string | null }
}));

vi.mock('$lib/jobs/tasks.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/jobs/tasks.svelte')>()),
	taskList: {
		ensure: vi.fn(async () => {}),
		row: (id: string) => (id === 'backup' && rows.last ? { id, last: rows.last } : undefined)
	}
}));

import LastRun from './LastRun.svelte';

const NOW = 1_790_000_000;

let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	rows.last = null;
	document.body.innerHTML = '';
});

function draw(props: Record<string, unknown>): HTMLElement {
	const host = document.createElement('p');
	document.body.append(host);
	drawn = mount(LastRun, { target: host, props }) as Record<string, unknown>;
	flushSync();
	return host;
}

it('says how the run ended in the words of the page, by its outcome', () => {
	const words = runWords('The backup');
	expect(sayRun({ ended_at: NOW - 7200, outcome: 'done' }, words, NOW)).toBe(
		'The backup ran 2 hours ago.'
	);
	expect(sayRun({ ended_at: NOW - 7200 }, words, NOW)).toBe('The backup ran 2 hours ago.');
	expect(sayRun({ ended_at: NOW - 7200, outcome: 'failed' }, words, NOW)).toContain('problem');
	expect(sayRun({ ended_at: NOW - 7200, outcome: 'canceled' }, words, NOW)).toContain(
		'was stopped'
	);
	expect(sayRun({ ended_at: NOW + 3, outcome: 'done' }, words, NOW)).toBe(
		'The backup ran just now.'
	);
});

it("puts a finished run's own sentence on the line, and a failure's words on the hover", () => {
	const done = { ended_at: NOW, outcome: 'done', said: 'Saved one file.' };
	const failed = { ended_at: NOW, outcome: 'failed', said: 'The disk is full.' };
	expect(lineSaid(done)).toBe('Saved one file.');
	expect(hoverSaid(done)).toBe(exactly(NOW));
	expect(lineSaid(failed)).toBeNull();
	expect(hoverSaid(failed)).toBe(`${exactly(NOW)}. The disk is full.`);
	expect(lineSaid({ ended_at: NOW, said: null })).toBeNull();
});

it("reads a task's last run from its row on Tasks, and draws nothing before it has one", () => {
	const empty = draw({ task: 'backup', words: runWords('The last automatic backup') });
	expect(empty.textContent?.trim()).toBe('');
	unmount(drawn!);
	drawn = null;

	rows.last = { ended_at: Math.floor(Date.now() / 1000) - 120, outcome: 'done', said: 'Saved.' };
	const host = draw({ task: 'backup', words: runWords('The last automatic backup') });
	expect(host.textContent).toContain('The last automatic backup ran 2 minutes ago.');
	expect(host.textContent).toContain('Saved.');
});

it('draws a run handed in by a page whose run is not a task', () => {
	const host = draw({
		run: { ended_at: Math.floor(Date.now() / 1000) - 60, said: 'Imported 2 of 3 files.' }
	});
	expect(host.textContent?.trim()).toMatch(/^It ran .+\.\s+Imported 2 of 3 files\.$/);
});

it("keeps the space before the run's own sentence outside the hover box, where it is drawn", () => {
	const host = draw({
		run: { ended_at: Math.floor(Date.now() / 1000) - 60, said: 'Imported 2 of 3 files.' }
	});
	const box = host.firstElementChild;
	expect(box).not.toBeNull();
	const after: string[] = [];
	for (let node = box!.nextSibling; node; node = node.nextSibling)
		after.push(node.textContent ?? '');
	expect(after.join('')).toMatch(/^\s+Imported 2 of 3 files\.$/);
});
