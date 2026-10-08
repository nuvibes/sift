import { describe, expect, it, vi } from 'vitest';
import { LiveFeed } from './live.svelte';
import { libraryChanges } from '$lib/library/changes.svelte';
import type { components } from '$lib/api/schema';

vi.mock('$lib/api/client', async (original) => ({
	...((await original()) as Record<string, unknown>),
	api: { get: async () => ({ about: [], marker: '5', opinions: [], more_opinions: false }) }
}));

type LiveState = components['schemas']['LiveState'];

/* A subject is one of the feed's own: a name every object inherits is not one, and calling what
   it names would take the connection down as surely as an unknown name would. */
describe('a subject named like something every object has', () => {
	it.each(['__proto__', 'constructor', 'toString', 'hasOwnProperty'])(
		'is passed over: %s',
		(name) => {
			const before = libraryChanges.generation;
			const about = [name, 'library'] as LiveState['about'];

			expect(() =>
				new LiveFeed().apply({
					about,
					marker: '7',
					opinions: [],
					more_opinions: false,
					commands: []
				})
			).not.toThrow();

			expect(libraryChanges.generation).toBe(before + 1);
		}
	);
});
