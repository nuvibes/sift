// SPDX-License-Identifier: AGPL-3.0-or-later
/* What a downloaded file is named and where it lands: the words, and what somebody can type to
 * find them. Drawn on Downloads, per Site as well as once for everything, which is why it is a
 * table rather than a setting.
 *
 * ONE COPY MODULE PER PANE. `NamingTemplate.svelte` and the name builder inside it,
 * `NameTemplateField.svelte`, draw every word they add from `COPY`, and the search entries are built
 * from the same object. "Download folder" is the row every download follows, and a Site's own
 * card uses the same words for the one it follows instead.
 */
import type { Searchable } from './search';
import { NO_DOWNLOAD_FOLDER } from '$lib/library/destinations.svelte';

export const COPY = {
	heading: 'Folders and file names',
	name: 'Name template',
	help: 'What a downloaded file is called, and which folder it goes in.',
	cannotLoad: "Couldn't load these settings. Refresh the page to try again.",
	notSaved: "Couldn't save that",
	notUndone: "Couldn't undo that",
	folder: 'Download folder',
	folderHelp:
		"Where a download lands when you don't choose a folder for it. A folder outside your library is added to your library, and whatever is already in it is imported.",
	choose: 'Choose a folder',
	/** The sheet a browser chooses the folder in: the folders Sift has, as Add a folder offers. */
	picker: {
		title: 'Choose the download folder',
		list: 'Folders Sift has',
		help: 'Click through to the folder you want, then choose it.',
		use: 'Use this folder',
		cancel: 'Cancel',
		atTop: 'Click into a folder first: this is the list of folders Sift has, not a folder itself.'
	},
	siteFolder: 'Download folder',
	defaultFolder: 'Download folder',
	/** The folder row with nothing set. Shared with every chooser that offers the default, so
	 *  Settings and the Downloads screen never describe the unset state differently. */
	notSet: NO_DOWNLOAD_FOLDER,
	perSite: {
		name: 'Per-Site settings',
		help: "Each Site downloads into the download folder above and names its files Sift's way, with the downloader Sift chooses for it. You can give a Site settings of its own."
	},
	noneYet: 'No Site has its own settings yet.',
	addSite: 'Give a Site its own settings',
	/* A Site card's three answers each follow something different when the Site has none of its
	   own: the folder follows the row above, the name is the one Sift ships for that Site, and the
	   downloader is Sift's choice (the server's word for it, "Sift"). So no press or option on the
	   card says "default", which would mean all three: each names what it follows. */
	/* Short enough for the row's control column, where a longer phrase would be cut short. */
	followFolder: 'The download folder above',
	reset: 'Remove its own settings',
	resetSite: (site: string) => `Remove the settings for ${site}`,
	afterReset: (site: string) =>
		`Without settings of its own, ${site} uses the download folder above, Sift's default name and the downloader Sift chooses.`,
	folderFor: (site: string) => `Download folder for ${site}`,
	downloader: 'Downloader',
	downloaderFor: (site: string) => `Downloader for ${site}`,
	/** The box for the rule every address Sift has no Site for follows. Every Site Sift knows has
	 *  a name of its own, so this rule reaches no Site, and its line has to say so or a rule typed
	 *  here reads as ignored on TikTok. */
	otherAddresses: 'Name template for other addresses',
	otherAddressesHelp:
		'For addresses Sift has no Site for. Each Site names its files its own way, which you can change under Per-Site settings.',
	selectSite: 'Select a Site',
	field: {
		presets: 'Name presets',
		presetsHelp: 'A ready-made rule. Choosing one fills the box above.',
		presetsFor: (site: string) => `Name presets for ${site}`,
		ownRule: 'Your own rule',
		keep: "The Site's own file name",
		siftsName: "Sift's default name",
		tokens: 'Choose one to add it to the name.',
		tokensFor: (site: string) =>
			`Choose one to add it to the name. These are the words ${site} can fill.`,
		/* Which of the three states a Site's row is in. No rule and keep are both an empty box, so
		   the line under the label is what tells them apart. */
		shippedRule: (site: string, rule: string) =>
			`Sift names ${site} files ${rule} unless you type a rule.`,
		shippedKeep: (site: string) =>
			`Sift keeps the name ${site} gave each file unless you type a rule.`,
		chosenKeep: (site: string) => `Keeps the name ${site} gave each file, as you chose.`,
		typedRule: (rule: string) =>
			`Your rule. Clear the box to go back to Sift's default name, ${rule}.`,
		typedKeep: (site: string) =>
			`Your rule. Clear the box to keep the name ${site} gave each file.`,
		wouldBe: 'A file would be called',
		keeps: 'Files keep the name the Site gave them.'
	}
} as const;

export const SEARCHABLE: Searchable[] = [
	{
		name: COPY.folder,
		key: 'downloads.default_folder',
		section: 'downloads',
		help: COPY.folderHelp,
		keywords: 'download folder location where downloads go save destination directory choose path'
	},
	{
		name: COPY.name,
		key: 'downloads.name_template',
		section: 'downloads',
		help: COPY.help,
		keywords:
			'filename pattern rename folder subfolder organize organise per site title date download location'
	},
	/* Every other name the pane draws, so the search finds each (`check_settings_search_covers_panes.js`). */
	{
		name: COPY.field.presets,
		section: 'downloads',
		keywords: 'name presets file name rule template ready made'
	},
	{
		name: COPY.heading,
		section: 'downloads',
		keywords: 'folders file names naming where downloads land'
	},
	{
		name: COPY.perSite.name,
		section: 'downloads',
		keywords: 'per site settings own folder naming method each site'
	},
	{
		name: COPY.downloader,
		section: 'downloads',
		keywords: 'downloader method tool service fetch'
	},
	{
		name: COPY.addSite,
		section: 'downloads',
		keywords: 'add site own settings per site override'
	}
];
