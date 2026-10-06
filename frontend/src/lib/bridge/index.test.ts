import { afterEach, describe, expect, it, vi } from 'vitest';
import { bridge } from './index';

/* In a browser the bridge does nothing and says so.
 *
 * This is the case that runs for almost everybody, and it is the one that would break quietly: a
 * missing method on `window` throws when it is called, and the throw happens inside a drag handler
 * where it looks like the drag simply did not work.
 */

afterEach(() => {
	vi.unstubAllGlobals();
	delete window.sift;
});

describe('with no desktop client', () => {
	it('reports that it cannot do anything', () => {
		expect(bridge.canNativeDrag()).toBe(false);
		expect(bridge.canChooseFolder()).toBe(false);
	});

	it('does nothing rather than throwing when asked anyway', () => {
		expect(() => bridge.startDrag('01HX')).not.toThrow();
	});

	/* Not a limitation. Granting Sift a folder is exactly the decision that should require the
	 * machine itself, so a browser answering "nowhere" is the correct answer to the question. */
	it('answers no folder rather than throwing when asked to open a dialog', async () => {
		await expect(bridge.chooseFolder()).resolves.toBeNull();
	});
});

describe('with the desktop client', () => {
	it('reports what the client actually has, one method at a time', () => {
		// A client that ships drag before it ships the folder dialog is a real state, and each
		// capability is answered by whether that method is there, not by one flag saying "desktop".
		window.sift = { startDrag: vi.fn() };

		expect(bridge.canNativeDrag()).toBe(true);
		expect(bridge.canChooseFolder()).toBe(false);
	});

	it('passes the call through', () => {
		const startDrag = vi.fn();
		window.sift = { startDrag };

		bridge.startDrag('01HX');

		expect(startDrag).toHaveBeenCalledWith('01HX');
	});
});

describe('choosing a folder', () => {
	it('answers where the person chose', async () => {
		window.sift = { chooseFolder: vi.fn().mockResolvedValue('D:\\Media') };

		expect(bridge.canChooseFolder()).toBe(true);
		await expect(bridge.chooseFolder()).resolves.toBe('D:\\Media');
	});

	it('answers nothing when they cancelled', async () => {
		window.sift = { chooseFolder: vi.fn().mockResolvedValue(null) };

		await expect(bridge.chooseFolder()).resolves.toBeNull();
	});
});

describe('dragging a file out', () => {
	/* The value crosses a process boundary from code that ships separately, so it is CHECKED
	 * against the three names rather than cast: a shell one version ahead could answer something
	 * this page has never heard of, and "unavailable" is the honest reading of that. */
	it('reads an answer it does not recognise as nothing having happened', async () => {
		window.sift = { startDrag: vi.fn().mockResolvedValue('something-new') };

		await expect(bridge.startDrag('01HX')).resolves.toBe('unavailable');
	});

	it('passes the answer it knows straight through', async () => {
		/* One outcome, not two. A shell answering `prepared` (a drag that takes two gestures)
		 * is a shell one version behind, which the test above covers as an answer this page does
		 * not recognise.
		 */
		window.sift = { startDrag: vi.fn().mockResolvedValue('dragged') };
		await expect(bridge.startDrag('01HX')).resolves.toBe('dragged');
	});

	it('watching progress in a browser hands back something safe to call', () => {
		const stop = bridge.onDragProgress(() => {});
		expect(() => stop()).not.toThrow();
	});
});

describe('the clipboard', () => {
	it('is unreadable in a browser, which is the honest answer over plain http', async () => {
		expect(bridge.canReadClipboard()).toBe(false);
		await expect(bridge.readClipboard()).resolves.toBeNull();
	});

	it('answers what the operating system had, in the desktop client', async () => {
		window.sift = { readClipboard: vi.fn().mockResolvedValue({ text: 'a', image: null }) };

		expect(bridge.canReadClipboard()).toBe(true);
		await expect(bridge.readClipboard()).resolves.toEqual({ text: 'a', image: null });
	});
});

describe('a picture of the window', () => {
	it('is out of reach in a browser, which can read a video but not the page around it', async () => {
		expect(bridge.canCaptureWindow()).toBe(false);
		await expect(bridge.captureWindow(null)).resolves.toBeNull();
	});

	it('answers the shell picture as a PNG, and passes the area through', async () => {
		const captureWindow = vi.fn().mockResolvedValue(new Uint8Array([137, 80, 78, 71]));
		window.sift = { captureWindow };
		const area = { x: 1, y: 2, width: 3, height: 4 };

		expect(bridge.canCaptureWindow()).toBe(true);
		const picture = await bridge.captureWindow(area);

		expect(captureWindow).toHaveBeenCalledWith(area);
		expect(picture?.type).toBe('image/png');
		expect(picture?.size).toBe(4);
	});

	it('answers no picture for an empty or unreadable answer', async () => {
		for (const answer of [new Uint8Array(), null, 'data:image/png;base64,AAAA']) {
			window.sift = { captureWindow: vi.fn().mockResolvedValue(answer) };
			await expect(bridge.captureWindow(null)).resolves.toBeNull();
		}
	});
});

describe('saving a server', () => {
	/* A browser answers the refusal itself rather than silently doing nothing: the connect screen
	 * has a form on it, and a form that submits to nowhere is the worst of the three outcomes. */
	it('refuses in words in a browser rather than doing nothing', async () => {
		expect(bridge.canSaveServer()).toBe(false);
		await expect(bridge.saveServer('http://x:5171')).resolves.toBe(
			'This only works in the Sift app.'
		);
	});

	it('hands the shell what was typed, and its refusal back', async () => {
		const saveServer = vi.fn().mockResolvedValue('Nothing answered at that address.');
		window.sift = { saveServer };

		await expect(bridge.saveServer('http://x:5171')).resolves.toBe(
			'Nothing answered at that address.'
		);
		expect(saveServer).toHaveBeenCalledWith('http://x:5171');
	});

	it('answers nothing for the last server when there is no shell', async () => {
		await expect(bridge.lastServer()).resolves.toBeNull();
	});

	/* A shell from before the connect screen could say why, or list what it had saved, still
	 * answers the last address, so the screen starts from it rather than from blank. */
	it('reads the connect state off an older shell from the last address alone', async () => {
		window.sift = { lastServer: vi.fn().mockResolvedValue('http://x:5171') };

		await expect(bridge.connectState()).resolves.toEqual({
			last: 'http://x:5171',
			problem: null,
			servers: []
		});
		await expect(bridge.forgetServer('http://x:5171')).resolves.toEqual([]);
	});

	it('hands the connect state and a forgotten address straight through', async () => {
		const state = {
			last: 'http://x:5171',
			problem: 'Sift could not reach http://x:5171. Nothing answered at that address.',
			servers: [{ label: 'http://x:5171', origin: 'http://x:5171' }]
		};
		const forgetServer = vi.fn().mockResolvedValue([]);
		window.sift = { connectState: vi.fn().mockResolvedValue(state), forgetServer };

		await expect(bridge.connectState()).resolves.toEqual(state);
		await expect(bridge.forgetServer('http://x:5171')).resolves.toEqual([]);
		expect(forgetServer).toHaveBeenCalledWith('http://x:5171');
	});
});

describe('updating', () => {
	it('cannot update in a browser, and says so rather than pretending', async () => {
		expect(bridge.canApplyUpdate()).toBe(false);
		await expect(bridge.applyUpdate()).resolves.toEqual({ ok: false, reason: 'failed' });
	});

	it('hands the shell answer straight back', async () => {
		window.sift = { applyUpdate: vi.fn().mockResolvedValue({ ok: true, version: 'v1.2.3' }) };

		await expect(bridge.applyUpdate()).resolves.toEqual({ ok: true, version: 'v1.2.3' });
	});
});

/* Whether this page is inside the desktop application at all.
 *
 * What needs it is a screen naming whose computer it is describing: "there is no second computer"
 * is true both on the machine running the library and in a browser, and those two want different
 * words: a browser is not a machine.
 */
describe('isDesktop', () => {
	it('is false with no bridge, which is what a browser has', () => {
		expect(bridge.isDesktop()).toBe(false);
	});

	it('is true inside the application', () => {
		window.sift = { isDesktop: true };

		expect(bridge.isDesktop()).toBe(true);
	});
});

/* The firewall, which is the one thing Sift ever asks Windows for administrator rights to do.
 *
 * `unknown` rather than `closed` is the answer everywhere it cannot be asked, and the difference
 * matters on the screen: closed is what puts a button up offering to open it, so answering closed
 * in a browser would offer an administrator prompt that nothing could ever raise.
 */
describe('the firewall', () => {
	const UNKNOWN = { state: 'unknown', networks: null, scope: null };

	it('is unknown in a browser rather than closed', async () => {
		await expect(bridge.firewall()).resolves.toEqual(UNKNOWN);
		await expect(bridge.openFirewall()).resolves.toEqual(UNKNOWN);
	});

	/* A shell older than this verb is the same case as a browser: it has the sharing switch and not
	 * the button, and the screen falls back to saying what to run by hand. */
	it('is unknown in a client that has the switch but not this', async () => {
		window.sift = { setSharing: vi.fn() };

		await expect(bridge.openFirewall()).resolves.toEqual(UNKNOWN);
	});

	it('hands the shell answer straight back', async () => {
		const closed = { state: 'closed', networks: ['Private'], scope: null };
		const open = { state: 'open', networks: ['Private'], scope: 'private' };
		window.sift = {
			firewall: vi.fn().mockResolvedValue(closed),
			openFirewall: vi.fn().mockResolvedValue(open)
		};

		await expect(bridge.firewall()).resolves.toEqual(closed);
		await expect(bridge.openFirewall()).resolves.toEqual(open);
	});

	/* A shell from before the networks were read answers the word alone. It is an answer that
	 * knows nothing about the networks, and the screen treats it as such rather than as a fault. */
	it('reads an older shell word as a report that knows nothing about the networks', async () => {
		window.sift = {
			firewall: vi.fn().mockResolvedValue('closed'),
			openFirewall: vi.fn().mockResolvedValue('open')
		};

		await expect(bridge.firewall()).resolves.toEqual({
			state: 'closed',
			networks: null,
			scope: null
		});
		await expect(bridge.openFirewall()).resolves.toEqual({
			state: 'open',
			networks: null,
			scope: null
		});
	});

	/* It takes nothing at all. The port and the rule belong to the shell, and a verb that accepted
	 * either would be a verb for "run this as administrator". */
	it('asks for nothing but which networks, so there is nothing else for a page to name', async () => {
		const openFirewall = vi.fn().mockResolvedValue('open');
		window.sift = { openFirewall };

		await bridge.openFirewall();
		expect(openFirewall).toHaveBeenCalledWith('private');

		await bridge.openFirewall('any');
		expect(openFirewall).toHaveBeenCalledWith('any');
	});
});

/* What version the application in front of you is, which the page cannot work out for itself:
 * everything it can see is served by the library's machine, and in client mode that is another
 * computer. */
describe('the version of this copy', () => {
	it('is nothing in a browser, where there is no copy to have a version', async () => {
		await expect(bridge.shellVersion()).resolves.toBeNull();
	});

	it('is nothing in a client too old to answer, rather than a wrong number', async () => {
		window.sift = { isDesktop: true };

		await expect(bridge.shellVersion()).resolves.toBeNull();
	});

	it('hands the shell answer straight back', async () => {
		window.sift = { shellVersion: vi.fn().mockResolvedValue('0.1.0') };

		await expect(bridge.shellVersion()).resolves.toBe('0.1.0');
	});
});

/* The library's Detail setting, handed to the shell's own log. */
describe("the shell log's detail", () => {
	it('is nothing in a browser and from a shell too old to be told', async () => {
		await expect(bridge.shellLogDetail(true, false)).resolves.toBeNull();
		window.sift = { isDesktop: true };
		await expect(bridge.shellLogDetail(true, false)).resolves.toBeNull();
	});

	it('hands the shell the answer and hands its answer back', async () => {
		const shellLogDetail = vi.fn().mockResolvedValue(false);
		window.sift = { shellLogDetail };

		await expect(bridge.shellLogDetail(false, true)).resolves.toBe(false);
		expect(shellLogDetail).toHaveBeenCalledWith(false, true);
	});
});

/* Every log of this app and its library, made into one archive by the shell. */
describe('the log archive', () => {
	it('is nothing in a browser and from a shell that cannot make one', async () => {
		expect(bridge.canSaveLogArchive()).toBe(false);
		await expect(bridge.saveLogArchive('logs.zip')).resolves.toHaveProperty('reason');
		window.sift = { isDesktop: true };
		await expect(bridge.saveLogArchive('logs.zip')).resolves.toHaveProperty('reason');
	});

	it('hands the shell the name and hands back where it went', async () => {
		const saveLogArchive = vi.fn().mockResolvedValue({ file: 'C:\\Saved\\logs.zip' });
		window.sift = { saveLogArchive };

		expect(bridge.canSaveLogArchive()).toBe(true);
		await expect(bridge.saveLogArchive('logs.zip')).resolves.toEqual({
			file: 'C:\\Saved\\logs.zip'
		});
		expect(saveLogArchive).toHaveBeenCalledWith('logs.zip');
	});
});

describe('the canvas colour handed to the desktop window', () => {
	it('is the computed colour as six hex digits, or nothing for a colour it cannot read', async () => {
		const { canvasHex } = await import('./index');
		const channels = (hex: string | null) =>
			[1, 3, 5].map((at) => Number.parseInt(hex?.slice(at, at + 2) ?? '', 16));
		expect(canvasHex('rgb(10, 11, 255)')).toHaveLength(7);
		expect(channels(canvasHex('rgb(10, 11, 255)'))).toEqual([10, 11, 255]);
		expect(channels(canvasHex('rgba(0, 1, 2, 1)'))).toEqual([0, 1, 2]);
		expect(canvasHex('transparent')).toBeNull();
	});
});
