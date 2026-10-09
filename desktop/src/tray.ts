/* What the close button does, and the notification-area icon that is the way back. */

import { Menu, Tray, type MenuItemConstructorOptions } from 'electron';

/** What a close is asking for. */
export type Closing = 'hide' | 'quit';

/** What pressing the window's close button should do. */
export function whatClosingDoes(keepRunning: boolean, quitting: boolean): Closing {
	if (quitting) return 'quit';
	return keepRunning ? 'hide' : 'quit';
}

/** What the icon in the notification area can do. Two verbs, and there is no third worth offering. */
export interface TrayVerbs {
	/** Bring the window back and put it in front. */
	open(): void;
	/** Stop Sift properly: the backend is asked to stop, and the application ends. */
	quit(): void;
}

/** The menu on that icon. Built as a template rather than as a `Menu`, so what it offers can be
 * read and pressed in a test without a running Electron. */
export function trayMenuTemplate(verbs: TrayVerbs): MenuItemConstructorOptions[] {
	return [
		{ label: 'Open Sift', click: () => verbs.open() },
		{ type: 'separator' },
		{ label: 'Quit Sift', click: () => verbs.quit() }
	];
}

/** Put Sift's icon in the notification area, and answer the handle that takes it away again. */
export function showInTray(iconFile: string | null, verbs: TrayVerbs): Tray | null {
	if (iconFile === null) return null;
	const tray = new Tray(iconFile);
	/* The name Windows shows on hover. "Sift" alone, because the tooltip is read beside a dozen
	   others and a sentence there is noise. */
	tray.setToolTip('Sift');
	tray.setContextMenu(Menu.buildFromTemplate(trayMenuTemplate(verbs)));
	/* A double-click opens, which is what every other application in that area does. */
	tray.on('double-click', () => verbs.open());
	return tray;
}
