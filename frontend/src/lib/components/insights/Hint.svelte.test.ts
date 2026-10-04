/* A hint is shown once: drawn, the account told at that moment, and never drawn again, not on a
 * second screen this session, not once the account says it was seen, and not on a guess when the
 * account's answer could not be read. */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import Hint from './Hint.svelte';
import { HINT_WORDS, forgetPathSession } from './your-path';

const mocks = vi.hoisted(() => ({ post: vi.fn(), state: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	return { ...real, api: { ...(real.api as object), post: mocks.post } };
});
vi.mock('$lib/shell/interface-state.svelte', async (importOriginal) => {
	const real = await importOriginal<Record<string, unknown>>();
	return { ...real, interfaceState: mocks.state };
});

const hosts: HTMLElement[] = [];
const drawn: ReturnType<typeof mount>[] = [];

async function render(name: 'organize_empty' | 'first_pile' | 'first_insights') {
	const host = document.createElement('div');
	document.body.append(host);
	hosts.push(host);
	drawn.push(mount(Hint, { target: host, props: { name } }));
	for (let i = 0; i < 6; i += 1) {
		flushSync();
		await Promise.resolve();
	}
	flushSync();
	return host;
}

beforeEach(() => {
	forgetPathSession();
	mocks.post.mockReset().mockResolvedValue(undefined);
	mocks.state.mockReset().mockResolvedValue({});
});

afterEach(() => {
	for (const one of drawn.splice(0)) unmount(one);
	for (const host of hosts.splice(0)) host.remove();
});

describe('A hint', () => {
	it('is drawn the first time, and the account is told at once', async () => {
		const host = await render('first_pile');
		expect(host.textContent).toContain(HINT_WORDS.first_pile);
		expect(mocks.post).toHaveBeenCalledWith('/insights/path/hints/first_pile/seen');
	});

	it('is not drawn a second time in the same session', async () => {
		await render('organize_empty');
		const again = await render('organize_empty');
		expect(again.textContent).not.toContain(HINT_WORDS.organize_empty);
		expect(mocks.post).toHaveBeenCalledTimes(1);
	});

	it('is drawn once when two screens ask for it while the read is in flight', async () => {
		const first = document.createElement('div');
		const second = document.createElement('div');
		document.body.append(first, second);
		hosts.push(first, second);
		drawn.push(mount(Hint, { target: first, props: { name: 'first_pile' } }));
		drawn.push(mount(Hint, { target: second, props: { name: 'first_pile' } }));
		for (let i = 0; i < 6; i += 1) {
			flushSync();
			await Promise.resolve();
		}
		flushSync();
		const shown = [first, second].filter((one) => one.textContent?.includes(HINT_WORDS.first_pile));
		expect(shown).toHaveLength(1);
		expect(mocks.post).toHaveBeenCalledTimes(1);
	});

	it('is not drawn once the account says it was seen', async () => {
		mocks.state.mockResolvedValue({ 'path.hint.first_insights.seen': 'seen' });
		const host = await render('first_insights');
		expect(host.textContent).not.toContain(HINT_WORDS.first_insights);
		expect(mocks.post).not.toHaveBeenCalled();
	});

	it('is not drawn when the account could not be read', async () => {
		mocks.state.mockRejectedValue(new Error('unread'));
		const host = await render('first_pile');
		expect(host.textContent).not.toContain(HINT_WORDS.first_pile);
		expect(mocks.post).not.toHaveBeenCalled();
	});

	it('goes when Close is pressed', async () => {
		const host = await render('first_insights');
		host.querySelector('button')!.click();
		flushSync();
		expect(host.textContent).not.toContain(HINT_WORDS.first_insights);
	});
});
