// SPDX-License-Identifier: AGPL-3.0-or-later
/* What a pasted link will do, per Site: the words, and what somebody can type to find them. */
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
