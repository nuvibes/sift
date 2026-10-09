// SPDX-License-Identifier: AGPL-3.0-or-later
/* Renaming an artist reaches every song crediting them, through the artist's own route. */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ put: vi.fn(), shown: vi.fn(), changed: vi.fn() }));

vi.mock('$lib/api/client', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/api/client')>()),
	api: { get: vi.fn(), post: vi.fn(), put: mocks.put, del: vi.fn() }
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: mocks.shown } }));
vi.mock('$lib/library/changes.svelte', async (importOriginal) => ({
	...(await importOriginal<typeof import('$lib/library/changes.svelte')>()),
	libraryChanges: { changed: mocks.changed }
}));

import { ApiError } from '$lib/api/client';
import { facetValueLabel } from '$lib/components/shell/facet-labels';
import { artistRename, artistVerbs, RENAME_SAYS } from './artists.svelte';

const ARTIST = { id: 'ar1', name: 'Odo Venn' };

beforeEach(() => {
	vi.clearAllMocks();
	artistRename.open = false;
	artistRename.artist = null;
});

describe('an artist on every song', () => {
	it('offers Rename to an admin and nothing to anybody else', () => {
		expect(artistVerbs(ARTIST, true).map((one) => one.label)).toEqual(['Rename']);
		expect(artistVerbs(ARTIST, false)).toEqual([]);
	});

	it('opens the rename sheet on the artist, with their name typed in', () => {
		artistVerbs(ARTIST, true)[0].run?.([ARTIST.id]);
		expect(artistRename.open).toBe(true);
		expect(artistRename.artist).toEqual(ARTIST);
		expect(artistRename.typed).toBe('Odo Venn');
	});

	it("renames through the artist's own route, names the chip and tells every screen", async () => {
		mocks.put.mockResolvedValue(undefined);
		artistRename.ask(ARTIST);
		artistRename.typed = '  Odo Venne ';
		await artistRename.save();
		expect(mocks.put).toHaveBeenCalledWith('/artists/ar1', { body: { name: 'Odo Venne' } });
		expect(facetValueLabel('artists', 'ar1')).toBe('Odo Venne');
		expect(mocks.changed).toHaveBeenCalled();
	});

	it('sends nothing for a name that has not changed', async () => {
		artistRename.ask(ARTIST);
		await artistRename.save();
		expect(mocks.put).not.toHaveBeenCalled();
	});

	it('says so when no song the viewer can see credits the artist any more', async () => {
		mocks.put.mockRejectedValue(new ApiError(404, 'Not found'));
		artistRename.ask(ARTIST);
		artistRename.typed = 'Ilsa Moor';
		await artistRename.save();
		expect(mocks.shown).toHaveBeenCalledWith('No song you can see credits that artist any more', {
			tone: 'error'
		});
		expect(mocks.changed).not.toHaveBeenCalled();
	});

	it('says what the rename reaches, and that a taken name joins the two', () => {
		expect(RENAME_SAYS).toMatch(/every song/i);
		expect(RENAME_SAYS).toMatch(/joins the two/);
	});
});
