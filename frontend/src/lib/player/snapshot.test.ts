import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
	show: vi.fn(),
	download: vi.fn(),
	request: vi.fn(),
	values: new Map<string, unknown>(),
	admin: true
}));
vi.mock('$lib/shell/toasts.svelte', () => ({ toasts: { show: mocks.show } }));
vi.mock('$lib/capture/copy-out', () => ({ triggerDownload: mocks.download }));
vi.mock('$lib/settings-ui/settings', () => ({
	fetchSettingValues: async () => mocks.values,
	onSettingsSaved: () => {}
}));
vi.mock('$lib/shell/session.svelte', () => ({
	session: {
		get isAdmin() {
			return mocks.admin;
		}
	}
}));
vi.mock('$lib/api/client', () => ({
	request: mocks.request,
	ApiError: class extends Error {
		detail?: string;
	}
}));

import {
	deliver,
	drawnBox,
	frameName,
	lastStretch,
	screenshotName,
	SHOT_LABELS,
	SHOT_MENU,
	shotsOffered,
	takeShot
} from './snapshot';

describe('the last few seconds', () => {
	it('ends at the playhead and runs back as far as asked', () => {
		expect(lastStretch(42.5, 10)).toEqual({ startMs: 32_500, durationMs: 10_000 });
	});

	it('takes what there is near the start rather than refusing', () => {
		expect(lastStretch(3, 10)).toEqual({ startMs: 0, durationMs: 3_000 });
	});

	it('has nothing to keep before anything has played', () => {
		expect(lastStretch(0, 10)).toBeNull();
		expect(lastStretch(Number.NaN, 10)).toBeNull();
	});
});

describe('a saved frame', () => {
	it('is named for the moment it was taken', () => {
		expect(frameName(125.9)).toBe('frame-2m05s.png');
	});
});

describe('a screenshot', () => {
	const PICTURE = new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' });
	let written: unknown[] = [];

	beforeEach(() => {
		mocks.show.mockReset();
		mocks.download.mockReset();
		mocks.request.mockReset().mockResolvedValue({ job_id: 'j1' });
		mocks.values = new Map();
		mocks.admin = true;
		written = [];
		Object.assign(URL, { createObjectURL: () => 'blob:picture', revokeObjectURL: () => {} });
	});

	afterEach(() => {
		delete window.sift;
		vi.unstubAllGlobals();
		document.querySelectorAll('.sift-shutter').forEach((one) => one.remove());
	});

	/** A page that may write pictures to the clipboard, as a secure one may. */
	function clipboardAllowed(): void {
		vi.stubGlobal('isSecureContext', true);
		vi.stubGlobal(
			'ClipboardItem',
			class {
				constructor(readonly items: Record<string, Blob>) {}
			}
		);
		Object.defineProperty(navigator, 'clipboard', {
			configurable: true,
			value: { write: async (items: unknown[]) => void written.push(...items) }
		});
	}

	it('is called Screenshot, and is the frame alone in a browser and three in the app', () => {
		expect(SHOT_MENU).toBe('Screenshot');
		expect(shotsOffered()).toEqual(['frame']);
		window.sift = { captureWindow: vi.fn() };
		expect(shotsOffered()).toEqual(['frame', 'player', 'window']);
		for (const shot of shotsOffered()) expect(SHOT_LABELS[shot].length).toBeGreaterThan(0);
	});

	it('is named for the moment it was taken, to the second', () => {
		expect(screenshotName(new Date(2026, 0, 5, 9, 4, 7))).toBe(
			'screenshot-2026-01-05 09-04-07.png'
		);
	});

	it('is copied to the clipboard by default, and nothing is downloaded', async () => {
		clipboardAllowed();
		await deliver(PICTURE, 'screenshot-a.png');
		expect(written).toHaveLength(1);
		expect(mocks.download).not.toHaveBeenCalled();
		expect(mocks.request).not.toHaveBeenCalled();
		expect(mocks.show).toHaveBeenCalledWith('Screenshot copied', { tone: 'success' });
	});

	it('is downloaded where the browser refuses the clipboard, and says why', async () => {
		await deliver(PICTURE, 'screenshot-a.png');
		expect(mocks.download).toHaveBeenCalledWith('blob:picture', 'screenshot-a.png');
		expect(mocks.show.mock.calls[0]?.[0]).toMatch(/can't copy pictures here/);
	});

	it('in the app, never goes to the download folder: refused the clipboard, it saves into the library', async () => {
		window.sift = { captureWindow: vi.fn() };
		await deliver(PICTURE, 'screenshot-a.png');
		expect(mocks.download).not.toHaveBeenCalled();
		expect(mocks.request).toHaveBeenCalledWith('POST', '/capture/import/file', {
			body: expect.any(FormData)
		});
	});

	it('is saved into the folder the setting names when Save is chosen', async () => {
		clipboardAllowed();
		mocks.values = new Map<string, unknown>([
			['playback.screenshot', 'save'],
			['playback.screenshot_folder', 'folder-7']
		]);
		await deliver(PICTURE, 'screenshot-a.png');
		const form = mocks.request.mock.calls[0]?.[2]?.body as FormData;
		expect(form.get('dest_folder_id')).toBe('folder-7');
		expect((form.get('file') as File).name).toBe('screenshot-a.png');
		expect(written).toHaveLength(0);
	});

	it('says it is in the library once the file exists, as a link that opens the new file', async () => {
		mocks.values = new Map<string, unknown>([['playback.screenshot', 'save']]);
		mocks.request.mockImplementation(async (method: string, path: string) => {
			if (method === 'POST') return { job_id: 'j1' };
			if (path === '/jobs/j1/steps') return { jobs: [{ id: 's1', subject_id: 'asset-9' }] };
			return { jobs: [] };
		});
		await deliver(PICTURE, 'screenshot-a.png');

		await vi.waitFor(() =>
			expect(mocks.show).toHaveBeenCalledWith(
				[
					'Screenshot saved to your library as ',
					{ text: 'screenshot-a.png', kind: 'asset', id: 'asset-9' }
				],
				{ tone: 'success' }
			)
		);
	});

	it('names the file on screen to the import, and the toast links the name the file was given', async () => {
		mocks.values = new Map<string, unknown>([['playback.screenshot', 'save']]);
		mocks.request.mockImplementation(async (method: string, path: string) => {
			if (method === 'POST') return { job_id: 'j1' };
			if (path === '/jobs/j1/steps') return { jobs: [{ id: 's1', subject_id: 'asset-9' }] };
			if (path === '/assets/asset-9') return { original_filename: 'Beach Day-ss.png' };
			return { jobs: [] };
		});
		await deliver(PICTURE, 'frame-0m03s.png', 'asset-1');

		const form = mocks.request.mock.calls[0]?.[2]?.body as FormData;
		expect(form.get('screenshot_of')).toBe('asset-1');
		await vi.waitFor(() =>
			expect(mocks.show).toHaveBeenCalledWith(
				[
					'Screenshot saved to your library as ',
					{ text: 'Beach Day-ss.png', kind: 'asset', id: 'asset-9' }
				],
				{ tone: 'success' }
			)
		);
	});

	it('links the name the folder gave a second screenshot, not the name it was sent under', async () => {
		mocks.values = new Map<string, unknown>([['playback.screenshot', 'save']]);
		mocks.request.mockImplementation(async (method: string, path: string) => {
			if (method === 'POST') return { job_id: 'j1' };
			if (path === '/jobs/j1/steps') return { jobs: [{ id: 's1', subject_id: 'asset-9' }] };
			if (path === '/assets/asset-9')
				return { original_filename: 'Beach Day-ss.png', filename: 'Beach Day-ss-1.png' };
			return { jobs: [] };
		});
		await deliver(PICTURE, 'frame-0m03s.png', 'asset-1');

		await vi.waitFor(() =>
			expect(mocks.show).toHaveBeenCalledWith(
				[
					'Screenshot saved to your library as ',
					{ text: 'Beach Day-ss-1.png', kind: 'asset', id: 'asset-9' }
				],
				{ tone: 'success' }
			)
		);
	});

	it('says a refused import as a refusal, never as a save', async () => {
		mocks.values = new Map<string, unknown>([['playback.screenshot', 'save']]);
		mocks.request.mockImplementation(async (method: string, path: string) => {
			if (method === 'POST') return { job_id: 'j1' };
			if (path === '/jobs/j1/steps') return { jobs: [] };
			return { jobs: [{ id: 'j1', state: 'failed', error: 'That is not a picture', note: null }] };
		});
		await deliver(PICTURE, 'screenshot-a.png');

		await vi.waitFor(() =>
			expect(mocks.show).toHaveBeenCalledWith('That is not a picture', { tone: 'error' })
		);
		expect(mocks.show).toHaveBeenCalledTimes(1);
	});

	it('leaves the folder to the server, the default downloads folder, when none is named', async () => {
		mocks.values = new Map<string, unknown>([['playback.screenshot', 'save']]);
		await deliver(PICTURE, 'screenshot-a.png');
		const form = mocks.request.mock.calls[0]?.[2]?.body as FormData;
		expect(form.get('dest_folder_id')).toBeNull();
	});

	it('is not added to the library for somebody who may not add files', async () => {
		mocks.admin = false;
		mocks.values = new Map<string, unknown>([['playback.screenshot', 'save']]);
		await deliver(PICTURE, 'screenshot-a.png');
		expect(mocks.request).not.toHaveBeenCalled();
		expect(mocks.download).toHaveBeenCalled();
	});

	it('asks the app for the player without its bar, and draws the shutter after', async () => {
		clipboardAllowed();
		let bare: boolean | null = null;
		const captureWindow = vi.fn().mockImplementation(async () => {
			bare = document.documentElement.classList.contains('shooting');
			return new Uint8Array([1]);
		});
		window.sift = { captureWindow };
		const stage = document.createElement('div');
		stage.getBoundingClientRect = () => ({ left: 10, top: 20, width: 300, height: 200 }) as DOMRect;

		await takeShot('player', { video: null, stage });

		expect(captureWindow).toHaveBeenCalledWith({ x: 10, y: 20, width: 300, height: 200 });
		expect(bare).toBe(true);
		expect(document.documentElement.classList.contains('shooting')).toBe(false);
		expect(document.querySelector('.sift-shutter')).not.toBeNull();
	});

	it('draws the shutter over what was photographed: the player for the player', async () => {
		clipboardAllowed();
		window.sift = { captureWindow: vi.fn().mockResolvedValue(new Uint8Array([1])) };
		const stage = document.createElement('div');
		stage.getBoundingClientRect = () => ({ left: 10, top: 20, width: 300, height: 200 }) as DOMRect;

		await takeShot('player', { video: null, stage });

		const veil = document.querySelector('.sift-shutter') as HTMLElement;
		expect([veil.style.left, veil.style.top, veil.style.width, veil.style.height]).toEqual([
			'10px',
			'20px',
			'300px',
			'200px'
		]);
	});

	it('and the whole screen for the whole window', async () => {
		clipboardAllowed();
		window.sift = { captureWindow: vi.fn().mockResolvedValue(new Uint8Array([1])) };

		await takeShot('window', { video: null, stage: document.createElement('div') });

		const veil = document.querySelector('.sift-shutter') as HTMLElement;
		expect(veil.style.width, 'a whole-window shutter was cut down').toBe('');
	});

	it('finds the frame inside its box, between the bars a fitted picture leaves', () => {
		/* A 16:9 video in a square box is drawn full width with a bar above and below: the frame
		   the shutter covers is the picture, not the bars. */
		const video = document.createElement('video');
		Object.defineProperty(video, 'videoWidth', { value: 1600 });
		Object.defineProperty(video, 'videoHeight', { value: 900 });
		video.getBoundingClientRect = () => ({ left: 10, top: 20, width: 400, height: 400 }) as DOMRect;

		expect(drawnBox(video)).toEqual({ left: 10, top: 107.5, width: 400, height: 225 });
	});

	it('takes the whole window as it is, controls and all', async () => {
		let bare: boolean | null = null;
		const captureWindow = vi.fn().mockImplementation(async () => {
			bare = document.documentElement.classList.contains('shooting');
			return null;
		});
		window.sift = { captureWindow };
		await takeShot('window', { video: null, stage: document.createElement('div') });
		expect(captureWindow).toHaveBeenCalledWith(null);
		expect(bare).toBe(false);
	});

	it('says so when the picture could not be taken, and delivers nothing and no shutter', async () => {
		window.sift = { captureWindow: vi.fn().mockResolvedValue(null) };
		await takeShot('window', { video: null, stage: null });
		expect(mocks.download).not.toHaveBeenCalled();
		expect(mocks.show).toHaveBeenCalledWith("The screenshot couldn't be taken", { tone: 'error' });
		expect(document.querySelector('.sift-shutter')).toBeNull();
	});
});
