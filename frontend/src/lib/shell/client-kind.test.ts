// SPDX-License-Identifier: AGPL-3.0-or-later
import { afterEach, describe, expect, it, vi } from 'vitest';

/* Each case imports the module afresh, because what is under test is what it answers BEFORE the
   layout has said anything, and the first `setClientKind` in a shared module would hide that. */
async function fresh() {
	vi.resetModules();
	return import('./client-kind');
}

function widthIs(phone: boolean) {
	vi.stubGlobal(
		'matchMedia',
		(media: string) =>
			({
				matches: media.includes('max-width') ? phone : media.includes('any-pointer: fine'),
				addEventListener: () => {}
			}) as unknown as MediaQueryList
	);
}

afterEach(() => {
	vi.unstubAllGlobals();
	delete (window as { sift?: unknown }).sift;
});

describe('the kind a page says before its layout has', () => {
	it('is a phone at a phone width', async () => {
		// The first request, who is signed in, stamps the session of a browser signed in before
		// devices were kept; it must not say "computer" from a phone.
		widthIs(true);
		const { clientKind } = await fresh();

		expect(clientKind()).toBe('phone');
	});

	it('is the app inside the desktop shell, whatever its width', async () => {
		widthIs(true);
		(window as { sift?: unknown }).sift = { isDesktop: true };
		const { clientKind } = await fresh();

		expect(clientKind()).toBe('app');
	});

	it('is a computer in a wide window', async () => {
		widthIs(false);
		const { clientKind } = await fresh();

		expect(clientKind()).toBe('computer');
	});

	it('is whatever the layout says once it has said it', async () => {
		widthIs(true);
		const { clientKind, setClientKind } = await fresh();

		setClientKind('remote');

		expect(clientKind()).toBe('remote');
	});
});
