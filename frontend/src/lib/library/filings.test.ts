// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * What a filing is called, over the four shapes one can arrive in.
 *
 * Three of the four are cases the screen cannot conveniently produce and a person will still meet:
 * a drop names nobody, a download names a username, and deleting a site leaves the usernames on it
 * still holding files. The label is the only thing standing between those and a chip with nothing
 * in it, which is a chip nobody presses, on the one row whose whole purpose is to be pressed.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';
import {
	filingLabel,
	filingName,
	filingRemovalLabel,
	filingsOf,
	removeFiling,
	type Filing
} from '$lib/library/filings';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

beforeEach(() => {
	vi.clearAllMocks();
});

function filing(overrides: Partial<Filing> = {}): Filing {
	return {
		username_id: 'acc-1',
		site_id: 'plat-1',
		site: 'OnlyFans',
		username: null,
		source_name: null,
		person_id: null,
		// How the filing was decided. Null is somebody doing it, which is the ordinary case; the one
		// word the server writes today is `stash_box`.
		source: null,
		// What names the site's cover on its chip's address; none chosen and no logo here.
		art: null,
		cover_asset_id: null,
		cover_upload_id: null,
		cover_at_ms: null,
		cover_frame: null,
		icon: null,
		...overrides
	};
}

describe('filingLabel', () => {
	it('names the poster before the site, because that is what the row answers', () => {
		expect(filingLabel(filing({ username: '@harlowquin' }))).toBe('@harlowquin on OnlyFans');
	});

	it('is the site alone where the filing names nobody', () => {
		// What a drop onto a Site card writes. The empty username never reaches the wire.
		expect(filingLabel(filing())).toBe('OnlyFans');
	});

	it('says the site was deleted, and still names the username that posted it', () => {
		expect(filingLabel(filing({ site_id: null, site: null, username: '@harlowquin' }))).toBe(
			'@harlowquin, on a Site that was deleted'
		);
	});

	it('says the site was deleted where there is nothing else left to say', () => {
		expect(filingLabel(filing({ site_id: null, site: null }))).toBe('a Site that was deleted');
	});
});

/*
 * The chip's own word, which is a different question from the sentence above.
 *
 * The chip stands in a row of SITES under the Sites glyph, so the kind is already said and
 * the site's name is the one word somebody is scanning for. What the removal control promises has
 * not changed, and neither has what an orphaned filing says. Both are asserted here beside it, so
 * a change to one cannot quietly be read as a change to the other.
 */
describe('filingName', () => {
	it('is the site alone, even where the filing names a username', () => {
		expect(filingName(filing({ username: '@harlowquin' }))).toBe('OnlyFans');
	});

	it('is the site alone where the filing names nobody, which it already was', () => {
		expect(filingName(filing())).toBe('OnlyFans');
	});

	it('falls back to the whole sentence where there is no site left to name', () => {
		// Nothing to shorten to: the site is gone, so the username is all that tells one orphaned
		// filing from another and the sentence is the only thing worth putting on the chip.
		expect(filingName(filing({ site_id: null, site: null, username: '@harlowquin' }))).toBe(
			'@harlowquin, on a Site that was deleted'
		);
		expect(filingName(filing({ site_id: null, site: null }))).toBe('a Site that was deleted');
	});
});

describe('filingRemovalLabel', () => {
	it('names THIS FILE, so a cross on a page of destructive verbs says which it is not', () => {
		expect(filingRemovalLabel(filing({ username: '@harlowquin' }))).toBe(
			'Remove this file from @harlowquin on OnlyFans'
		);
	});

	it('carries every shape the label has rather than a second copy of the rules', () => {
		expect(filingRemovalLabel(filing({ site_id: null, site: null }))).toBe(
			'Remove this file from a Site that was deleted'
		);
	});
});

/*
 * The two addresses, asserted.
 *
 * A request typed one character wrong is a 404 the screen swallows: `AssetView` leaves the row
 * empty on a failure, deliberately, so the whole feature would simply not appear and nothing would
 * say why. The names are the server's own, and these are the only place the client writes them.
 */
describe('the two addresses', () => {
	it("reads a file's filings from the file", async () => {
		mocked.get.mockResolvedValue([]);

		await filingsOf('a1');

		expect(mocked.get).toHaveBeenCalledWith('/assets/a1/filings');
	});

	it('removes one by naming the file and the USERNAME, never the site', async () => {
		mocked.del.mockResolvedValue(undefined);

		await removeFiling('a1', 'acc-2');

		expect(mocked.del).toHaveBeenCalledWith('/assets/a1/filings/acc-2');
	});
});
