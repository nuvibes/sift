/* The address a username's picture is asked at: by its Site, and only where a picture is kept. */
import { describe, expect, it, vi } from 'vitest';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(async () => ({ usernames: ['NeveAlder'] })) }
}));

const { creatorArt, usernameArt } = await import('./creator-art.svelte');

describe("a username's picture", () => {
	it('is asked by the Site and the page, and only for a name that has one', async () => {
		await creatorArt.load();

		expect(
			usernameArt({
				username: 'nevealder',
				site_name: 'OnlyFans',
				url: 'https://onlyfans.com/nevealder'
			})
		).toBe(
			'/api/creator-art/nevealder?site=OnlyFans&address=https%3A%2F%2Fonlyfans.com%2Fnevealder'
		);
		expect(usernameArt({ username: 'nevealder', site_name: null })).toBeNull();
		expect(usernameArt({ username: 'someoneelse', site_name: 'OnlyFans' })).toBeNull();
	});
});
