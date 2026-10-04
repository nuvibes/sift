/* Editing: what the editor makes, and how big a smaller copy is.
 *
 * Two headings and nothing else. The confirmations are on General, so none is drawn here, and the
 * compression group says once what the four sizes are for while each size keeps its own line
 * saying whose upload limit it starts at. */

import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { COPY } from './Editing.search';

const mocks = vi.hoisted(() => ({ fetchSettings: vi.fn() }));

vi.mock('$lib/settings-ui/settings', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/settings-ui/settings')>()),
	fetchSettings: mocks.fetchSettings
}));

const Editing = (await import('./Editing.svelte')).default;

function size(key: string, mb: number) {
	return {
		key,
		value: mb,
		default: mb,
		label: `Size ${mb}`,
		help: `Starts at ${mb} MB, one upload limit.`,
		min: 1,
		max: 4000
	};
}

let host: HTMLDivElement;
let drawn: Record<string, unknown> | null = null;

beforeEach(() => {
	mocks.fetchSettings.mockResolvedValue([
		{
			name: 'Editing',
			settings: [
				{
					key: 'edit.gif_format',
					value: 'gif',
					default: 'gif',
					label: 'GIF format',
					choices: ['gif'],
					choice_labels: ['GIF']
				},
				size('compress.target_small_mb', 10),
				size('compress.target_standard_mb', 50),
				size('compress.target_large_mb', 100),
				size('compress.target_very_large_mb', 500)
			]
		}
	]);
	host = document.createElement('div');
	document.body.append(host);
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
});

async function draw(): Promise<string> {
	drawn = mount(Editing, { target: host });
	for (let turn = 0; turn < 8; turn += 1) await Promise.resolve();
	flushSync();
	return (host.textContent ?? '').replace(/\s+/g, ' ');
}

it('heads the format GIFs and draws no confirmations', async () => {
	const words = await draw();

	expect(words).toContain(COPY.gifs);
	// The format's own row, found by the key the server stores it under.
	expect(words).toContain('GIF format');
	expect(words).not.toContain('Animations');
	expect(words).not.toContain('Confirmations');
	expect(host.querySelector('[id="editing.delete.ask"]')).toBeNull();
});

it("says once what the sizes are for, and keeps each size's own line", async () => {
	const words = await draw();

	expect(words).toContain(COPY.compressionHelp);
	for (const mb of [10, 50, 100, 500]) expect(words).toContain(`Starts at ${mb} MB`);
});
