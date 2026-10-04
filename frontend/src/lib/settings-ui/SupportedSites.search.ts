// SPDX-License-Identifier: AGPL-3.0-or-later
/* What a pasted link will do, per Site: the words, and what somebody can type to find them. Drawn
 * on Sites, beside the cookies and the tunnels of the same Sites. "What can Sift fetch" sounds
 * like a downloading question, but the two panes split on a rule: Downloads is what is set once
 * about downloading, and a table of what each Site supports, needs and refuses is a fact about
 * the Sites. The key follows the table: a search result that lands on a pane not drawing the
 * thing it named is worse than no result, because it teaches somebody the wrong place to look.
 *
 * ONE COPY MODULE PER PANE. `SupportedSites.svelte` draws every word it adds from `COPY`, and the
 * search entry is built from the same object. The cookies badges are `NEED_BADGE`'s, shared with
 * the cookies sheet, and each Site's own sentences are the server's.
 */
import type { Searchable } from './search';

export const COPY = {
	name: 'Supported Sites',
	help: 'What happens when you paste a link from a given Site.',
	lede: "Sift recognizes a link from any of these and adds the file to its Site, and to the username in the address where there's one. Links from other Sites may still download.",
	supported: 'Supported',
	supportedSays: 'Tested and working.',
	routing: 'Routing only',
	routingSays:
		'Not officially supported, but usually works. Requests go through a tunnel you added, to get past regional blocks.',
	single: 'One link at a time',
	singleSays: 'Partly supported: a single link works, but not a whole profile or channel.',
	requiredSays: 'Nothing downloads until cookies are added.',
	partialSays: 'Public content downloads without cookies; some needs them.',
	notNeededSays: 'Everything downloads without cookies.',
	unreachable: "Couldn't reach Sift. Check that it's still running.",
	/* The fold's words say how many Sites are behind it, since that count is why it is folded. */
	showAll: (count: number) =>
		`Show all ${count.toLocaleString()} ${count === 1 ? 'Site' : 'Sites'}`,
	columns: {
		site: 'Site',
		support: 'Support',
		addresses: 'Addresses',
		bulk: 'Bulk downloads',
		cookies: 'Cookies',
		issues: 'Known issues'
	},
	yes: 'Yes',
	nothingRecorded: 'Nothing recorded'
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.name,
		key: 'sites.supported',
		section: 'sites',
		help: COPY.help,
		keywords:
			'which sites work supported extractors yt-dlp gallery-dl routing only bulk whole profile matrix list sites sift knows'
	}
];
