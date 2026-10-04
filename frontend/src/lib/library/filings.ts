// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * Which sites a file is filed under, and how to take one off.
 *
 * ## Why this is a module and not four lines inside the detail view
 *
 * Two of the four things here are decisions rather than plumbing (what a filing is CALLED, and
 * what the control that removes it says it will do), and both have to hold for cases the screen
 * cannot conveniently produce: a site that has been deleted out from under the username still
 * holding the file, and a username row whose name is empty, which is the library's way of writing "from
 * here, poster unknown". Written inline they are two expressions nothing can reach; written here
 * they are four branches with a test each.
 *
 * The plumbing comes along because splitting a request from the sentence that describes its result
 * is how the two come to disagree about what a row is.
 */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** The server's own shape. Generated from the schema it publishes, never hand-copied. */
export type Filing = components['schemas']['FiledUnder'];

/**
 * What one filing is called, in a person's words.
 *
 * "@harlowquin on OnlyFans" and not "OnlyFans: @harlowquin", because the row is answering *where did this
 * file come from* and the answer to that is a poster before it is a site, and because a chip
 * beginning with the site reads as a link to the site, which is what the whole chip already is.
 *
 * A site can be deleted while a username on it still holds files (its `site_id` is
 * `ON DELETE SET NULL`), so both halves can be missing and neither absence is an error. The
 * deleted site is NAMED rather than left blank: a chip with nothing in it is a chip nobody presses,
 * and the one thing somebody wants to do with a filing whose site is gone is take it off.
 *
 * It is named in lower case, which is the one wording decision here. Every label is a fragment that
 * a sentence is built around ("Remove this file from ..."), and a capital in the middle of that
 * sentence reads as a proper noun, so the screen would appear to have found a site actually called
 * "A site that was deleted".
 */
export function filingLabel(filing: Filing): string {
	if (filing.site && filing.username) return `${filing.username} on ${filing.site}`;
	if (filing.site) return filing.site;
	if (filing.username) return `${filing.username}, on a Site that was deleted`;
	return 'a Site that was deleted';
}

/**
 * What the CHIP says: the site's name, and nothing else.
 *
 * ## Why the chip is not the sentence above
 *
 * `filingLabel` is the sentence, and it is right for what it is for: a control that promises to
 * take something off has to name the whole thing it is taking off. The chip sits in a row of SITES,
 * under the Sites glyph, beside a row of people under the people glyph. The kind is said once, at
 * the head of the row, so a chip reading "@harlowquin on QuillMoss" would repeat the row's own heading
 * and then bury the site's name behind a username, which is the one word somebody is scanning the
 * row for.
 *
 * Two filings under one site therefore draw two identical chips, and that is accepted rather than
 * folded: a row per row is what makes one cross remove one thing. See the model's own note.
 *
 * The deleted-site case keeps the sentence, because there is no name to fall back to and "a site
 * that was deleted" is the only thing left to say. The username comes with it there for the same
 * reason: it is all that distinguishes one orphaned filing from another.
 */
export function filingName(filing: Filing): string {
	return filing.site ?? filingLabel(filing);
}

/**
 * What the cross beside a filing promises, said in full for a screen reader.
 *
 * "Take off" rather than "remove" or "delete", and the sentence names the FILE: nothing about the
 * site, the username or any other file changes, and a control on a page full of destructive verbs
 * has to say which of them it is not. See the route: one row of a join table goes, and the bytes,
 * the path and the location rows are exactly as they were.
 */
export function filingRemovalLabel(filing: Filing): string {
	return `Remove this file from ${filingLabel(filing)}`;
}

/** Every site this file is filed under. Sites the asking account's vault conceals are not in it. */
export async function filingsOf(assetId: string): Promise<Filing[]> {
	return await api.get<Filing[]>(`/assets/${assetId}/filings`);
}

/** Remove this file from one username. Quiet where the filing had already gone. */
export async function removeFiling(assetId: string, usernameId: string): Promise<void> {
	await api.del(`/assets/${assetId}/filings/${usernameId}`);
}
