import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { reactiveProps, words as wordsOn } from '$lib/design/testing.svelte';
import FileActionsProbe from './FileActionsProbe.test.svelte';

/* These actions change files on somebody's disk, so what is asserted here is when they appear.
 *
 * A greyed-out button on every file in a read-only library teaches people to ignore the menu; a
 * button that appears and then refuses is worse. The rule is that the server is asked first and the
 * controls are absent unless it says yes, and the hiding is a courtesy, not the control, which is
 * why the last group asserts the request is made rather than assumed.
 */

const get = vi.fn();
const post = vi.fn();

vi.mock('$lib/api/client', async () => {
	const actual = await vi.importActual<typeof import('$lib/api/client')>('$lib/api/client');
	return {
		...actual,
		api: {
			get: (...args: unknown[]) => get(...args),
			post: (...args: unknown[]) => post(...args)
		}
	};
});

const show = vi.fn();
vi.mock('$lib/shell/toasts.svelte', () => ({
	toasts: { show: (...args: unknown[]) => show(...args) }
}));

let host: HTMLElement;

beforeEach(() => {
	get.mockReset();
	post.mockReset();
	show.mockReset();
});

afterEach(() => {
	host?.remove();
	document.body.innerHTML = '';
});

/** Mount, and let the answer the server was going to give arrive before anything is asserted. */
async function render(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	const reactive = reactiveProps({ id: 'asset-1', filename: 'clip.mp4', ...props });
	mount(FileActionsProbe, { target: host, props: reactive });
	flushSync();
	await Promise.resolve();
	await Promise.resolve();
	flushSync();
	return reactive;
}

function button(label: string): HTMLButtonElement | undefined {
	return [...document.querySelectorAll('button')].find((element) => wordsOn(element) === label) as
		HTMLButtonElement | undefined;
}

function allowed(extra: Record<string, unknown> = {}) {
	get.mockResolvedValue({ can_organize: true, reason: null, undo_move_id: null, ...extra });
}

describe('whether the actions are offered at all', () => {
	it('shows them when the server says the file can be organized', async () => {
		allowed();

		await render();

		expect(button('Rename')).toBeDefined();
		// And moving is deliberately NOT here. It is the same verb every surface showing files
		// offers, so it comes from the one place that declares them; what is left here is the pair
		// that only exists on a single file's own screen.
		expect(button('Move')).toBeUndefined();
		expect(button('Hide')).toBeUndefined();
	});

	it('offers undoing a move only while the server still holds one', async () => {
		allowed({ undo_move_id: 'move-1' });

		await render();

		expect(button('Undo move')).toBeDefined();
	});

	it('does not offer undoing a move when there is nothing to put back', async () => {
		allowed();

		await render();

		expect(button('Undo move')).toBeUndefined();
	});

	it('shows nothing at all on a library Sift may not change', async () => {
		get.mockResolvedValue({
			can_organize: false,
			reason: 'Sift is not allowed to change files in Videos.',
			undo_move_id: null
		});

		await render();

		expect(button('Rename')).toBeUndefined();
		expect(button('Undo move')).toBeUndefined();
		// Not the reason either. Nobody asked a question, so there is nothing to answer.
		expect(host.textContent?.trim()).toBe('');
	});

	it('shows nothing when the server could not be asked', async () => {
		get.mockRejectedValue(new Error('offline'));

		await render();

		expect(host.textContent?.trim()).toBe('');
	});

	it('asks the server rather than deciding for itself', async () => {
		allowed();

		await render({ id: 'asset-9' });

		expect(get).toHaveBeenCalledWith('/assets/asset-9/organize');
	});
});

describe('renaming', () => {
	it('opens with the name the file already has, so it can be edited rather than retyped', async () => {
		allowed();
		await render({ filename: 'holiday.mp4' });

		button('Rename')?.click();
		flushSync();

		expect((document.querySelector('input') as HTMLInputElement).value).toBe('holiday.mp4');
	});

	it('sends the new name and shows it once the server has taken it', async () => {
		allowed();
		post.mockResolvedValue({
			asset_id: 'asset-1',
			location_id: 'loc-1',
			filename: 'beach.mp4',
			folder_id: null,
			move_id: 'move-1'
		});
		await render();

		button('Rename')?.click();
		flushSync();
		const input = document.querySelector('input') as HTMLInputElement;
		input.value = 'beach.mp4';
		input.dispatchEvent(new Event('input'));
		flushSync();
		document.querySelector('form')?.requestSubmit();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		expect(post).toHaveBeenCalledWith('/assets/asset-1/rename', { body: { name: 'beach.mp4' } });
		expect(button('Undo move')).toBeDefined();

		// And the component is holding the new name, not the one it was given as a prop. Reopening
		// the box is where that shows: without this it could keep offering `clip.mp4` forever.
		button('Rename')?.click();
		flushSync();
		expect((document.querySelector('input') as HTMLInputElement).value).toBe('beach.mp4');
	});

	it('shows the server sentence when the name is refused', async () => {
		const { ApiError } = await vi.importActual<typeof import('$lib/api/client')>('$lib/api/client');
		allowed();
		post.mockRejectedValue(
			new ApiError(409, 'Conflict', "There is already something called 'taken.mp4' in that folder.")
		);
		await render();

		button('Rename')?.click();
		flushSync();
		document.querySelector('form')?.requestSubmit();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		// The words are the product: they say what to do next. A generic failure would leave
		// somebody with a box that will not close and no idea what to change.
		// The body, not the host: the box is a sheet and a sheet is portalled out of the markup.
		expect(document.body.textContent).toContain("already something called 'taken.mp4'");
	});
});

describe('undo', () => {
	it('is offered only once there is something to take back', async () => {
		allowed();
		await render();

		expect(button('Undo move')).toBeUndefined();
	});

	it('is offered when the server names a move that has not been undone', async () => {
		allowed({ undo_move_id: 'move-7' });
		await render();

		expect(button('Undo move')).toBeDefined();
	});

	it('names the move the server offered, not one it worked out for itself', async () => {
		// Two moves exist as far as this component is concerned; only the one it was handed may be
		// used. Undoing anything else would take back something nobody asked about.
		allowed({ undo_move_id: 'move-7' });
		post.mockResolvedValue({
			asset_id: 'asset-1',
			location_id: 'loc-1',
			filename: 'clip.mp4',
			folder_id: null,
			move_id: null
		});
		await render();

		button('Undo move')?.click();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();

		expect(post).toHaveBeenCalledWith('/moves/move-7/undo', {});
		expect(post).toHaveBeenCalledTimes(1);
	});
});
