/* The words a wall is searched by, kept in its address. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$app/navigation', () => ({ goto: vi.fn(async () => undefined) }));

const { goto } = await import('$app/navigation');
const { WallWords, addressWith, emptyWallSays, wordsIn } = await import('./wall-words');

const at = (where: string) => new URL(`http://x${where}`);

beforeEach(() => vi.mocked(goto).mockClear());

describe('the words in a wall address', () => {
	it('reads them trimmed, and nothing when there are none', () => {
		expect(wordsIn(at('/sites?q=%20wren%20'))).toBe('wren');
		expect(wordsIn(at('/sites'))).toBe('');
	});

	it('writes new words and drops the position the old ones had', () => {
		expect(addressWith(at('/sites?parent=n1&from=01ABC&near=12&offset=24'), 'wren ')).toBe(
			'/sites?parent=n1&q=wren'
		);
	});

	it('takes them off when the box is emptied, and writes nothing when nothing changed', () => {
		expect(addressWith(at('/tags?q=wren'), '  ')).toBe('/tags');
		expect(addressWith(at('/tags?q=wren'), 'wren')).toBeNull();
	});
});

describe('a box writing its words', () => {
	it('is not handed its own write back, and is handed anybody else s', () => {
		const words = new WallWords();
		expect(words.write(at('/people'), 'wren')).toBe(true);
		expect(vi.mocked(goto)).toHaveBeenCalledTimes(1);

		expect(words.echoed('wren'), 'its own words came back as somebody else s').toBe(true);
		/* The chip's cross, Back, or a link: the box shows them. */
		expect(words.echoed('')).toBe(false);
	});

	it('writes nothing when the address already says it', () => {
		const words = new WallWords();
		expect(words.write(at('/people?q=wren'), 'wren')).toBe(false);
		expect(vi.mocked(goto)).not.toHaveBeenCalled();
	});
});

describe('a wall whose words go under a name of their own', () => {
	it('reads and writes that name, and leaves the query language alone', () => {
		expect(wordsIn(at('/loops?q=beach&called=dusk'), 'called')).toBe('dusk');
		expect(addressWith(at('/loops?q=beach'), 'dusk', 'called')).toBe('/loops?q=beach&called=dusk');
		const words = new WallWords('called');
		expect(words.write(at('/loops?q=beach'), 'dusk')).toBe(true);
		expect(vi.mocked(goto).mock.calls.at(-1)?.[0]).toBe('/loops?q=beach&called=dusk');
	});
});

describe('an empty wall', () => {
	it('names the words it was searched by, then the filters, before its first-run sentence', () => {
		const first = 'No Sites yet.';
		expect(emptyWallSays('Sites', 'zzq', true, first)).toBe('No Sites match "zzq".');
		expect(emptyWallSays('Sites', '', true, first)).toBe('No Sites match these filters.');
		expect(emptyWallSays('Sites', '', false, first)).toBe(first);
	});
});
