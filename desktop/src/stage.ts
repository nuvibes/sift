// SPDX-License-Identifier: AGPL-3.0-or-later
/* The page saying how far it has drawn, for the opening frame over the window. */

import { ipcMain } from 'electron';

import { screenOf, WINDOW_STAGE, type WindowStage } from './opening';
import { askingFrame, type ReachCheck } from './verbs';

/** `drawn` hears the stage, the look (unchecked, for `opening.ts` to read) and the screen's first
 *  path segment, from a trusted top frame only: a frame inside the page has drawn nothing. */
export function registerWindowStage(
	reachOf: ReachCheck,
	drawn: (stage: WindowStage, look: unknown, screen: string) => void
): void {
	ipcMain.handle(WINDOW_STAGE, async (event, stage: unknown, look: unknown): Promise<boolean> => {
		const frame = askingFrame(event, reachOf, WINDOW_STAGE);
		if (frame === null) return false;
		if (stage !== 'painted' && stage !== 'usable') return false;
		drawn(stage, look, screenOf(frame.url));
		return true;
	});
}
