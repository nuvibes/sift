// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The Files wall filtered to the files one product gave up on, and whether a wall is one.
 *
 * The query language's own word (`left_out:<product>`), written as a named parameter so a link to
 * it is an ordinary address and the bar draws it as an ordinary chip, the shape `like` already has.
 * The Importing pane's sentence ("24 files couldn't have thumbnails generated and are left out")
 * makes its count this link. The value is the product's own key, the one the pane's row carries,
 * and the server answers it through the same scoped read every wall uses, so a file the vault is
 * holding back is on the wall exactly as it is anywhere else.
 */

/** The parameter the Files wall filters by to show the files one product gave up on. */
const LEFT_OUT_FIELD = 'left_out';

/** The Files wall showing every file this product gave up on, each saying why under its tile. */
export function leftOutWall(product: string): string {
	return `/browse?${new URLSearchParams({ [LEFT_OUT_FIELD]: product })}`;
}

/**
 * Whether a wall asked THIS way carries a line under every tile saying why the file was left out.
 *
 * Read off the named parameter, the way a link writes it, so the wall is laid out with the line
 * from its first page rather than re-laid when the rows arrive. A `left_out:` TYPED into the box
 * travels as free text, which only the server can take apart; those rows still carry their words
 * and the grid gives them the line as they land (see `Grid.caption`).
 */
export function asksWhyLeftOut(query: Readonly<Record<string, string>>): boolean {
	const value = query[LEFT_OUT_FIELD];
	if (!value) return false;
	/* An excluding one (`-thumbnails`) is every file a product did NOT give up on: nothing to say. */
	return !value.startsWith('-');
}
