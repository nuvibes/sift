/* What the close button does, and the notification-area icon that is the way back.
 *
 * WHY THIS IS A MODULE AND NOT FOUR LINES IN `main`.
 *
 * `main.ts` is the process's own top level, and importing it starts an application: its test has
 * to double every module it composes to reach it at all. So the two decisions worth being sure of
 * live here, where a test can make them directly: what closing the window means, and what the menu
 * on that icon offers.
 *
 * The close button is not the quit button once this is on, and that is the whole feature. Sift with
 * its window shut is still a library being scanned, a download finishing, a recognition pass
 * running, and (when sharing is on) a service another computer is reading from. A close that
 * stopped all of it would be a gesture that costs hours of work and gives no warning; the icon in
 * the notification area is what makes "still running" visible and "actually stop" reachable.
 */

import { Menu, Tray, type MenuItemConstructorOptions } from 'electron';

/** What a close is asking for. */
export type Closing = 'hide' | 'quit';

/**
 * What pressing the window's close button should do.
 *
 * TWO INPUTS, AND THE SECOND IS NOT OPTIONAL. `quitting` is the shell saying the application is
 * already on its way out: the tray's own Quit, an update's relaunch, a shutdown. Without it,
 * hiding the window on every close means Quit hides the window instead of quitting and the
 * application can never be closed at all. That is not a hypothetical: it is the first bug every
 * close-to-tray implementation has.
 */
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

/**
 * The menu on that icon.
 *
 * Built as a template rather than as a `Menu`, so what it offers can be read and pressed in a test
 * without a running Electron. The labels name Sift, not "Open" and "Quit" on their own: the menu
 * appears among every other application's icon, where a bare "Quit" belongs to whatever the reader
 * last clicked.
 */
export function trayMenuTemplate(verbs: TrayVerbs): MenuItemConstructorOptions[] {
	return [
		{ label: 'Open Sift', click: () => verbs.open() },
		{ type: 'separator' },
		{ label: 'Quit Sift', click: () => verbs.quit() }
	];
}

/**
 * Put Sift's icon in the notification area, and answer the handle that takes it away again.
 *
 * The icon file is passed in rather than looked up here for the reason every path in this shell is
 * passed: where it lives differs between a checkout and an installed copy, and `paths.ts` is the
 * one place that knows which. Null when there is no icon to draw: a tray with no picture is a
 * gap in the tray that nothing can be clicked on, which is worse than no tray at all, and the
 * window then keeps the ordinary close.
 */
export function showInTray(iconFile: string | null, verbs: TrayVerbs): Tray | null {
	if (iconFile === null) return null;
	const tray = new Tray(iconFile);
	/* The name Windows shows on hover. "Sift" alone, because the tooltip is read beside a dozen
	   others and a sentence there is noise. */
	tray.setToolTip('Sift');
	tray.setContextMenu(Menu.buildFromTemplate(trayMenuTemplate(verbs)));
	/* A double-click opens, which is what every other application in that area does. Single-click
	   is deliberately not bound: on Windows a single click is what opens the menu on some machines
	   and nothing on others, so binding it makes the menu unreachable for half of them. */
	tray.on('double-click', () => verbs.open());
	return tray;
}
