/* A heading's link in the Documentation pane, as the docs site's own: one press copies it. */
import { copyText } from '$lib/shell/clipboard';
import { toasts } from '$lib/shell/toasts.svelte';

/** The address that opens a page of the pane at one of its headings. */
export function sectionAddress(page: string, anchor: string, origin: string): string {
	return new URL(`${page}#doc-${anchor}`, origin).href;
}

/** Copy a section's address and say whether it landed. */
export async function copySectionLink(address: string): Promise<boolean> {
	const landed = await copyText(address);
	if (landed) toasts.show('Link copied');
	else toasts.show("That link couldn't be copied", { tone: 'error' });
	return landed;
}
