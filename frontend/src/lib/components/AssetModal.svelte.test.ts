/*
 * Leaving the file panel is one step back, however many times the veil is pressed.
 *
 * Leaving is a step back in history and the panel stays drawn until that step lands, so every press
 * on the veil before then would be another step: a double click would close the panel AND leave the
 * screen it was opened over.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { reactiveProps } from '$lib/design/testing.svelte';
import { mini } from '$lib/player/mini.svelte';

const ahead = vi.hoisted(() => ({ drop: vi.fn() }));

vi.mock('$lib/components/AssetView.svelte', async () => ({
	default: (await import('./AssetModalStub.test.svelte')).default
}));
vi.mock('$lib/player/asset-view', () => ({
	canStepBack: () => false,
	canStepForward: () => false,
	dismissingAsset: () => {},
	dropAhead: ahead.drop,
	lookAhead: () => {},
	playOn: async () => null,
	runGoesOn: () => false,
	showAsset: () => {},
	showStranger: () => {},
	stepAwayFrom: async () => null,
	stepBack: () => null,
	stepForward: async () => null
}));
vi.mock('$lib/player/dwell.svelte', () => ({
	dwell: { pictures: false, load: async () => {} }
}));

import AssetModal from './AssetModal.svelte';

let host: HTMLElement;
let instance: ReturnType<typeof mount> | null = null;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	host?.remove();
});

function open(id: string) {
	host = document.createElement('div');
	document.body.append(host);
	const onclose = vi.fn();
	const props = reactiveProps({ id, onclose });
	instance = mount(AssetModal, { target: host, props });
	flushSync();
	const veil = host.querySelector('button.veil') as HTMLButtonElement;
	return { props, onclose, veil };
}

describe('leaving the file panel', () => {
	it('steps back once for a double click on the veil', () => {
		const { onclose, veil } = open('file-one');

		veil.click();
		veil.click();

		expect(onclose).toHaveBeenCalledTimes(1);
	});

	it('steps back once for Escape pressed twice', () => {
		const { onclose } = open('file-one');

		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
		window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

		expect(onclose).toHaveBeenCalledTimes(1);
	});

	it('can still be left when the step back lands on another file', () => {
		/* A file opened from inside the panel is a history entry of its own, so Back from it lands
		   on the first file's panel, which has to close in its turn. */
		const { props, onclose, veil } = open('file-two');

		veil.click();
		props.id = 'file-one';
		flushSync();
		veil.click();

		expect(onclose).toHaveBeenCalledTimes(2);
	});
});

describe('what was found ahead', () => {
	it('is dropped when the panel closes', () => {
		ahead.drop.mockClear();
		open('file-one');

		unmount(instance!);
		instance = null;

		expect(ahead.drop).toHaveBeenCalledTimes(1);
	});

	it('is kept for the corner the file goes down to', () => {
		ahead.drop.mockClear();
		open('file-one');
		mini.handover = true;
		flushSync();

		unmount(instance!);
		instance = null;
		mini.left();

		expect(ahead.drop).not.toHaveBeenCalled();
	});
});
