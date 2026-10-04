// SPDX-License-Identifier: AGPL-3.0-or-later
/* Which programs do the downloading and which version of each is running: the words, and what
 * somebody can type to find them. Facts rather than settings, so nothing in the registry names
 * them; somebody chasing a failed download types the tool's name, or "version", or "out of date".
 *
 * ONE COPY MODULE PER PANE. `DownloadTools.svelte` draws every word it adds from `COPY`, and the
 * search entry is built from the same object. */
import type { Searchable } from './search';

export const COPY = {
	name: 'Download tools',
	help: "The programs Sift downloads with, and the version of each that's running.",
	/** What each tool is for, keyed by the server's key so a tool added later still draws. */
	purpose: {
		ffmpeg: "Joins a video's picture and sound, and generates every preview.",
		'yt-dlp': 'Downloads videos from most Sites.',
		'gallery-dl': 'Downloads galleries of photos.',
		'js-runtime': "yt-dlp runs some Sites' checks in it. Without one, YouTube offers fewer formats."
	} as Record<string, string>,
	shipped: 'Shipped with Sift.',
	missing: 'Not found on this device.',
	devicesOwn: "This device's own copy, because Sift didn't find its own.",
	engine: 'JavaScript engine',
	noneFound: 'None found',
	notResponding: 'Not responding',
	check: 'Check',
	checkLabel: 'Check for a newer yt-dlp',
	checkHelp:
		'Sites change how they serve their files, and a newer yt-dlp is often what fixes a download that stopped. Sift only looks when you choose Check.',
	cannotAsk: "Couldn't check the tools.",
	checking: 'Checking\u2026',
	cannotCheck: "Couldn't check. Try again in a moment.",
	unreachable: "Couldn't reach yt-dlp's release list. Try again later.",
	newer: (version: string) =>
		`yt-dlp ${version} is out, newer than this one. It arrives with the next Sift update.`,
	newest: (version: string) => `This is the newest yt-dlp (${version}).`
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.name,
		key: 'updates.download_tools',
		section: 'updates',
		help: COPY.help,
		keywords:
			'yt-dlp ytdlp gallery-dl ffmpeg quickjs javascript engine version versions out of date outdated update newer check tools programs'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.checkLabel,
		key: 'updates.download_tools.yt-dlp',
		section: 'updates',
		keywords: 'yt-dlp newer version update downloader tool check'
	}
];
