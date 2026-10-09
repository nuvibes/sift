import { beforeEach, describe, expect, it, vi } from 'vitest';

import { api } from '$lib/api/client';

import {
	GENERATE_KEY,
	GENERATE_KEYS,
	IDENTIFY_KEY,
	IDENTIFY_KEYS,
	SCAN_KEYS,
	fetchBuildSheet,
	fetchFolderAnswers,
	setFolderAnswers,
	startBuild
} from './importing';

vi.mock('$lib/api/client', () => ({
	api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn() }
}));

const mocked = vi.mocked(api);

beforeEach(() => {
	vi.clearAllMocks();
});

/* The Importing screen's own request layer. */

describe('the Importing screen asks the server', () => {
	it('for every folder and what it answers differently', async () => {
		mocked.get.mockResolvedValue({ folders: [], keys: [] });

		await fetchFolderAnswers();

		expect(mocked.get).toHaveBeenCalledWith('/importing/folders');
	});

	it('to answer for one folder, by its id, with the answers in a body', async () => {
		mocked.put.mockResolvedValue({ root_id: 'r1', name: 'x', answers: {} });

		await setFolderAnswers('r1', { [GENERATE_KEY]: false });

		expect(mocked.put).toHaveBeenCalledWith('/importing/folders/r1', {
			body: { answers: { [GENERATE_KEY]: false } }
		});
	});

	it('what a Build would do, which queues nothing', async () => {
		mocked.get.mockResolvedValue({ rows: [], files: 0, identifying: 0, running: false, unread: 0 });

		await fetchBuildSheet();

		expect(mocked.get).toHaveBeenCalledWith('/importing/build');
		expect(mocked.post, 'reading the sheet started a pass').not.toHaveBeenCalled();
	});

	it('to start a Build, which is the one that does queue something', async () => {
		mocked.post.mockResolvedValue({ queued: true, files: 3 });

		await startBuild(['pictures', 'faces'], true);

		expect(mocked.post).toHaveBeenCalledWith('/importing/build', {
			body: { products: ['pictures', 'faces'], tonight: true }
		});
	});
});

describe('the three groups the screen arranges the switches into', () => {
	/* Named here rather than read from the server, deliberately: the screen has to group them
	   before any request has come back, and a group whose membership arrived asynchronously
	   would draw itself once empty and once full. */
	it('do not overlap, and no group is empty', () => {
		const groups = [SCAN_KEYS, GENERATE_KEYS, IDENTIFY_KEYS];
		for (const group of groups) expect(group.length).toBeGreaterThan(0);

		const all = groups.flatMap((group) => [...group]);
		expect(new Set(all).size, 'a key is in two groups, so a switch is drawn twice').toBe(
			all.length
		);
	});

	it('are the parts, and the two masters are not among them', () => {
		/* `importing.generate` and `importing.identify` govern their groups; a master listed
		   inside its own group would draw as one of the parts it turns off. */
		const all = [...SCAN_KEYS, ...GENERATE_KEYS, ...IDENTIFY_KEYS];
		expect(all).not.toContain(GENERATE_KEY);
		expect(all).not.toContain(IDENTIFY_KEY);
	});

	it('offers no switch for the thumbnail, which every file gets', () => {
		expect(GENERATE_KEYS).not.toContain('performance.generate_thumbnails');
		expect(GENERATE_KEYS[0]).toBe('performance.generate_previews');
	});
});
