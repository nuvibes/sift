/*
 * One proposed shoot opened up: every picture of it as a wall, with the card's question and its
 * answers, at an address of its own (`/organize/shoots/<id>`, through the queue's detail registry).
 */
import { afterEach, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

import { detailFor } from '$lib/organize/panels';
import { noServerAt } from '../../../test-setup';

/* Left unanswered on purpose: the board the header reads for its tabs, and the frame's settings. */
noServerAt('/api/workbench');
noServerAt('/api/settings/interface');

const mocks = vi.hoisted(() => ({
	shoot: vi.fn(),
	makeTheSet: vi.fn(),
	openAsset: vi.fn()
}));

vi.mock('$app/state', () => ({ page: { params: { queue: 'shoots', id: 's-1' } } }));
vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$lib/player/asset-view', () => ({ openAsset: mocks.openAsset }));
vi.mock('$lib/entity/shoots.svelte', () => ({
	shoot: mocks.shoot,
	makeTheSet: mocks.makeTheSet,
	notASet: vi.fn(),
	nameTheRest: vi.fn()
}));

import ShootDetail from './ShootDetail.svelte';

let host: HTMLElement;
let instance: Record<string, unknown> | null = null;

afterEach(() => {
	if (instance) void unmount(instance, { outro: false });
	instance = null;
	host?.remove();
});

function shootOf(pictures: number) {
	return {
		id: 's-1',
		person_id: 'p-1',
		name: 'Neve Arbogast',
		found_at: 0,
		pictures,
		unnamed: 0,
		question: `Do these ${pictures} photos of Neve Arbogast belong together?`,
		detail: `${pictures} photos of Neve Arbogast that are in no Photo Set`,
		links: [],
		items: Array.from({ length: pictures }, (_one, at) => ({
			id: `asset-${at}`,
			art: null,
			named: true,
			media_type: 'image'
		}))
	};
}

async function draw(): Promise<void> {
	host = document.createElement('div');
	document.body.append(host);
	instance = mount(ShootDetail, { target: host });
	flushSync();
	for (let turn = 0; turn < 5; turn++) await tick();
	flushSync();
}

it('is the detail screen of the Shoots queue', () => {
	expect(detailFor('shoots')).toBe(ShootDetail);
});

it('draws every picture of the shoot, with the card question and its answers', async () => {
	mocks.shoot.mockResolvedValue(shootOf(30));

	await draw();

	expect(mocks.shoot).toHaveBeenCalledWith('s-1');
	expect(host.querySelectorAll('.wall img')).toHaveLength(30);
	expect(host.textContent).toContain('Do these 30 photos of Neve Arbogast belong together?');
	expect(host.textContent).toContain('Create Photo Set');
});

it('makes the Photo Set from the page and goes back to the wall', async () => {
	const { goto } = await import('$app/navigation');
	mocks.shoot.mockResolvedValue(shootOf(3));
	mocks.makeTheSet.mockResolvedValue({});

	await draw();
	[...host.querySelectorAll<HTMLButtonElement>('button')]
		.find((one) => one.textContent?.includes('Create Photo Set'))
		?.click();
	for (let turn = 0; turn < 5; turn++) await tick();

	expect(mocks.makeTheSet).toHaveBeenCalledWith('s-1');
	expect(vi.mocked(goto)).toHaveBeenCalledWith('/organize/shoots');
});

it('takes a refusal for pictures already in a Photo Set as the shoot settled, and says so', async () => {
	/* A 409 is a card whose pictures went into a Photo Set while it stood: the server answers the
	   card by that set as it refuses, so the page goes back to the wall with the server's sentence. */
	const { goto } = await import('$app/navigation');
	const { ApiError } = await import('$lib/api/client');
	const { toasts } = await import('$lib/shell/toasts.svelte');
	vi.mocked(goto).mockClear();
	mocks.shoot.mockResolvedValue(shootOf(3));
	mocks.makeTheSet.mockRejectedValue(
		new ApiError(
			409,
			'That conflicts',
			'Those pictures are already in a Photo Set, so nothing new was made.'
		)
	);

	await draw();
	[...host.querySelectorAll<HTMLButtonElement>('button')]
		.find((one) => one.textContent?.includes('Create Photo Set'))
		?.click();
	for (let turn = 0; turn < 5; turn++) await tick();

	expect(vi.mocked(goto)).toHaveBeenCalledWith('/organize/shoots');
	const said = toasts.items.at(-1);
	expect(said?.message).toContain('already in a Photo Set');
	expect(said?.tone).toBe('info');
});
