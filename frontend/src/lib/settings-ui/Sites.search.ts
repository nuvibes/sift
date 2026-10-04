// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Sites and Tunnels pane: cookies, the tunnels, and which Site uses which: its words, and
 * what somebody can type to find them.
 *
 * ONE COPY MODULE PER PANE. `Sites.svelte` and the two blocks it draws, `Tunnels.svelte` and
 * `Routing.svelte`, take every word they add from `COPY`, and the search entries are built from the
 * same objects, so a heading cannot say one thing on the pane and another in a search result. The
 * third block it draws, the Sites Sift knows, carries its own module (`SupportedSites.search.ts`).
 *
 * None of these is a registered setting: saved cookies are a secret and a tunnel is a file
 * somebody dropped in. `keywords` is carrying most of the weight here: the words people use for
 * these are almost never the words on the heading, which is why "login" and "sign in" are still
 * searchable terms below while the screen itself never says either. */
import { counted } from '$lib/entity/entity-counts';
import type { Searchable } from './search';

export const COPY = {
	cookies: {
		name: 'Cookies',
		help: 'Add the cookies your browser holds for a Site, and Sift can download from it.',
		lede: 'Some Sites show their files only when you are signed in. Add the cookies your browser already holds for a Site, and Sift can download from it.',
		row: 'Saved cookies',
		none: 'No cookies saved yet.',
		saved: (n: number) =>
			n === 1 ? 'Cookies are saved for 1 Site.' : `Cookies are saved for ${counted(n)} Sites.`,
		edit: 'Edit cookies'
	},
	unlock: {
		name: 'Unlock your saved keys',
		help: 'Your password unlocks the cookies, tunnels and keys Sift has saved.'
	},
	tunnels: {
		name: 'Tunnels',
		help: 'Send a Site through a WireGuard tunnel instead of your own connection.',
		lede: 'A download uses your own IP address unless its Site uses a tunnel. Sift supports WireGuard tunnels.',
		noFallback:
			"A Site set to a tunnel that isn't connected doesn't download at all. It never falls back to your own connection.",
		states: {
			locked: 'Locked',
			finishing: 'Finishing',
			connected: 'Connected',
			notConnecting: 'Not connecting',
			off: 'Off'
		},
		/* Whether a swap can be hosted on the tunnel, as the last try to host one found: the
		   server's `can_host`, which is null until somebody tries. A provider's P2P server with port
		   forwarding on hands the tunnel a port; any other server does not, and only a try tells.
		   One wording, read by every screen that says it. */
		hosting: {
			can: 'Can host a swap',
			cannot: "This configuration can't host a swap because it wasn't created properly.",
			untried: 'Not tried for a swap yet'
		},
		/* "P2P VPN server", not "P2P server": a VPN or proxy server keeps its word, and a bare
		   "server" is the retired word for this device. */
		hostingHelp:
			'A swap needs a tunnel made on a P2P VPN server with port forwarding on. While a swap starts or ends, downloads through that tunnel pause for a moment.',
		imported: 'Tunnel imported. Turn it on to use it.',
		use: (name: string) => `Use ${name}`,
		replaceFor: (name: string) => `Replace the configuration for ${name}`,
		replace: 'Replace',
		deleteNamed: (name: string) => `Delete the tunnel ${name}`,
		import: 'Import a tunnel',
		drop: 'Drop a WireGuard file here',
		chooseLabel: 'Choose a WireGuard file',
		choose: 'Choose a file',
		fileFrom: 'A',
		fileRest: 'file from your provider. Nothing is uploaded until you choose Import tunnel.',
		nameLabel: 'Name',
		nameHelp: 'A name for this tunnel.',
		configLabel: 'Configuration',
		configHelp:
			'Filled in by the file you chose, or paste what is inside one. It is encrypted and never shown again.',
		lockedFirst:
			'Unlock your saved cookies and tunnels at the top of this page first. Tunnel settings are encrypted with your password, and Sift needs it again after a restart.',
		importButton: 'Import tunnel',
		deleteTitle: (name: string | null) =>
			name ? `Delete the tunnel ${name}?` : 'Delete this tunnel?',
		/* NOT "Sites sent through it use your own connection": the route is
		   left pointing at the deleted tunnel on purpose (`delete_tunnel`, and the routes table's
		   own note), so those Sites refuse to download until they are pointed somewhere else. */
		deleteSays:
			'Its configuration is deleted. Sites that use it stop downloading until you choose another tunnel or your own connection for them.',
		deleteConfirm: 'Delete'
	},
	routing: {
		name: 'Site tunnels',
		help: 'Which connection each Site uses.',
		nothingYet:
			"There's nothing to choose yet. Every Site uses your own connection until you import a tunnel above.",
		ownIp: 'Every Site uses your own IP address unless you choose a tunnel for it.',
		direct: 'Direct \u2014 your own connection',
		useDefault: 'Use the default',
		deleted: 'The tunnel this used has been deleted',
		defaultLabel: 'Default for every Site',
		defaultHelp: "What a Site uses when you haven't chosen for it.",
		perSite: (count: number) =>
			`Choose a tunnel for each of ${count.toLocaleString()} ${count === 1 ? 'Site' : 'Sites'}`,
		itsDeleted: 'Its tunnel has been deleted',
		using: (tunnel: string) => `Using ${tunnel}`,
		down: (tunnel: string) => `${tunnel} isn't connected`,
		connectionFor: (site: string) => `Connection for ${site}`
	},
	/* The tunnel this Sift dials through when it joins a swap. Whether a tunnel can host one is
	   said on its own row under Tunnels. The join's choice is made on the swap screen, beside the
	   join, where the server it goes through is named; this block says what is chosen and opens
	   that screen. The swap screens' own sentences about a tunnel link
	   here: `/settings/sites#sites.swap_tunnels`. */
	swapTunnels: {
		name: 'Swap tunnels',
		help: 'The tunnel you join a swap through.',
		lede: 'A swap always goes through one of your tunnels, never your own connection. To host one, the tunnel must be made on a P2P VPN server with port forwarding on.',
		join: {
			name: 'Tunnel for joining a swap',
			help: 'The tunnel your side of a swap goes through when you join one.',
			chosen: (tunnel: string) => `Joins through ${tunnel}.`,
			unchosen: 'Not chosen yet. You choose it on the swap screen when you join a swap.',
			gone: 'The tunnel it used has been deleted. Choose another on the swap screen.',
			open: 'Open',
			openLabel: 'Open the swap screen'
		}
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.cookies.name,
		key: 'sites.cookies',
		section: 'sites',
		help: COPY.cookies.help,
		keywords: 'cookies login account credentials sign in authentication instagram onlyfans reddit'
	},
	{
		name: COPY.tunnels.name,
		key: 'sites.tunnels',
		section: 'sites',
		help: COPY.tunnels.help,
		keywords:
			'vpn wireguard proxy exit ip address region country geoblock swap host port forwarding p2p'
	},
	{
		name: COPY.routing.name,
		key: 'sites.routing',
		section: 'sites',
		help: COPY.routing.help,
		keywords: 'routing tunnel per site default connection vpn way out connect through'
	},
	{
		name: COPY.swapTunnels.name,
		key: 'sites.swap_tunnels',
		section: 'sites',
		help: COPY.swapTunnels.help,
		keywords: 'swap host join tunnel vpn port forwarding p2p exchange another sift guest'
	},
	{
		name: COPY.swapTunnels.join.name,
		key: 'sites.swap_join',
		section: 'sites',
		help: COPY.swapTunnels.join.help,
		keywords: 'swap join guest tunnel dial connect through vpn'
	},
	{
		name: COPY.unlock.name,
		key: 'sites.unlock',
		section: 'sites',
		help: COPY.unlock.help,
		keywords: 'secrets locked keys cookies logins tunnels password unlock after restart'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.cookies.row,
		key: 'sites.cookies-saved',
		section: 'sites',
		keywords: 'saved cookies sites login signed in'
	},
	{
		name: COPY.routing.defaultLabel,
		section: 'sites',
		keywords: 'default every site tunnel connection route direct'
	},
	{
		name: 'Unlock your saved keys, cookies and tunnels',
		section: 'sites',
		keywords: 'unlock password saved keys cookies tunnels locked restart'
	}
];
