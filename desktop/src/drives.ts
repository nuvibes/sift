/* Whether a folder sits on a drive this computer reaches over the network. */

import { execFile } from 'node:child_process';

/** Answers a drive letter's kind as Windows names it (`Fixed`, `Network`, `Removable`...), or
 *  null when it could not be asked. Injected so a test never spawns PowerShell. */
export type DriveKindAsker = (letter: string) => Promise<string | null>;

const ASK_TIMEOUT_MS = 10_000;

const askWindows: DriveKindAsker = (letter) =>
	new Promise((resolve) => {
		/* `DriveInfo` answers by object, in every language Windows ships in; parsing `net use`
		 * would not. The letter is checked by the caller before it reaches this text. */
		execFile(
			'powershell.exe',
			[
				'-NoProfile',
				'-NonInteractive',
				'-Command',
				`([System.IO.DriveInfo]::new('${letter}:\\')).DriveType`
			],
			{ timeout: ASK_TIMEOUT_MS, windowsHide: true },
			(error, stdout) => resolve(error ? null : stdout.trim())
		);
	});

/** The sentence a refused folder gets. One place, because two screens say it. */
export const KEEP_IT_HERE =
	"Sift keeps its library on this device's own drive. A network folder can hold your photos and videos; the database that describes them has to stay here.";

/** Whether the folder is on a network drive: a UNC path, or a drive letter Windows calls
 * `Network`. */
export async function isRemoteDrive(target: string, ask: DriveKindAsker = askWindows): Promise<boolean> {
	if (target.startsWith('\\\\') || target.startsWith('//')) return true;
	const letter = /^([A-Za-z]):/.exec(target)?.[1];
	if (letter === undefined) return false;
	const kind = await ask(letter.toUpperCase());
	return kind !== null && kind.trim().toLowerCase() === 'network';
}
