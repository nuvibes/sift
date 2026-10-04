import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { session } from '$lib/shell/session.svelte';
import HeldFaces from './HeldFaces.svelte';

/*
 * The face descriptions a swap brought for somebody already here: offered on their page, added only
 * by the press, and never asked for on anybody's behalf but an admin's.
 */

vi.mock('$lib/components/swap/swap', async (importOriginal) => ({
	...(await importOriginal<Record<string, unknown>>()),
	heldFaces: vi.fn(),
	addHeldFaces: vi.fn()
}));

const { heldFaces, addHeldFaces } = await import('$lib/components/swap/swap');
const read = vi.mocked(heldFaces);
const add = vi.mocked(addHeldFaces);

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | undefined;

beforeEach(() => {
	vi.clearAllMocks();
	session.viewer = { role: 'admin' } as typeof session.viewer;
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = undefined;
	host?.remove();
	session.viewer = undefined;
});

async function draw(onadded?: (added: number) => void): Promise<HTMLElement> {
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(HeldFaces, {
		target: host,
		props: { personId: 'p1', name: 'Cassia Lynn', onadded }
	});
	await settle();
	return host;
}

async function settle(): Promise<void> {
	for (let turn = 0; turn < 4; turn++) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
}

describe('what a swap brought, on the person page', () => {
	it('offers the count and adds nothing until Add is pressed', async () => {
		read.mockResolvedValue({ waiting: 12, added: 0 });

		await draw();

		expect(host.querySelector('.number')?.textContent?.trim()).toBe(
			'12 facial fingerprints from a swap'
		);
		const press = host.querySelector('button');
		expect(press?.getAttribute('aria-label')).toBe(
			'Add what a swap brought: 12 facial fingerprints'
		);
		expect(add).not.toHaveBeenCalled();
	});

	it('adds on the press, says so, and the offer goes once nothing is left', async () => {
		read
			.mockResolvedValueOnce({ waiting: 1, added: 0 })
			.mockResolvedValue({ waiting: 0, added: 0 });
		add.mockResolvedValue({ waiting: 0, added: 1 });
		const told: number[] = [];

		await draw((added) => told.push(added));
		expect(host.querySelector('.number')?.textContent?.trim()).toBe(
			'1 facial fingerprint from a swap'
		);
		host.querySelector('button')?.click();
		await vi.waitFor(() => {
			flushSync();
			if (read.mock.calls.length < 2) throw new Error('not read again yet');
		});
		await settle();

		expect(add).toHaveBeenCalledWith('p1');
		expect(told).toEqual([1]);
		expect(host.querySelector('.asks')).toBeNull();
	});

	it('draws nothing when nothing is waiting', async () => {
		read.mockResolvedValue({ waiting: 0, added: 0 });

		await draw();

		expect(host.querySelector('.asks')).toBeNull();
	});

	it('asks nothing for anybody but an admin', async () => {
		session.viewer = { role: 'user' } as typeof session.viewer;
		read.mockResolvedValue({ waiting: 5, added: 0 });

		await draw();

		expect(read).not.toHaveBeenCalled();
		expect(host.querySelector('.asks')).toBeNull();
	});
});
