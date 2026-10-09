/* What a library's reading says before one is opened, and the machine's own pickers for one. */

import { dialog } from 'electron';

import { backUpLibrary, inspectLibrary, type LibraryReport } from './libraries';
import { log } from './log';
import type { DataLocations } from './paths';

/** The machine's own file dialog, filtered to Sift's databases, or null when closed. */
export async function pickDatabaseFile(): Promise<string | null> {
	const picked = await dialog.showOpenDialog({
		title: 'Choose a Sift database',
		properties: ['openFile', 'dontAddToRecent'],
		filters: [{ name: 'Sift database', extensions: ['sqlite3'] }],
		buttonLabel: 'Open'
	});
	const chosen = picked.filePaths[0];
	if (picked.canceled || chosen === undefined) return null;
	return chosen;
}

/* What each reading of a CHOSEN FILE says, or undefined to carry on: no "new" or "gone" readings for
 * a file just picked, and `newer` is the folder's own sentence. */
export function refusalForFile(report: LibraryReport): string | undefined {
	const verdict = report.verdict;
	if (verdict === 'current' || verdict === 'older') return undefined;
	if (verdict === 'newer') return NEWER_REFUSAL;
	if (verdict === 'empty')
		return 'That file is not a Sift library, so there is nothing in it to open.';
	if (verdict === 'unreadable') {
		return report.detail || 'That file is not a Sift library this copy can read.';
	}
	return 'Sift could not read that file to see what is in it.';
}

const NEWER_REFUSAL =
	'That library was last opened by a newer version of Sift than this one. Update Sift, ' +
	'then open it again. Nothing has been changed.';

/* An older library asked for from another computer: its upgrade dialog is on this screen. */
export const OLDER_FROM_AFAR =
	'That library was last opened by an older Sift, and opening it upgrades it. Open it in the Sift ' +
	'app on the computer running Sift, which asks first. Nothing has been changed.';

/** Whether the target's schema lets it be opened, asking first where the answer is a decision:
 * `undefined` carries on, a string is the sentence to show, null is somebody saying no. */
export async function settleSchema(
	target: DataLocations,
	mustBeThere: boolean,
	agreed = false,
	fromAfar = false
): Promise<string | null | undefined> {
	const report = await inspectLibrary(target);
	if (report.verdict === 'empty') {
		/* Empty is NEW when just pointed at, and GONE when this copy opened it before. */
		return mustBeThere
			? 'That library is not there any more. If it is on a drive or a share, check it is connected.'
			: undefined;
	}
	if (report.verdict === 'current') return undefined;
	if (report.verdict === 'newer') return NEWER_REFUSAL;
	/* The reading's own sentence where it has one: it says how to open an older library. */
	if (report.verdict === 'unreadable') {
		return report.detail || 'There is no Sift library in that folder that this copy can read.';
	}
	if (report.verdict === 'unknown') {
		return 'Sift could not read that folder to see what is in it.';
	}
	if (fromAfar) return OLDER_FROM_AFAR;

	const UPGRADE = 0;
	const chosen = agreed
		? { response: UPGRADE }
		: await dialog.showMessageBox({
				type: 'warning',
				title: 'Open this library?',
				message: 'This library was last opened by an older Sift.',
				detail:
					'Opening it upgrades it in one direction; that Sift will not read it afterwards. Sift ' +
					'backs it up first.',
				buttons: ['Upgrade and open', 'Cancel'],
				defaultId: UPGRADE,
				cancelId: 1,
				noLink: true
			});
	if (chosen.response !== UPGRADE) return null;

	const copy = await backUpLibrary(target);
	/* A REFUSAL, not a warning: an upgrade promised a way back. */
	if (copy === null) {
		return 'Sift could not back up that library, so it has not been opened.';
	}
	log.info('library.backed_up', { copy });
	return undefined;
}
