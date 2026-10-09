/* The batch rename sheet: it shows the server's plan before anything moves, asks again when the
 * words or the clash rule change, and renames only on the press, with one Undo for the batch. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import BatchRename from './BatchRename.svelte';
import type { RenamePreview } from './batch-rename';

const calls = vi.hoisted(() => ({
	preview: vi.fn(),
	apply: vi.fn(),
	undo: vi.fn(),
	toast: vi.fn()
}));

vi.mock('./batch-rename', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	previewRename: calls.preview,
	applyRename: calls.apply
}));

vi.mock('$lib/organize/organize.svelte', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	undo: calls.undo
}));

vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: calls.toast } }));

/* The clash chooser is a listbox that needs a layout jsdom does not have; replaced by something
   that hands a value back the way choosing one does. */
const picker = vi.hoisted(() => ({
	props: null as { value: string; onValueChange?: (value: string) => void } | null
}));

vi.mock('$lib/components/common', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	Select: (_anchor: unknown, props: { value: string }) => {
		picker.props = props as typeof picker.props;
	}
}));

function answer(over: Partial<RenamePreview> = {}): RenamePreview {
	return {
		rows: [
			{ asset_id: 'a1', before: 'one.mp4', after: 'Trip 1.mp4', state: 'renamed', reason: null },
			{ asset_id: 'a2', before: 'two.mp4', after: 'Trip-1.mp4', state: 'numbered', reason: null }
		],
		total: 2,
		renaming: 2,
		numbered: 1,
		same: 0,
		clashes: 1,
		refused: 0,
		as_task: false,
		words: { name: 'The name it has now.', n: 'Its place in this batch.' },
		...over
	};
}

let host: HTMLElement;

beforeEach(() => {
	vi.useFakeTimers();
	calls.preview.mockReset().mockResolvedValue(answer());
	calls.apply
		.mockReset()
		.mockResolvedValue({ renamed: 2, skipped: 0, receipt_id: 'r1', job_id: null, reason: null });
	calls.undo.mockReset().mockResolvedValue({ undone: true, put_back: 2, of: 2, said: null });
	calls.toast.mockReset();
});

afterEach(() => {
	vi.useRealTimers();
	host?.remove();
	document.body.innerHTML = '';
});

async function render(assetIds: string[] = ['a1', 'a2']) {
	host = document.createElement('div');
	document.body.append(host);
	const props = reactiveProps({ open: true, assetIds });
	mount(BatchRename, { target: host, props });
	flushSync();
	await vi.advanceTimersByTimeAsync(300);
	flushSync();
	return props;
}

describe('the plan, before anything moves', () => {
	it('asks the server for every name and draws its answer, clashes marked', async () => {
		await render();

		expect(calls.preview).toHaveBeenCalledWith(['a1', 'a2'], '{name} {n}', 'number');
		const rows = [...document.querySelectorAll('.plan .row')].map((row) => row.textContent);
		expect(rows[0]).toContain('Trip 1.mp4');
		expect(rows[1]).toContain('Name taken, numbered');
		expect(document.querySelector('.summary')?.textContent).toContain('One name was taken');
		expect(calls.apply).not.toHaveBeenCalled();
	});

	it('asks again when the clash rule changes', async () => {
		await render();

		picker.props!.onValueChange?.('skip');
		flushSync();
		await vi.advanceTimersByTimeAsync(300);

		expect(calls.preview).toHaveBeenLastCalledWith(['a1', 'a2'], '{name} {n}', 'skip');
	});

	it('offers the words the server fills, as chips that put one in the box', async () => {
		await render();

		const chip = [...document.querySelectorAll('.words button')].find(
			(button) => button.textContent?.trim() === '{n}'
		) as HTMLButtonElement;
		chip.click();
		flushSync();

		expect((document.querySelector('.sheet input') as HTMLInputElement).value).toContain('{n}');
	});
});

describe('the words follow the count', () => {
	/* One file is "Rename this file", never "Each file gets a name" and "New names". */
	it('speaks of one file and one name when it renames one', async () => {
		calls.preview.mockResolvedValue(answer({ rows: answer().rows.slice(0, 1), total: 1 }));
		await render(['a1']);
		const sheet = document.querySelector('.sheet') as HTMLElement;
		expect(sheet.textContent).toContain('Rename this file');
		expect(sheet.textContent).toContain('This file gets a name made from the words below.');
		expect(sheet.textContent).not.toContain('Each file');
		expect(sheet.querySelector('label')?.textContent?.trim()).toBe('New name');
	});

	/* One file starting from "{name} {n}" would be offered "clip 1.mp4" for a rename. */
	it('starts one file from its own name and offers no place in a batch', async () => {
		calls.preview.mockResolvedValue(answer({ rows: answer().rows.slice(0, 1), total: 1 }));
		await render(['a1']);
		expect(calls.preview).toHaveBeenCalledWith(['a1'], '{name}', 'number');
		expect((document.querySelector('.sheet input') as HTMLInputElement).value).toBe('{name}');
		const chips = [...document.querySelectorAll('.words button')].map((one) =>
			one.textContent?.trim()
		);
		expect(chips).toEqual(['{name}']);
	});

	it('speaks of each file and their names when it renames several', async () => {
		await render();
		const sheet = document.querySelector('.sheet') as HTMLElement;
		expect(sheet.textContent).toContain('Each file gets a name made from the words below.');
		expect(sheet.querySelector('label')?.textContent?.trim()).toBe('New names');
	});

	/* "Press a word..." must not sit against the first chip with no gap. */
	it('stands the words off the help above them by the field gap', async () => {
		await render();
		const source = (await import('./BatchRename.svelte?raw')).default;
		const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
		const words = document.querySelector('.words') as HTMLElement;
		applyStyles(source, words);
		expect(getComputedStyle(words).getPropertyValue('margin-block-start')).toBe('var(--space-2)');
		removeStyles();
	});
});

describe('the press', () => {
	it('renames the batch once, and its toast carries the one Undo for all of it', async () => {
		await render();

		(document.querySelector('button[type="submit"]') as HTMLButtonElement).click();
		await vi.advanceTimersByTimeAsync(0);

		expect(calls.apply).toHaveBeenCalledTimes(1);
		const [message, options] = calls.toast.mock.calls[0];
		expect(message).toBe('Renamed 2 files');
		options.action.run();
		await vi.advanceTimersByTimeAsync(0);
		expect(calls.undo).toHaveBeenCalledWith('r1');
		expect(calls.toast.mock.calls.at(-1)?.[0]).toBe('Put every name back');
	});

	it('says how many names went back when some files had been renamed again since', async () => {
		const said = 'Put back 1 of 2 names. The others keep the names they have now.';
		calls.undo.mockResolvedValue({ undone: true, put_back: 1, of: 2, said });
		await render();

		(document.querySelector('button[type="submit"]') as HTMLButtonElement).click();
		await vi.advanceTimersByTimeAsync(0);
		calls.toast.mock.calls[0][1].action.run();
		await vi.advanceTimersByTimeAsync(0);

		expect(calls.toast.mock.calls.at(-1)?.[0]).toBe(said);
	});
});
