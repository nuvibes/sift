// SPDX-License-Identifier: AGPL-3.0-or-later
/* Whether one release number comes after another, the way the shell and the server read them. */

const SHAPE = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/;

/** True when `candidate` is a later release than `running`; false when either will not read. */
export function isNewer(candidate: string, running: string): boolean {
	const later = SHAPE.exec(candidate.trim());
	const now = SHAPE.exec(running.trim());
	if (later === null || now === null) return false;
	for (let i = 1; i <= 3; i += 1) {
		const difference = Number(later[i]) - Number(now[i]);
		if (difference !== 0) return difference > 0;
	}
	// The same three numbers: a pre-release sorts below the release it leads to.
	if (later[4] === now[4]) return false;
	if (later[4] === undefined) return true;
	if (now[4] === undefined) return false;
	return later[4] > now[4];
}
