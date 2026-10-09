/* The sign-in screen draws NOTHING until the server has said whether the instance has its admin. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';

const asked = vi.hoisted(() => ({
	answer: null as null | ((needs: boolean) => void),
	refuse: null as null | ((why: Error) => void)
}));

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => undefined) }));
vi.mock('$lib/shell/session.svelte', () => ({
	session: { isSignedIn: false },
	needsSetup: vi.fn(
		() =>
			new Promise<boolean>((resolve, reject) => {
				asked.answer = resolve;
				asked.refuse = reject;
			})
	)
}));

import { goto } from '$app/navigation';
import Login from './+page.svelte';

let host: HTMLElement;
let drawn: ReturnType<typeof mount> | null = null;

beforeEach(() => {
	vi.mocked(goto).mockClear();
	asked.answer = asked.refuse = null;
	host = document.createElement('div');
	document.body.append(host);
	drawn = mount(Login, { target: host });
	flushSync();
});

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	host.remove();
});

/** Whatever of the sign-in form is on screen: its fields, its button, its words. */
function form(): { fields: number; text: string } {
	return { fields: host.querySelectorAll('input, button').length, text: host.textContent ?? '' };
}

async function settle(): Promise<void> {
	for (let round = 0; round < 5; round += 1) await tick();
	flushSync();
}

describe('the sign-in screen before the server has answered', () => {
	it('draws nothing while the question is out', () => {
		expect(asked.answer, 'the screen never asked').not.toBeNull();
		expect(form()).toEqual({ fields: 0, text: '' });
	});

	it('goes to setup on a fresh instance, having drawn no form at any point', async () => {
		asked.answer?.(true);
		await settle();
		expect(goto).toHaveBeenCalledWith('/setup');
		expect(form()).toEqual({ fields: 0, text: '' });
	});

	it('draws the form once the answer is that the admin exists', async () => {
		asked.answer?.(false);
		await settle();
		expect(goto).not.toHaveBeenCalled();
		expect(form().fields).toBeGreaterThan(0);
	});

	it('draws the form when the question cannot be asked, so its own failure can speak', async () => {
		asked.refuse?.(new Error('unreachable'));
		await settle();
		expect(form().fields).toBeGreaterThan(0);
	});
});
