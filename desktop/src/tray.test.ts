/* What the close button does, and what the notification-area icon offers. */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { resetElectronStub, trays } from '../test/electron-stub';
import { showInTray, trayMenuTemplate, whatClosingDoes } from './tray';

beforeEach(() => {
	resetElectronStub();
});

describe('what closing the window does', () => {
	it('hides it when Sift is to keep running', () => {
		expect(whatClosingDoes(true, false)).toBe('hide');
	});

	it('quits when somebody has said close means closed', () => {
		expect(whatClosingDoes(false, false)).toBe('quit');
	});

	/* THE ONE THAT MAKES THE FEATURE CLOSEABLE AT ALL. */
	it('quits while the application is already on its way out, whatever the setting says', () => {
		expect(whatClosingDoes(true, true)).toBe('quit');
		expect(whatClosingDoes(false, true)).toBe('quit');
	});
});

describe('the menu on the icon', () => {
	it('offers exactly Open Sift and Quit Sift', () => {
		const labels = trayMenuTemplate({ open: () => {}, quit: () => {} })
			.map((one) => one.label)
			.filter((one) => one !== undefined);
		expect(labels).toEqual(['Open Sift', 'Quit Sift']);
	});

	/* Named rather than bare. The menu appears among every other application's icon, where a plain
	 * "Quit" belongs to whatever the reader last clicked. */
	it('names Sift in both, because the menu is read beside a dozen others', () => {
		for (const label of trayMenuTemplate({ open: () => {}, quit: () => {} }).map((o) => o.label)) {
			if (label !== undefined) expect(label).toContain('Sift');
		}
	});

	it('opens when Open is pressed and quits when Quit is', () => {
		const open = vi.fn();
		const quit = vi.fn();
		const tray = showInTray('C:\\sift\\installer\\icon.ico', { open, quit });

		expect(tray).not.toBeNull();
		trays[0]?.press('Open Sift');
		expect(open).toHaveBeenCalledTimes(1);
		expect(quit).not.toHaveBeenCalled();

		trays[0]?.press('Quit Sift');
		expect(quit).toHaveBeenCalledTimes(1);
	});

	it('opens on a double-click, the way every other icon in that strip does', () => {
		const open = vi.fn();
		showInTray('C:\\sift\\installer\\icon.ico', { open, quit: () => {} });
		trays[0]?.listeners.get('double-click')?.();
		expect(open).toHaveBeenCalledTimes(1);
	});

	/* A single click is deliberately not bound: on Windows it opens the menu on some machines and
	 * does nothing on others, so binding it makes the menu unreachable for half of them. */
	it('leaves the single click to the operating system', () => {
		showInTray('C:\\sift\\installer\\icon.ico', { open: () => {}, quit: () => {} });
		expect(trays[0]?.listeners.has('click')).toBe(false);
	});

	it('says Sift on hover, and nothing longer', () => {
		showInTray('C:\\sift\\installer\\icon.ico', { open: () => {}, quit: () => {} });
		expect(trays[0]?.tooltip).toBe('Sift');
	});
});

describe('with no icon to draw', () => {
	/* A tray with no picture is a gap in the strip nothing can be clicked on, which is worse than
	 * no tray at all, and the caller's answer to null is to leave the close button alone. */
	it('draws nothing rather than an empty gap in the notification area', () => {
		expect(showInTray(null, { open: () => {}, quit: () => {} })).toBeNull();
		expect(trays).toHaveLength(0);
	});
});
