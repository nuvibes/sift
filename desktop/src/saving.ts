// SPDX-License-Identifier: AGPL-3.0-or-later
/* Where a file the page saves goes, with no dialog in the way. */

import { app, session } from 'electron';
import * as fs from 'node:fs';
import * as path from 'node:path';

/* Where a saved file goes, without asking: Chromium's Save As would be a second decision, in the
 * operating system's dialog. */
export function saveWithoutAsking(folder: () => string): void {
	session.defaultSession.on('will-download', (_event, item) => {
		let directory = folder();
		if (!fs.existsSync(directory)) directory = app.getPath('downloads');
		item.setSavePath(path.join(directory, uniqueIn(directory, item.getFilename())));
	});
}

/** `name` in `directory`, or the same name with a counter where it is already taken. */
export function uniqueIn(directory: string, name: string): string {
	if (!fs.existsSync(path.join(directory, name))) return name;
	const extension = path.extname(name);
	const stem = name.slice(0, name.length - extension.length);
	// Bounded: a thousand copies of one file is a key held down.
	for (let counter = 1; counter < 1000; counter += 1) {
		const tried = `${stem} (${counter})${extension}`;
		if (!fs.existsSync(path.join(directory, tried))) return tried;
	}
	return name;
}
