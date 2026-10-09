import type { IconName } from '$lib/design/icons';
import type { IconUse } from './icon-uses';

/* The second half of `ICON_USES`, apart only for length. */
export const MORE_ICON_USES = {
	autostop: {
		what: 'Stop previewing',
		where:
			'The top bar, on the preview toggle, and only while it is on. The same button draws `autoplay` when it is off, so the shape says what pressing it does rather than repeating the state it is already in.'
	},
	view_array: {
		what: 'How the wall is divided',
		where:
			'The theater bar, on the panel that chooses the arrangement of cells. Columns side by side, which is what a layout IS here; the tile grid beside it in the rail is a wall of files.'
	},
	table_view: {
		what: 'Your Saved Layouts',
		where:
			'The theater bar, on the Saved Layouts panel. A ruled grid, which is what a Saved Layout IS \u2014 a wall divided into places with a source in each.'
	},
	grid_view: {
		what: 'The grid',
		where:
			"The rail's Browse, the size control on the top bar, and the folder browser's view switch."
	},
	browse: {
		what: 'The library itself',
		where: "The rail's own mark for the library screen."
	},
	group: {
		what: 'People, and shared with',
		where: 'The sharing panel, the appearances on a file, and the People verbs.'
	},
	policy: {
		what: 'A check on who can see something, rather than a decision about it',
		where:
			"The Visibility row, beside Share in every menu that offers it. The shield says it's about access, and the magnifier says it's a report. The sharing itself is decided by the two figures above it."
	},
	hide_image: {
		what: 'No picture',
		where:
			'A tile whose thumbnail could not be made, and a folder with nothing to show. Also the Left out filter, in the search box and on the filter panel.'
	},
	unknown_document: {
		what: 'Sift cannot find the file behind this \u2014 the library and the disk disagree',
		where:
			'The first mark on a tile, in the warning color, and the same mark on the file itself where the picture would be. Not the crossed-out eye beside it: that one is about what you have hidden, and this one is about what has gone.'
	},
	calendar_month: {
		what: 'A date',
		where:
			"The filter panel's Added column, on the control that opens the calendar. Not the history dial beside it in this list: that one is about what you did, this one is about when a file was imported."
	},
	history: {
		what: 'Recently viewed',
		where:
			"The rail's history, the search box's recent queries and a face group's past decisions. Also the mark on a picker row that is in front because it was picked recently."
	},
	info: {
		what: 'Something worth knowing',
		where: 'A plain toast, the details panel, the About section, and each figure on Insights.'
	},
	inbox: {
		what: 'Organize',
		where:
			'The rail\'s Organize, each of the queues inside it, and "Show the face group" on a face \u2014 which is where that row lands.'
	},
	insights: {
		what: 'Your own figures: what you viewed, organized and added',
		where: "The rail's Insights and the heading of that screen."
	},
	shoppingmode: {
		what: 'A tag somebody applied',
		where:
			"The rail's Tags, the tag filter, the tag verb, and the Part of column on the Tags wall. A label on a string, which is what a tag is. A search row falls back to it, so every glyph below exists to keep other filters from showing as tags."
	},
	camera_roll: {
		what: 'What KIND of thing the file is: video, picture or GIF',
		where:
			'The Media row on the search dropdown, where it names the media: filter. The question next to it — File type — is about the container instead, and wears a named format.'
	},
	file_png: {
		what: 'The container the file is in: mp4, mkv, webm',
		where:
			'The File type row on the search dropdown. A named format, against the roll of film beside it: one asks what the file IS, the other what is inside it.'
	},
	calendar_clock: {
		what: 'When something happens: a file being imported, or a task running',
		where:
			'The Added row on the search dropdown, and Settings > Tasks and Activity, in the Library group. A date with a clock on it rather than the plain calendar the date-range control wears — this one is a moment, not a span.'
	},
	'4k': {
		what: 'How big the picture is, by its shorter side',
		where:
			'The Resolution row on the search dropdown, which is asked in the same words the glyph is drawn in: 4k, 1080p, 720p and up.'
	},
	av1: {
		what: 'The video codec a file is encoded with',
		where:
			'The Video codec row on the search dropdown. One codec standing for the question rather than for its own answer, which is what a field mark is.'
	},
	audio_file: {
		what: 'The audio codec, and whether there is any sound at all',
		where:
			"The Audio codec row on the search dropdown. About the file's OWN sound, unlike the single note beside it, which is a track somebody set the file to."
	},
	titlecase: {
		what: 'The name somebody gave a file, which is not the name on disk',
		where:
			"The Title row on the search dropdown. File name is the other half of that pair and wears the files glyph, because it's about the file, not what it was called."
	},
	undereye: {
		what: 'Watched: you have opened it',
		where:
			'The Viewed row on the search dropdown. It has its own mark rather than the plain eye. The eye already does two jobs: the vault reveal, and the count of openings on a tile.'
	},
	mobile_rotate: {
		what: 'Which way up the picture is: landscape, portrait or square',
		where:
			'The Orientation row on the search dropdown. A handset being turned, which is the act that changes the answer rather than one of the three answers.'
	},
	left_panel_close: {
		what: 'Collapse the rail',
		where: 'On the top bar, when the rail is wide.'
	},
	left_panel_open: {
		what: 'Expand the rail',
		where: 'On the top bar, when the rail is icon-only.'
	},
	link: {
		what: 'A link',
		where: 'The address on a person or a Site, a pasted URL, and a drag carrying one.'
	},
	lan: {
		what: 'On this network',
		where: 'A mount that is reached over the network rather than off a local disk.'
	},
	language: {
		what: 'The public internet',
		where: "A download that used your own IP rather than a tunnel's."
	},
	private_connectivity: {
		what: 'Through the tunnel',
		where: "A download that used a tunnel's IP instead of your own."
	},
	open_in_full: {
		what: 'Back to full size',
		where: 'The mini player and the Audio player, to put it back into the full one.'
	},
	fit_screen: {
		what: 'Fit the panel to what is in it',
		where: 'Beside "make it big again" on the mini player, so a clip has no bars down its sides.'
	},
	lock: {
		what: 'Hidden',
		where: 'The vault filter, and a tile you may not open.'
	},
	match_case: {
		what: 'Match capitals',
		where: 'The case-sensitivity control on the search box.'
	},
	menu: {
		what: 'More, in a list',
		where: "The settings row's own menu, and the folder browser's."
	},
	more_horiz: {
		what: 'The steps folded away',
		where: 'The middle of a long breadcrumb trail, opening a list of the steps it stands for.'
	},
	more_vert: {
		what: 'More actions',
		where:
			"The three dots that open a menu, wherever there isn't room for the verbs themselves. On a phone's bar it's More: Settings and signing out."
	},
	pause: {
		what: 'Hold it there',
		where:
			"Every transport: the player, the mini player and the editor's preview. Also a download's own row in the queue, where it means the same for a transfer. The bytes already downloaded are kept, and the row waits for Play."
	},
	person_add: {
		what: 'Saying who a username belongs to',
		where:
			'The Usernames Waiting pile on Organize, where a username nobody is behind is joined to somebody.'
	},
	merge: {
		what: 'Two people who turn out to be one',
		where: "A person's own page, on the button that moves everything they hold onto somebody else."
	},
	compare_arrows: {
		what: 'Two answers about one field, and neither has won',
		where: 'Organize, on the pile of fields where a stash-box disagrees with the library.'
	},
	swap_horiz: {
		what: 'A swap with another Sift',
		where:
			'Settings > Tasks and Activity > App History, on the lines where a swap with another Sift started and where it ended and where something was kept out of swaps or let back in; the swap row of the Created by column; the Swap door in the Downloads header; the heading of the swap screen itself; and the top bar control that enters swap mode. The arrows pass each other because files can go across both ways; two arrows pointing at each other mean two answers that disagree.'
	},
	swap_horizontal_circle: {
		what: 'Picked for a swap',
		where:
			'Filled, in the accent, over the picture of a tile or a card picked in swap mode. The swap arrows in a disc, because a pick is marked with a filled shape and the bare arrows have none.'
	},
	link_off: {
		what: 'Taking that back',
		where:
			'A History line that took something off. The chain rather than a crossed-out person: what is undone is the join, and the person stays.'
	},
	person_check: {
		what: 'Sift identifies them',
		where:
			"The verdict under a person's cover and on their card, in the status green of the bar's band."
	},
	person: {
		what: 'A named person',
		where:
			"The rail's People, the Identify settings and their Identify now, Identify now under a file's Run task, a face group, and the person filter."
	},
	artist: { what: 'Who a song credits', where: 'The Artists column on the Music wall.' },
	picture_in_picture: {
		what: 'Open mini player',
		where: 'The player, the Audio player, Theater and the Remote.'
	},
	autoplay: {
		what: 'Play as you hover',
		where: "The grid's preview mode, and the theater's."
	},
	play_arrow: {
		what: 'Play',
		where:
			"Every transport, the play control over a still, and Resume on a paused download's row. Also a scheduled task's Run now, and Run task on a file's menu and a selection's bar. One shape for letting something continue, whether it's a picture, a transfer or a piece of work."
	},
	replay_5: {
		what: 'Back five seconds',
		where: "The player's transport. Drawn at 28 because the number is inside the glyph."
	},
	forward_5: {
		what: 'On five seconds',
		where: 'The same, the other way.'
	},
	fast_forward: {
		what: 'Playing faster than usual, for as long as the key is down',
		where:
			'A badge in the corner of a Theater cell while its number key is held on the second press. The rate is beside it \u2014 two, and then four once the hold has run on.'
	},
	fast_rewind: {
		what: 'Running backwards through the clip, for as long as the key is down',
		where:
			'The same corner of the same cell, on a number key held on the third press. It is the only control in Sift that moves a playhead backwards continuously.'
	},
	slow_motion_video: {
		what: 'Playing slower than usual, for as long as the key is down',
		where:
			'The same corner again, on a number key held on its first press. The ring of marks is the film-speed dial rather than a clock \u2014 it is about how the picture moves.'
	},
	public: {
		what: 'Site',
		where:
			"The rail's Sites, the Site filter, a download's origin, and the Site A-Z row of the Sort menu on Downloads."
	},
	restore_from_trash: {
		what: 'Start the count again',
		where: "The O counter's menu row that puts the count back to nothing."
	},
	schedule: {
		what: 'Later, and how long',
		where: 'A task waiting its turn, a duration, and a password rule about age.'
	},
	event_repeat: {
		what: 'Something that happens again, on a clock',
		where:
			'A task that ran, on App History, and the note when there are no recaps yet: what happens again, on a clock.'
	},
	search: {
		what: 'Search by words',
		where: "The search box, a saved search, and the Identify section's find-more control."
	},
	saved_search: {
		what: 'A search somebody kept',
		where:
			'The saved-filters prototypes under Design. Saved Filters live at the foot of the Filter panel, where their pills carry names, not this mark.'
	},
	settings: {
		what: 'How Sift is set up',
		where: "The rail's Settings, and the gear on the Downloads screen."
	},
	checklist: {
		what: 'Choose several',
		where:
			'The multi-select mode on the library screen; and the learning path, on its card and on the Get to know Sift settings section.'
	},
	arrow_split: {
		what: 'Any one of these will do',
		where:
			"On a filter chip carrying more than one value, saying they are alternatives. Its opposite, every one of them, is the ampersand beside it. That's a letter, not a glyph, because the icon set has no ampersand."
	},
	shuffle: {
		what: 'Random order',
		where: "The player's shuffle, the still viewer's, and the sort control."
	},
	repeat: {
		what: 'Loop the set',
		where: "The player's loop mode: go round the whole queue."
	},
	repeat_one: {
		what: 'Loop this one',
		where: "The player's loop mode: repeat this file forever."
	},
	stop_circle: {
		what: 'Stopped',
		where: 'A download that was canceled, on its row and on its badge.'
	},
	hearing: {
		what: 'Which cell you hear',
		where: 'On a theater cell, for the one whose sound is playing.'
	},
	interactive_space: {
		what: 'Theater',
		where: "The rail's Theater, its settings section, and the third tab on a phone's bar."
	},
	keyboard_double_arrow_up: {
		what: 'Bring up the sections',
		where:
			"The popout's Info press on a phone, which opens under the picture what Expand opens on the desk."
	},
	keyboard: {
		what: 'The keys',
		where: 'The shortcut list on the theater screen.'
	},
	all_inclusive: {
		what: 'Keep going',
		where:
			"The player's endless mode, and a theater cell set never to move on. Also the Loops screen on the rail, and the Loops column of the filter panel."
	},
	casino: {
		what: 'Pick at random',
		where: "The player's surprise-me, and a theater cell that chooses its own next clip."
	},
	home_storage: {
		what: 'Where the file is',
		where: "The player bar's mark for which library a file came off."
	},
	style: {
		what: 'Design',
		where: "The rail's link to this page."
	},
	image_search: {
		what: 'Similar to this',
		where:
			"The file menu's Similar to this row, and the Similar to this filter in the search box's list."
	},
	star: {
		what: 'Rating',
		where: 'The five stars, on a file, a card and the rating menu.'
	},
	skip_next: {
		what: 'Next in this cell',
		where: "A theater cell's menu."
	},
	skip_previous: {
		what: 'Previous in this cell',
		where: "A theater cell's menu."
	},
	sync: {
		what: 'Working on it, or going round again',
		where:
			'A job in flight, a download running, a folder being scanned. Also the button that closes Sift and opens it again after the graphics card runtime is installed.'
	},
	filter_alt: {
		what: 'Filter it',
		where:
			"The filter panel on the bar, and a theater cell's own filter. Filled, in the accent, over the picture of a card picked on an entity page's tab: that card is filtering the page's files."
	},
	filter_plus: {
		what: 'Keep these filters',
		where:
			"The filter row, at the end of the chips, where it saves the filters on screen as a Saved Filter. It's the funnel every filter control wears, with a plus. The thing kept is a filter, so it wears the filter mark, not a floppy disk, which means a file is written."
	},
	functions: {
		what: 'How many it holds',
		where: 'The count filter on the People and Sites walls.'
	},
	sort: {
		what: 'Order',
		where: "The sort control on the bar, the grid, and the theater's source picker."
	},
	undo: {
		what: 'Take it back',
		where:
			'Every Undo button on a thing just done: a History row, a board card, a filenames decision, a music source. A decision already made in an Organize queue wears the restore glyph instead.'
	},
	upload: {
		what: 'Upload a file',
		where:
			'The file picker on the Add panel, for a cookie export or a tunnel config. Also every Import button: a pack, a folder, a tunnel, a library, a Stash database. And the History line saying a library was created from a database file.'
	},
	visibility: {
		what: 'Show it',
		where: 'Revealing a password, unhiding a file, and the shown state on a tile.'
	},
	visibility_off: {
		what: 'Hide it',
		where: 'Hiding a password, concealing a file, and the hidden state.'
	},
	remove: {
		what: 'Discard it',
		where:
			'The Discard verb on a face and on a face group, and the Discarded tab under Organize > Faces. Not the bin, which deletes for good; a discarded face stays listed and can be restored.'
	},
	volume_off: {
		what: 'Muted',
		where: "The player bar, a theater cell, and the theater's tools."
	},
	volume_up: {
		what: 'Sound on',
		where: 'The same three, when it is not.'
	},
	warning: {
		what: 'Careful',
		where: 'The compress and edit sheets, and the player when something is not quite right.'
	},
	data_info_alert: {
		what: 'Something about this record is waiting to be read.',
		where:
			"Beside the History tab on a person, a Site or a tag, where a stash-box disagrees with the record. It isn't the triangle next to it, which means something went wrong. Two answers about one field is a question, not a fault."
	},
	wc: {
		what: 'Gender',
		where: 'The Gender facet on the People wall and on the Files wall, and its chip.'
	},
	face_3: {
		what: 'Hair color',
		where: 'The Hair color facet on the People wall and on the Files wall, and its chip.'
	},
	diversity_2: {
		what: 'Ethnicity',
		where: 'The Ethnicity facet on the People wall and on the Files wall, and its chip.'
	},
	flag: {
		what: 'Nationality',
		where: 'The Nationality facet on the People wall and on the Files wall, and its chip.'
	},
	eyeglasses_3: {
		what: 'Breast type',
		where: 'The Breast type facet on the People wall and on the Files wall, and its chip.'
	},
	people_size_increase: {
		what: 'How tall somebody is',
		where: 'The Height facet on the People wall and on the Files wall, and its chip.'
	},
	cake: {
		what: 'How old somebody is',
		where: 'The Age facet on the People wall and on the Files wall, and its chip.'
	},
	work_history: {
		what: 'Career',
		where: "The Career facet on the People wall: the year somebody's work began."
	},
	category: {
		what: "A tag's category",
		where: 'The Category facet on the Tags wall, and its chip.'
	},
	event: {
		what: 'The year a release came out',
		where: 'The Released facet on the Files wall, and its chip.'
	},
	alternate_email: {
		what: 'A username on a Site',
		where: 'The Usernames column on the Sites wall, and a username named in a History line.'
	},
	cognition_2: {
		what: 'Stats for nerds',
		where: 'The panel of machine facts over the picture, in the player and in a theater cell.'
	},
	clock_arrow_up: {
		what: 'Newest first',
		where:
			'The Sort menu on every wall. A clock is WHEN a file was imported; the arrow is which end of that you want, so the four time orders are one family read by shape and direction rather than four clocks to learn.'
	},
	clock_arrow_down: {
		what: 'Oldest first',
		where: 'The Sort menu on every wall, the other direction of the clock that orders newest.'
	},
	hourglass_arrow_up: {
		what: 'Longest first',
		where:
			'The Sort menu, where a wall holds things with a running time. An hourglass means how long, where the clock means when. The shape tells the two questions apart, and the arrow tells the two directions apart.'
	},
	hourglass_arrow_down: {
		what: 'Shortest first',
		where: 'The Sort menu, the other direction of the hourglass that orders longest.'
	},
	sort_by_alpha: {
		what: 'Name order',
		where: 'The Sort menu, on both of the two name rows — the words beside it say which way.'
	},
	data_usage: {
		what: 'How much room it takes',
		where: 'The Sort menu, on the largest and smallest rows.'
	},
	star_rate: {
		what: 'Highest rated first',
		where:
			'The Sort menu on the walls that carry a rating. The outlined star elsewhere is the control that SETS one.'
	},
	cinematic_blur: {
		what: 'PMV creator',
		where:
			"Right of the name on a PMV creator's own page, and in the same spot on a preview of them. Also the bottom-right corner of their card on the People wall, and the facet that filters to them. It uses the ordinary ink of a glyph, with no color of its own."
	},
	upgrade: {
		what: 'Updates and Info',
		where:
			"Settings > Updates and Info, in the list of sections. And the desktop app's title bar, left of minimise, for an admin while an update is available; it opens that section."
	},
	menu_book: {
		what: 'Documentation',
		where: "Settings > Documentation, under Updates and Info: Sift's own guide, readable offline."
	},
	movie: {
		what: 'Enriched by the music-video stash-box',
		where:
			'The Enriched by and Created by marks when PMVStash filled in the file. Shown on a file, a preview, an entity page and their facet rows.'
	},
	person_celebrate: {
		what: "Enriched by the creators' stash-box",
		where:
			'The Enriched by and Created by marks when FansDB filled in the file. Shown on a file, a preview, an entity page and their facet rows.'
	}
} satisfies Partial<Record<IconName, IconUse>>;
