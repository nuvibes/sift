/* The lists under an open file are asked all at once, and each is drawn as it lands: a chain of
 * four waits put a tag added elsewhere over a second behind. */
import { describe, expect, it, vi } from 'vitest';

const gates = vi.hoisted(() => new Map<string, () => void>());
const asked = vi.hoisted(() => [] as string[]);

vi.mock('$lib/api/client', () => ({
	api: {
		get: vi.fn((path: string) => {
			asked.push(path);
			const answer = path.endsWith('/tags')
				? [{ id: 't1', name: 'Dusk' }]
				: path.endsWith('/people') || path.endsWith('/filings')
					? []
					: { items: [] };
			return new Promise((resolve) => gates.set(path, () => resolve(answer)));
		})
	}
}));
vi.mock('$lib/library/filings', () => ({
	filingsOf: vi.fn(() => {
		asked.push('filings');
		return new Promise((resolve) => gates.set('filings', () => resolve([])));
	}),
	removeFiling: vi.fn()
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: vi.fn() } }));

const { FileBand } = await import('./file-band.svelte');

describe("an open file's lists", () => {
	it('are all asked before any has answered, and the tags drawn while the people wait', async () => {
		const band = new FileBand(() => 'f1');
		const loading = band.load('f1');
		await Promise.resolve();

		expect(asked).toEqual(
			expect.arrayContaining([
				'/assets/f1/people',
				'filings',
				'/collections',
				'/photo-sets',
				'/songs',
				'/assets/f1/tags'
			])
		);

		gates.get('/assets/f1/tags')?.();
		await vi.waitFor(() => expect(band.tags.map((one) => one.name)).toEqual(['Dusk']));
		expect(band.bandFor, 'said done before the people answered').toBeNull();

		for (const open of gates.values()) open();
		await loading;
		expect(band.bandFor).toBe('f1');
	});

	it('writes nothing when a re-read finds the same lists', async () => {
		gates.clear();
		const band = new FileBand(() => 'f1');
		const first = band.load('f1');
		await Promise.resolve();
		for (const open of gates.values()) open();
		await first;
		const drawn = band.tags;

		gates.clear();
		const again = band.load('f1');
		await Promise.resolve();
		for (const open of gates.values()) open();
		await again;

		expect(band.tags, 'the same tags were written again').toBe(drawn);
	});
});
