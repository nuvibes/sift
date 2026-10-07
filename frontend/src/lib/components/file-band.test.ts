/* The lists under an open file are asked all together, and each is drawn as it lands: a chain of
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

const { FileBand, PANEL_WAIT_MS } = await import('./file-band.svelte');

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

describe('the panel under a file that is stepped to', () => {
	it('asks nothing until the picture is up, then reads the file on screen', () => {
		asked.length = 0;
		let current = 'f1';
		const band = new FileBand(() => current);
		const after = vi.fn();
		band.step(after);
		expect(asked).toEqual([]);
		expect(band.settled).toBe('');

		band.pictured();
		expect(band.settled).toBe('f1');
		expect(asked).toContain('/assets/f1/people');
		expect(after).toHaveBeenCalledOnce();

		// The next file: the last one's rows go immediately, the new ones wait for its picture.
		current = 'f2';
		asked.length = 0;
		band.step();
		expect(band.settled).toBe('f1');
		expect(band.tags).toEqual([]);
		expect(asked).toEqual([]);
		band.stop();
	});

	it('reads anyway once the wait is over, for a clip that never plays', () => {
		vi.useFakeTimers();
		try {
			asked.length = 0;
			const band = new FileBand(() => 'f3');
			band.step();
			vi.advanceTimersByTime(PANEL_WAIT_MS - 1);
			expect(asked).toEqual([]);
			vi.advanceTimersByTime(1);
			expect(band.settled).toBe('f3');
			expect(asked).toContain('/assets/f3/tags');
		} finally {
			vi.useRealTimers();
		}
	});

	it('reads the same file again immediately, with nothing to wait for', () => {
		asked.length = 0;
		const band = new FileBand(() => 'f4');
		band.pictured();
		asked.length = 0;
		const after = vi.fn();
		band.step(after);
		expect(asked).toContain('/assets/f4/people');
		expect(after).toHaveBeenCalledOnce();
	});
});
