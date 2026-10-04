import { describe, expect, it, vi } from 'vitest';
import {
	folderFor,
	placeIn,
	type FolderAt,
	type FolderSteps,
	type RootAt
} from './download-folder';

/* Where a folder somebody chose for downloads sits in the library. The default downloads folder is
   a library folder by id, so a chosen place is one of three things, and each needs a different
   act before it can be the default. */

const ROOTS: RootAt[] = [{ id: 'r1', path: 'D:\\Media' }];
const FOLDERS: FolderAt[] = [
	{ id: 'f1', root_id: 'r1', parent_id: null, name: 'Media' },
	{ id: 'f2', root_id: 'r1', parent_id: 'f1', name: 'Clips' }
];

describe('where a chosen folder sits', () => {
	it('is a library folder already, whatever the case and the separators', () => {
		expect(placeIn('D:\\Media\\Clips', ROOTS, FOLDERS)).toEqual({ kind: 'folder', id: 'f2' });
		expect(placeIn('d:/media/clips/', ROOTS, FOLDERS)).toEqual({ kind: 'folder', id: 'f2' });
		expect(placeIn('D:\\Media', ROOTS, FOLDERS)).toEqual({ kind: 'folder', id: 'f1' });
	});

	it('is inside a library, below folders that are not there yet', () => {
		expect(placeIn('D:\\Media\\Clips\\New\\Deeper', ROOTS, FOLDERS)).toEqual({
			kind: 'inside',
			parentId: 'f2',
			make: ['New', 'Deeper']
		});
	});

	it('is outside every library, including one whose name it only starts with', () => {
		expect(placeIn('D:\\Downloads', ROOTS, FOLDERS)).toEqual({ kind: 'outside' });
		expect(placeIn('D:\\MediaOther', ROOTS, FOLDERS)).toEqual({ kind: 'outside' });
	});

	it('keeps the case of a path that is not a Windows one', () => {
		const roots = [{ id: 'r2', path: '/srv/media' }];
		const folders = [{ id: 'g1', root_id: 'r2', parent_id: null, name: 'media' }];
		expect(placeIn('/srv/media', roots, folders)).toEqual({ kind: 'folder', id: 'g1' });
		expect(placeIn('/srv/Media', roots, folders)).toEqual({ kind: 'outside' });
	});
});

function steps(overrides: Partial<FolderSteps> = {}): FolderSteps {
	return {
		roots: vi.fn(async () => ROOTS),
		folders: vi.fn(async () => FOLDERS),
		grant: vi.fn(async () => undefined),
		addRoot: vi.fn(async () => undefined),
		makeFolder: vi.fn(async (parentId: string, name: string) => ({
			id: `${parentId}/${name}`,
			root_id: 'r1',
			parent_id: parentId,
			name
		})),
		...overrides
	};
}

describe('making it a library folder', () => {
	it('uses a library folder as it is, and makes nothing', async () => {
		const io = steps();
		expect(await folderFor('D:\\Media\\Clips', io)).toBe('f2');
		expect(io.makeFolder).not.toHaveBeenCalled();
		expect(io.addRoot).not.toHaveBeenCalled();
	});

	it('makes the missing folders one level at a time', async () => {
		const io = steps();
		expect(await folderFor('D:\\Media\\Clips\\New\\Deeper', io)).toBe('f2/New/Deeper');
		expect(io.makeFolder).toHaveBeenNthCalledWith(1, 'f2', 'New');
		expect(io.makeFolder).toHaveBeenNthCalledWith(2, 'f2/New', 'Deeper');
	});

	it('hands over and adds a folder outside every library, then uses its top', async () => {
		let added = false;
		const io = steps({
			roots: vi.fn(async () => (added ? [...ROOTS, { id: 'r9', path: 'E:\\Grabs' }] : ROOTS)),
			folders: vi.fn(async () =>
				added ? [...FOLDERS, { id: 'f9', root_id: 'r9', parent_id: null, name: 'Grabs' }] : FOLDERS
			),
			addRoot: vi.fn(async () => {
				added = true;
			})
		});
		expect(await folderFor('E:\\Grabs', io)).toBe('f9');
		expect(io.grant).toHaveBeenCalledWith('E:\\Grabs');
		expect(io.addRoot).toHaveBeenCalledWith('E:\\Grabs');
	});

	it("passes the server's refusal on as it came", async () => {
		const io = steps({
			addRoot: vi.fn(async () => {
				throw new Error('That folder overlaps a library that is already here.');
			})
		});
		await expect(folderFor('D:\\', io)).rejects.toThrow('overlaps a library');
	});
});
