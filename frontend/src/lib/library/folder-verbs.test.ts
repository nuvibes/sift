/* A folder's who-sees-it rows: one declaration, drawn by every folder menu. */

import { readFileSync } from 'node:fs';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { menuGroups } from '$lib/components/common/verbs';
import { folderMarks, folderVerbs } from './folder-verbs';

const writes = vi.hoisted(() => ({
	keptLocal: vi.fn(async () => ({})),
	keptFromSwaps: vi.fn(async () => ({}))
}));
vi.mock('$lib/entity/enrichment.svelte', () => ({ setKeptLocal: writes.keptLocal }));
vi.mock('$lib/components/swap/swap', () => ({ setKeptFromSwaps: writes.keptFromSwaps }));

function words(verbs: { label: string }[]): string[] {
	return verbs.map((verb) => verb.label);
}

describe('folderVerbs', () => {
	it('offers Hide, Share and Visibility to an admin, alphabetically, in the who-sees-it part', () => {
		const verbs = folderVerbs({
			isAdmin: true,
			handlers: { hide: () => {}, share: () => {}, visibility: () => {} }
		});
		expect(words(verbs)).toEqual(['Hide', 'Share', 'Visibility']);
		expect(verbs.every((verb) => verb.group === 'share')).toBe(true);
		const visibility = verbs.find((verb) => verb.id === 'visibility')!;
		expect(visibility.icon).toBe('policy');
		expect(visibility.singleOnly).toBe(true);
	});

	it('offers the way back out of a hidden folder, after Share', () => {
		const hide = vi.fn();
		const verbs = folderVerbs({
			isAdmin: true,
			hidden: true,
			handlers: { hide, share: () => {}, visibility: () => {} }
		});
		expect(words(verbs)).toEqual(['Share', 'Unhide', 'Visibility']);
		verbs.find((verb) => verb.label === 'Unhide')!.run?.([]);
		expect(hide).toHaveBeenCalledWith(false);
	});

	it('opens the report on a press of Visibility', () => {
		const visibility = vi.fn();
		const verbs = folderVerbs({ isAdmin: true, handlers: { visibility } });
		verbs.find((verb) => verb.id === 'visibility')!.run?.(['f1']);
		expect(visibility).toHaveBeenCalledOnce();
	});

	it('keeps Share and Visibility from a guest, and offers nothing it was handed no handler for', () => {
		const guest = folderVerbs({
			isAdmin: false,
			handlers: { hide: () => {}, share: () => {}, visibility: () => {} }
		});
		expect(words(guest)).toEqual(['Hide']);
		expect(folderVerbs({ isAdmin: true, handlers: {} })).toEqual([]);
	});

	it('calls Share what the row asks for', () => {
		const verbs = folderVerbs({
			isAdmin: true,
			shareLabel: 'Sharing',
			handlers: { share: () => {} }
		});
		expect(words(verbs)).toEqual(['Sharing']);
	});
});

describe("a folder's Don't enrich and Don't swap", () => {
	beforeEach(() => {
		writes.keptLocal.mockClear();
		writes.keptFromSwaps.mockClear();
	});

	it("offers both to an admin in their own part, before who sees it, in the entity pages' words", () => {
		const verbs = folderVerbs({
			isAdmin: true,
			handlers: { share: () => {}, keepLocal: () => {}, keepFromSwaps: () => {} }
		});
		const parts = menuGroups(verbs).map(words);
		expect(parts).toEqual([["Don't enrich", "Don't swap"], ['Share']]);
		expect(verbs.find((verb) => verb.id === 'keep-local')!.icon).toBe('shield');
		expect(verbs.find((verb) => verb.id === 'keep-from-swaps')!.icon).toBe('do_not_disturb_on');
	});

	it('offers the way back on a folder that carries them, and keeps both from a guest', () => {
		const verbs = folderVerbs({
			isAdmin: true,
			keptLocal: true,
			keptFromSwaps: true,
			handlers: { keepLocal: () => {}, keepFromSwaps: () => {} }
		});
		expect(words(verbs)).toEqual(['Allow enrichment', 'Allow swapping']);
		const guest = folderVerbs({
			isAdmin: false,
			handlers: { keepLocal: () => {}, keepFromSwaps: () => {} }
		});
		expect(guest).toEqual([]);
	});

	it("writes each mark through the folder's own door with what it should become", async () => {
		const after = vi.fn();
		const marks = folderMarks({ id: 'f-beach', keep_local: false, keep_from_swaps: true }, after);
		const verbs = folderVerbs({
			isAdmin: true,
			keptLocal: marks.keptLocal,
			keptFromSwaps: marks.keptFromSwaps,
			handlers: { keepLocal: marks.keepLocal, keepFromSwaps: marks.keepFromSwaps }
		});
		verbs.find((verb) => verb.id === 'keep-local')!.run?.([]);
		verbs.find((verb) => verb.id === 'keep-from-swaps')!.run?.([]);
		await vi.waitFor(() => expect(after).toHaveBeenCalledTimes(2));
		expect(writes.keptLocal).toHaveBeenCalledWith('folder', 'f-beach', true);
		expect(writes.keptFromSwaps).toHaveBeenCalledWith('folder', 'f-beach', false);
	});
});

describe('every folder menu draws the rows from the one declaration', () => {
	const MENUS = ['FolderExplorer.svelte', 'LibraryScreen.svelte', 'FolderGround.svelte'];

	for (const file of MENUS) {
		it(`${file} builds its folder rows with folderVerbs and writes none of its own`, () => {
			const source = readFileSync(`src/lib/library/${file}`, 'utf8');
			expect(source).toMatch(/folder(Verbs|MenuParts)\(/);
			expect(source).not.toMatch(
				/label="(Don't enrich|Don't swap|Allow enrichment|Allow swapping)"/
			);
			expect(source).not.toMatch(/label="(Hide|Unhide|Share|Visibility)"/);
		});
	}
});
