/* Whether this window is the Sift app in client mode, as the desktop shell answers it. */

import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(() => {
	delete (window as { sift?: unknown }).sift;
	vi.resetModules();
});

async function fresh() {
	return (await import('./sift-elsewhere.svelte')).siftElsewhere;
}

describe('the device running Sift', () => {
	it('is another computer in the app in client mode, which names the one it is on', async () => {
		(window as { sift?: unknown }).sift = {
			isDesktop: true,
			localHardware: async () => ({ cpu_model: 'A test processor' })
		};
		const elsewhere = await fresh();
		expect(elsewhere.yes).toBe(false);
		await vi.waitFor(() => expect(elsewhere.yes).toBe(true));
	});

	it('is this one in a browser and in the app running Sift, which name no second computer', async () => {
		for (const shell of [undefined, { isDesktop: true, localHardware: async () => null }]) {
			(window as { sift?: unknown }).sift = shell;
			const elsewhere = await fresh();
			expect(elsewhere.yes).toBe(false);
			await Promise.resolve();
			await Promise.resolve();
			expect(elsewhere.yes).toBe(false);
			vi.resetModules();
		}
	});
});
