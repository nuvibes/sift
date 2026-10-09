// SPDX-License-Identifier: AGPL-3.0-or-later
/* Which sites a file is filed under, and how to take one off. */

import { api } from '$lib/api/client';
import type { components } from '$lib/api/schema';

/** The server's own shape. Generated from the schema it publishes, never hand-copied. */
export type Filing = components['schemas']['FiledUnder'];

/** What one filing is called, in a person's words. */
export function filingLabel(filing: Filing): string {
	if (filing.site && filing.username) return `${filing.username} on ${filing.site}`;
	if (filing.site) return filing.site;
	if (filing.username) return `${filing.username}, on a Site that was deleted`;
	return 'a Site that was deleted';
}

/** What the CHIP says: the site's name, and nothing else. */
export function filingName(filing: Filing): string {
	return filing.site ?? filingLabel(filing);
}

/** What the cross beside a filing promises, said in full for a screen reader. */
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
