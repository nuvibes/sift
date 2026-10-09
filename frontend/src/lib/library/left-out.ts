// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Files wall filtered to the files one product gave up on, and whether a wall is one. */

/** The parameter the Files wall filters by to show the files one product gave up on. */
const LEFT_OUT_FIELD = 'left_out';

/** The Files wall showing every file this product gave up on, each saying why under its tile. */
export function leftOutWall(product: string): string {
	return `/browse?${new URLSearchParams({ [LEFT_OUT_FIELD]: product })}`;
}

/** Whether a wall asked THIS way carries a line under every tile saying why the file was left
 * out. */
export function asksWhyLeftOut(query: Readonly<Record<string, string>>): boolean {
	const value = query[LEFT_OUT_FIELD];
	if (!value) return false;
	/* An excluding one (`-thumbnails`) is every file a product did NOT give up on: nothing to say. */
	return !value.startsWith('-');
}
