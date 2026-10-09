import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import {
	Updates,
	describeLastChecked,
	inlineRuns,
	notesBlocks,
	type UpdateState
} from './updates.svelte';

/* What the client does with the server's answer, including the answer that says nothing is known,
 * which is the one an installation with no outbound network gets every time.
 */

const fetchMock = vi.fn();

function answer(body: unknown, ok = true, status = 200) {
	return {
		ok,
		status,
		headers: { get: () => 'application/json' },
		json: async () => body,
		text: async () => JSON.stringify(body)
	};
}

const AVAILABLE: UpdateState = {
	current_version: '1.4.0',
	latest_version: '1.5.0',
	update_available: true,
	notes: 'Faster thumbnails.',
	release_page: 'https://releases.example/sift/tag/v1.5.0',
	last_checked: 1_700_000_000,
	dismissed: false
};

const NOTHING_KNOWN: UpdateState = {
	current_version: '1.4.0',
	latest_version: null,
	update_available: false,
	notes: '',
	release_page: '',
	last_checked: 0,
	dismissed: false
};

beforeEach(() => {
	vi.stubGlobal('fetch', fetchMock);
	vi.stubGlobal('window', { location: { origin: 'http://sift.test' } });
	fetchMock.mockReset();
});

afterEach(() => vi.unstubAllGlobals());

describe('reading the update state', () => {
	it('notifies when an update is available and not dismissed', async () => {
		fetchMock.mockResolvedValueOnce(answer(AVAILABLE));
		const updates = new Updates();

		await updates.load();

		expect(updates.loaded).toBe(true);
		expect(updates.shouldNotify).toBe(true);
		expect(updates.state?.latest_version).toBe('1.5.0');
	});

	it('carries the release page through exactly as the server sent it', async () => {
		fetchMock.mockResolvedValueOnce(answer(AVAILABLE));
		const updates = new Updates();

		await updates.load();

		expect(updates.state?.release_page).toBe('https://releases.example/sift/tag/v1.5.0');
	});

	it('does not notify when nothing is known', async () => {
		fetchMock.mockResolvedValueOnce(answer(NOTHING_KNOWN));
		const updates = new Updates();

		await updates.load();

		expect(updates.loaded).toBe(true);
		expect(updates.shouldNotify).toBe(false);
	});

	it('does not notify about a version already dismissed', async () => {
		fetchMock.mockResolvedValueOnce(answer({ ...AVAILABLE, dismissed: true }));
		const updates = new Updates();

		await updates.load();

		expect(updates.shouldNotify).toBe(false);
		// Still available, though. Dismissed hides the banner, not the fact.
		expect(updates.state?.update_available).toBe(true);
	});

	it('a check that could not happen is not an error on the screen', async () => {
		fetchMock.mockRejectedValueOnce(new TypeError('network down'));
		const updates = new Updates();

		await updates.load();

		expect(updates.shouldNotify).toBe(false);
		expect(updates.state).toBeNull();
	});
});

describe('a copy behind its library', () => {
	/* The server's verdict is its own computer against the feed: a client copy two releases behind
	   a current library reads nothing from it, so the copy's lag is carried beside it. */
	it('is waiting for an update the server does not need', async () => {
		fetchMock.mockResolvedValueOnce(
			answer({ ...AVAILABLE, current_version: '1.5.0', update_available: false })
		);
		vi.stubGlobal('window', {
			location: { origin: 'http://sift.test' },
			sift: { shellVersion: async () => '1.4.0' }
		});
		const updates = new Updates();

		await updates.load();
		await updates.readHere();

		expect(updates.here).toBe('1.4.0');
		expect(updates.behindHere).toBe(true);
		expect(updates.waiting).toBe(true);
		expect(updates.shouldNotify, 'the banner speaks for this copy too').toBe(true);
		updates.here = '1.5.0';
		expect(updates.waiting, 'this copy is current, and so is the server').toBe(false);
		updates.here = null;
		expect(updates.behindHere, 'one computer: the server says').toBe(false);
	});
});

describe('dismissing', () => {
	it('sends the version being hidden and stops notifying', async () => {
		fetchMock.mockResolvedValueOnce(answer(AVAILABLE));
		const updates = new Updates();
		await updates.load();

		fetchMock.mockResolvedValueOnce({ ok: true, status: 204, headers: { get: () => null } });
		await updates.dismiss();

		const [, init] = fetchMock.mock.calls[1];
		expect(JSON.parse(init.body)).toEqual({ version: '1.5.0' });
		expect(updates.shouldNotify).toBe(false);
	});

	it('does nothing when there is no version to dismiss', async () => {
		fetchMock.mockResolvedValueOnce(answer(NOTHING_KNOWN));
		const updates = new Updates();
		await updates.load();

		await updates.dismiss();

		expect(fetchMock).toHaveBeenCalledTimes(1);
	});

	it('a dismissal that did not save leaves the notice showing', async () => {
		fetchMock.mockResolvedValueOnce(answer(AVAILABLE));
		const updates = new Updates();
		await updates.load();

		fetchMock.mockRejectedValueOnce(new TypeError('network down'));
		await updates.dismiss();

		// The safe direction: a notice that comes back beats one silently lost.
		expect(updates.shouldNotify).toBe(true);
	});
});

describe('when the check last ran', () => {
	const now = 1_700_000_000_000;

	it('says nothing when it never has', () => {
		expect(describeLastChecked(0, now)).toBe('');
	});

	it('reads at the scale a person thinks in', () => {
		expect(describeLastChecked(1_700_000_000, now)).toBe('just now');
		expect(describeLastChecked(1_700_000_000 - 600, now)).toBe('10 minutes ago');
		expect(describeLastChecked(1_700_000_000 - 3600, now)).toBe('an hour ago');
		expect(describeLastChecked(1_700_000_000 - 3600 * 5, now)).toBe('5 hours ago');
		expect(describeLastChecked(1_700_000_000 - 86400, now)).toBe('yesterday');
		expect(describeLastChecked(1_700_000_000 - 86400 * 3, now)).toBe('3 days ago');
	});

	it('does not read as being in the future when clocks disagree', () => {
		expect(describeLastChecked(1_700_000_000 + 5000, now)).toBe('just now');
	});
});

/* The notes are published as Markdown and drawn from a small subset of it, as plain strings: nothing
   in a release body can become markup, and the only link kept is to the release's own page. */
describe('the release notes', () => {
	const PAGE = 'https://releases.example/sift/tag/v1.5.0';

	it('reads headings, paragraphs, lists and code', () => {
		const notes = [
			'## What changed for you',
			'',
			'Faster **thumbnails** and a `--quiet` flag.',
			'Second line of the same paragraph.',
			'',
			'- one',
			'- two',
			'  carried on',
			'',
			'1. first',
			'2. second',
			'',
			'```',
			'<b>not bold</b>',
			'```'
		].join('\n');

		expect(notesBlocks(notes, PAGE)).toEqual([
			{ kind: 'heading', level: 2, inline: [{ kind: 'text', text: 'What changed for you' }] },
			{
				kind: 'paragraph',
				inline: [
					{ kind: 'text', text: 'Faster ' },
					{ kind: 'strong', text: 'thumbnails' },
					{ kind: 'text', text: ' and a ' },
					{ kind: 'code', text: '--quiet' },
					{ kind: 'text', text: ' flag. Second line of the same paragraph.' }
				]
			},
			{
				kind: 'list',
				ordered: false,
				items: [[{ kind: 'text', text: 'one' }], [{ kind: 'text', text: 'two carried on' }]]
			},
			{
				kind: 'list',
				ordered: true,
				items: [[{ kind: 'text', text: 'first' }], [{ kind: 'text', text: 'second' }]]
			},
			{ kind: 'code', text: '<b>not bold</b>' }
		]);
	});

	it('keeps a link to the release page and nothing else', () => {
		expect(inlineRuns(`[the notes](${PAGE}) and [a trap](https://evil.example/x)`, PAGE)).toEqual([
			{ kind: 'link', text: 'the notes', href: PAGE },
			{ kind: 'text', text: ' and a trap' }
		]);
	});

	it('keeps no link at all when there is no release page', () => {
		expect(inlineRuns(`[the notes](${PAGE})`, '')).toEqual([{ kind: 'text', text: 'the notes' }]);
	});

	it('draws an image as its words and fetches nothing', () => {
		expect(inlineRuns(`![a tracker](${PAGE})`, PAGE)).toEqual([
			{ kind: 'text', text: 'a tracker' }
		]);
	});

	/* HTML is text. The blocks are strings the screen puts into text nodes, so a tag in the notes
	   is shown as the characters it is. */
	it('keeps HTML as the characters it is', () => {
		expect(notesBlocks('<img src=x onerror=steal(1)> <script>x</script>', PAGE)).toEqual([
			{
				kind: 'paragraph',
				inline: [{ kind: 'text', text: '<img src=x onerror=steal(1)> <script>x</script>' }]
			}
		]);
	});

	it('leaves a marker with no partner as text', () => {
		expect(inlineRuns('2 ** 3 and a lone ` tick', PAGE)).toEqual([
			{ kind: 'text', text: '2 ** 3 and a lone ` tick' }
		]);
	});

	/* A release body is not signed. Text built to make a naive reader search the rest of the line
	   for every marker stays a single pass. */
	it('reads a body full of unclosed markers quickly', () => {
		const hostile = '** [x]( ` '.repeat(2000);
		const started = performance.now();
		notesBlocks(hostile, PAGE);
		expect(performance.now() - started).toBeLessThan(500);
	});
});
