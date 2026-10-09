import type { IconName } from '$lib/design/icons';

/*
 * What every icon in Sift means, and where you would come across it. Keyed by `IconName`, so a
 * glyph with no entry, or an entry for a removed glyph, does not compile.
 */
interface IconUse {
	/** What it means, in a few words. */
	what: string;
	/** Where in Sift you would see it. */
	where: string;
}

export const ICON_USES: Record<IconName, IconUse> = {
	cadence: {
		what: 'Open audio player',
		where:
			'The player and the mini player, left of Open mini player. The player shrinks to the Audio player: a bar that keeps the sound and drops the picture.'
	},
	hub: {
		what: 'A network of Sites',
		where:
			'Beside a Site name on its card and its page, when other Sites are part of it; the Network facet on the Sites wall and on the Files wall, and its chip; and the Share and Hidden dialogs, where a decision on a network reaches the Sites within.'
	},
	find_in_page: {
		what: 'Read what is in it',
		where:
			'The Read it button under Migrate from Stash. Sift reads a copy of the database and lists what it holds. Nothing is imported until you press Import.'
	},
	screenshot_region: {
		what: 'Screenshot',
		where:
			'The player drawer, on a video and on a picture. In the desktop app it opens a menu: this frame, the player, or the whole window. It is copied to the clipboard, or saved to a folder, as Settings > Playback > Screenshots says.'
	},
	person_remove: {
		what: 'Remove a name from a face',
		where:
			'Under Who is in this and on the faces of a person under Organize > Faces: the face stays, and only the name comes off it.'
	},
	face_left: {
		what: 'Turned away from the camera',
		where: "Beside a turned face's name, under a file's picture."
	},
	face: {
		what: 'Sift recognizes somebody BY this picture of them.',
		where:
			"At the foot of a face on one person's own wall under Organize > Faces. The wand beside it means Sift chose that name itself; this one means the picture is part of how it knows her."
	},
	help: {
		what: 'Nobody has answered for this face yet.',
		where:
			"At the foot of a face on one person's wall under Organize > Faces, on the faces in the tab that needs your input."
	},
	person_alert: {
		what: 'A name already on a file, and the face in it does not agree.',
		where:
			'The Disagreements tab under Organize > Faces, and its card on the board. The plain figure beside it means a person; this one means a question about one.'
	},
	view_column: {
		what: 'Names flowed down a column and then across, rather than one per line.',
		where:
			"Browse's folder view, as the middle of the three view buttons on the toolbar — between the plain list and the pictures. It is the one that fills the width of a wide window."
	},
	assignment_add: {
		what: 'Put what is on the clipboard into the library, without opening anything first.',
		where:
			'The slim half joined to the Add button on the top bar. The plain clipboard glyph beside it, in the Add panel, is the same act reached the longer way.'
	},
	gpp_maybe: {
		what: 'Quarantined: held at the door with something about it unresolved.',
		where: "A download's badge on the queue, and the same state on the Jobs list."
	},
	hard_disk: {
		what: 'A disk: where a folder sits, and how much room a file takes on one',
		where:
			'Beside a folder in the library list, opposite the network mark on a NAS folder. Also the File size row on the search dropdown and the File size column on the facet panel. One glyph for both on purpose: both are about room on a disk.'
	},
	apps: {
		what: 'All of them, with nothing filtered out.',
		where: "The Jobs screen's All tally, beside the tallies for each single state."
	},
	playlist_add_check: {
		what: 'Queued: waiting its turn, with nothing wrong.',
		where: "A job's badge on the Jobs list, and a download's badge on the queue."
	},
	check_circle: {
		what: 'Done: the work finished and there is nothing to do about it.',
		where: "A job's badge on the Jobs list, and a download's badge on the queue."
	},
	do_not_disturb_on: {
		what: 'Blocked: stopped, waiting for a person rather than for the machine.',
		where:
			"A job's badge on the Jobs list, wherever a job cannot go on by itself; Don't swap in a menu; and in swap mode, the mark on a card or a tile that will not go."
	},
	chronic: {
		what: 'Waiting for cookies: time passing while a Site asks who is asking.',
		where: "A download's badge on the queue, on the one blocked state a download can have."
	},
	cookie: {
		what: 'The cookies a Site was given, so Sift may download from it as you.',
		where:
			'The Add cookies button on a blocked download, the Cookies door on the Downloads page, and the Sites section of Settings. Beside the waiting glyph on the same row, which says a Site is asking and this says what it is asking for.'
	},
	cancel: {
		what: 'Failed: the work was attempted and did not work — or, outlined, taking something out.',
		where:
			"A job's badge on the Jobs list, a download's badge on the queue, and (outlined) the Remove from Sift card on the delete sheet."
	},
	water_drop: {
		what: 'The O counter — how many times you have pressed it on this file.',
		where:
			"The file's action row, beside the heart and the stars. Also the O count column in the filter panel, and the mark on a tile beside the watched count."
	},
	toll: {
		what: 'Already in the library: two of the same thing, so this one was not added again.',
		where: "A download's badge on the queue, on a link that turned out to be a repeat."
	},
	photo_library: {
		what: 'A Photo Set: the pictures imported together from one shoot.',
		where:
			'The rail, the Photo Sets wall and the tab for them on every entity page. Also the Photo Sets column of the filter panel, and the Has a cover photo column on the walls of things.'
	},
	newsstand: {
		what: 'Library: every kind of thing the files are grouped into',
		where:
			"The second tab on a phone's bar and the title of the screen it opens: Recently viewed, People, Sites and the rest, one row each."
	},
	settings_remote: {
		what: 'Remote: drive what is playing at the desk from a phone',
		where:
			"The title of the Remote's screen, which lists the signed-in person's open players and Theater walls with the controls for each; and, in the accent, over the Filter on Theater's bar while a phone is driving that wall."
	},
	connected_tv: {
		what: "A Theater wall sent between two of a person's devices, as older History lines record it",
		where: "The mark of a wall sent between two devices on a file's History."
	},
	bookmark_add: {
		what: 'Keep the stretch marked on the timeline, as a row on the Loops screen.',
		where: "The player's control row, once both ends of a loop are marked."
	},
	palette: {
		what: 'Appearance',
		where: 'The Settings section for how the app looks.'
	},
	shield_person: {
		what: 'Privacy and Security',
		where: 'The Settings section for the vault, hidden files and what leaves this machine.'
	},
	supervised_user_circle: {
		what: 'User Management',
		where: 'The Settings section for who may sign in and what each of them may see.'
	},
	readiness_score: {
		what: 'Performance',
		where:
			'The Settings section for how hard this machine is working and how well it is keeping up.'
	},
	mop: {
		what: 'Maintenance',
		where: 'The Settings section for tidying up: duplicates, orphaned files, rebuilding an index.'
	},
	settings_backup_restore: {
		what: 'Backup and restore',
		where:
			'The Settings section for taking a copy of the catalog and putting one back, and the History line saying a backup was restored.'
	},
	video_settings: {
		what: 'Playback',
		where: 'The Settings section for how video is played and converted.'
	},
	videocam: {
		what: 'This cell plays one clip',
		where: "On a theater cell's drawer, for the source that is a single file."
	},
	video_camera_back_add: {
		what: 'This cell plays a set',
		where: "On a theater cell's drawer, for the source that is a search or a collection."
	},
	timer: {
		what: 'How long before it moves on',
		where: "On a theater cell's drawer and its menu, beside the dwell time."
	},
	account_circle: {
		what: 'You, and signing out',
		where: "The rail's link to your own profile, and the sign-out menu it opens."
	},
	add: {
		what: 'Add something',
		where:
			'The Add button on the top bar, the tag adder and every control that creates something new. Also the menu row that holds the places a file can be filed.'
	},
	arrow_back: {
		what: 'Back',
		where:
			'The back control at the top of a face group, a collection and a settings section on a phone.'
	},
	arrow_downward: {
		what: 'Sort descending, and move down',
		where:
			"On the rail while rearranging it, and on a collection's ordering. Also on an entry of an ordered list in a record's form, such as a song's artists."
	},
	arrow_forward: {
		what: 'Go there',
		where: 'On a search suggestion that LEAVES the screen rather than filtering it.'
	},
	arrow_upward: {
		what: 'Move up',
		where:
			"On the rail while rearranging, and on a download being pushed up the queue. Also on an entry of an ordered list in a record's form, such as a song's artists."
	},
	auto_awesome: {
		what: 'Search by meaning',
		where:
			"The spark on the search box and the Smart Search settings section. It isn't a magnifying glass: a plain search reads the words, and this one reads what they mean."
	},
	music_note_2: {
		what: 'A song, the piece of music a file is set to',
		where:
			"The Music page in the sidebar, a Music tab and a song's own page. Also a song with no cover, on its card, its chip and its hover card. Also the Music settings section, a song row on the search dropdown, Song under a file's Add to, and AcoustID under Auto-enrich. It's a beamed pair rather than a speaker, because it names a piece of music, not sound playing."
	},
	auto_fix_high: {
		what: 'Auto-enriching from a stash-box',
		where:
			'The Auto-enrich verb on the People, Sites and Tags walls and on a library folder. Also the pile waiting to be enriched under Organize, and the Stash-boxes button that does the whole library. A wand: it fills things in without asking.'
	},
	backlight_low: {
		what: 'Enrich: ask a stash-box about this one thing, and choose',
		where:
			"A file's own menu, the Options menu on a person's or a Site's page, and the heading of the Enriched by column. It's the sister of Auto-enrich: it opens the chooser with the name already filled in, for the cases the wand left to you. On the column it names a state, not a verb: what enrichment has already done."
	},
	inventory_2: {
		what: 'Enriched by a stash-box',
		where:
			"The mark on the Enriched by facet's stash-box row. A stash-box's answer was applied to the file, or it put a person or a tag on it. Also the heading of the Created by column on every wall."
	},
	person_search: {
		what: 'Look for faces again',
		where:
			"Drawn nowhere: a file's three-dot menu reaches this act as Faces under Run task, Identify now, which wears the stage's glyph. Kept until the icon font is cut again."
	},
	familiar_face_and_zone: {
		what: 'Enriched by Sift, from a face',
		where:
			"The mark on the Enriched by facet's faces row. Sift matched a face on the file, or proposed one and somebody agreed."
	},
	folder_supervised: {
		what: 'Enriched by Sift, from a folder name',
		where:
			"The mark on the Enriched by facet's folder row. Sift read the folder's name and filed the file under that person without asking."
	},
	quick_reference: {
		what: 'Enriched by Sift, from file metadata',
		where:
			"The mark on the Enriched by facet's metadata row, and on the History line of a file this pass filed. Sift read two fields inside a few of one username's files, Artist and ImageDescription. That told it which username the ID in the file's name belongs to. It says where the copy came from, never who is in it."
	},
	position_bottom_right: {
		what: 'Enriched by Sift, from a watermark on the picture',
		where:
			"The mark on the Enriched by facet's watermark row, on Settings > Watermarks, and on the History line of a file this pass read. Sift read the site's own address printed on the frame \u2014 in the bottom-right corner the glyph draws, or the band along the foot \u2014 and filed the file under that site. It says where the COPY came from \u2014 never who is in it."
	},
	document_scanner: {
		what: "Enriched by Sift, from the file's own name",
		where:
			"The mark on the Enriched by facet's filename row, and on the Enriched from filenames card. Sift read the Site's own signature in the filename and filed the file under that username. It's a page under a reader, not a pen: this pass renames nothing. The name was read, not written."
	},
	error_med: {
		what: 'Generate: make the pictures and fingerprints that are missing',
		where: "Generate now under a file's Run task, with Generate all and every pass under it."
	},
	split_scene: {
		what: 'Scan: read the folders for new files',
		where:
			"A library folder's Scan now, and Scan now under a file's Run task with Scan all and its pass. The refresh spinner on the rail and the re-grouping of faces are a different act — look again — and wear the arrows."
	},
	expand_circle_up: {
		what: 'There is more down here',
		where:
			'The sliver at the foot of a wall in Theater, while its bar is down — pointing at it brings the bar up.'
	},
	bolt: {
		what: 'It happens automatically',
		where:
			'The wall drawer in Theater, on the Stage View mode — lit when a preview comes up the moment it starts something new. The hand beside it is the other half of the same control.'
	},
	bolt_boost: {
		what: 'Turbo mode',
		where:
			"The sidebar's bolt, above the rule, in yellow. Background work is in turbo mode, using all of this device although it's in use, because you pressed for it. On a phone, the same row heads More."
	},
	energy_savings_leaf: {
		what: 'Using less of this device',
		where:
			"The sidebar's leaf, above the rule, in green: background work using a share of this device, a quarter unless you chose otherwise, because it's in use. Pressed, it becomes the bolt; on a phone, the same row heads More."
	},
	touch_app: {
		what: 'It happens when you double-click it',
		where:
			'The wall drawer in Theater, on the Stage View mode — the mode where a preview waits to be pressed. The bolt is the other half of the same control.'
	},
	block: {
		what: 'Turn off',
		where: "Turning a guest's sign-in off, in User Management."
	},
	group_off: {
		what: 'Restricted',
		where:
			'The state, wherever it is shown rather than set: the group glyph with a line through it.'
	},
	cached: {
		what: 'Look again',
		where: "Re-running the face grouping, and the Application log's Refresh."
	},
	call_split: {
		what: 'Split this apart',
		where: 'Taking faces out of a group, and separating a suggestion from the pile it was found in.'
	},
	groups: {
		what: 'A whole pile of faces, as what a verb is aimed at',
		where: 'The "All N in this group" half of the split rows on a pile\'s own screen.'
	},
	check: {
		what: 'Yes, done, chosen',
		where: 'The tick in a chooser, a met password rule, and a confirmed action.'
	},
	done_all: {
		what: 'Every outcome shown here has been seen.',
		where:
			'The right-click menu of the Downloads row on the rail, as Mark downloads as seen. Two ticks rather than one, because it acknowledges every finished or failed download the dot is lighting.'
	},
	check_box: {
		what: 'A setting in a menu that is switched ON.',
		where:
			"A menu row that is a preference rather than an act, such as Compact rows in the Jobs screen's Options. It isn't the tick beside it. A tick that comes and goes shifts the words next to it, and a box that fills or empties doesn't."
	},
	check_box_outline_blank: {
		what: 'The same setting, switched off.',
		where: 'Beside its filled twin above, on the menu rows that are preferences.'
	},
	chevron_left: {
		what: 'Previous',
		where: "The player's previous control, and the facet panel's scroll-left."
	},
	chevron_right: {
		what: 'Next, and deeper',
		where:
			"The player's next, a folder that opens, a submenu, and the separator between breadcrumbs."
	},
	keyboard_arrow_right: {
		what: 'Shut, and pointing at what opens when you press it.',
		where:
			'Every disclosure in the app — the mark in front of a summary, which turns to point down while the thing it names is open.'
	},
	first_page: {
		what: 'All the way back',
		where:
			"The pager's jump to the first page. A chevron against a bar, which is what every transport uses."
	},
	last_page: {
		what: 'All the way forward',
		where: "The pager's jump to the last page."
	},
	close: {
		what: 'Close, clear, remove',
		where: 'Every dismiss: a dialog, the player, a chip being taken off, a selection being cleared.'
	},
	box: {
		what: 'A collection',
		where:
			"The rail's Collections, the collection wall, the search dropdown, and the verb that puts something into one."
	},
	content_copy: {
		what: 'Copy this',
		where:
			'Copying a link to a file, what this machine is out of Performance, a password, a bug report and a duplicated library.'
	},
	content_paste: {
		what: 'Take what is on the clipboard',
		where: 'The Paste button in the Add panel, where reading the clipboard is possible at all.'
	},
	aspect_ratio: {
		what: 'The shape of the box, rather than of what is in it',
		where:
			"A Theater cell's menu, where it opens the list of shapes one cell can be held to. One of them is Dynamic, the shape of whatever it is playing."
	},
	crop: {
		what: 'Crop the picture',
		where: 'In the editor, and on the compress sheet where a size is being chosen.'
	},
	content_cut: {
		what: 'Take a clip out',
		where:
			"The editor's clip panel, the player's control for saving the marked stretch as a clip, and the mark on something made from another file."
	},
	gif: {
		what: 'Turn it into a GIF',
		where: "The editor's clip panel, beside the lengths. Which format it writes is a setting."
	},
	rotate_left: { what: 'Turn it left', where: "The editor's picture panel." },
	rotate_right: { what: 'Turn it right', where: "The editor's picture panel." },
	flip: { what: 'Mirror it', where: "The editor's picture panel." },
	delete: {
		what: 'Delete for good',
		where:
			'The destructive verb, on a file, a person, a collection and a saved search. Always last in a menu.'
	},
	download: {
		what: 'Download it from a Site',
		where: "The rail's Downloads, the copy-out control, and anything downloading from a Site."
	},
	drag_indicator: {
		what: 'Drag this',
		where: 'The grip on a rail item being rearranged.'
	},
	edit: {
		what: 'Change this',
		where:
			'Whatever is neither a rename nor the editor — changing a saved search, a note, a record.'
	},
	edit_square: {
		what: 'Give this a different name',
		where:
			'Every Rename row in Sift: a file, a folder, a person, a user, a saved search, a wall. It is the one glyph that means a NAME is changing and nothing else about the thing is.'
	},
	gif_box: {
		what: 'Create a GIF from this clip',
		where: "Its own row on a file's Options menu, beside Trim rather than inside it."
	},
	article: {
		what: 'A file',
		where:
			"A folder's count in the explorer, the Files tab on every entity page, the Files figure on a person's hover card, and the Filename row on the search box. Not `news`: a folded newspaper is a publication, and at 14px the fold is all there is of it."
	},
	diversity_3: {
		what: 'Who else is in these files',
		where: "The Seen with tab on a person's page and its figure on the hover card."
	},
	design_services: {
		what: 'Change what is in the file itself',
		where:
			"The Modify row on a file's own menu, and the editor it opens. The drafting tools rather than the pen beside it, which only ever changes a name. Also the Created by line of a tag Sift made for a copy it edited, on the tag's page and hover card."
	},
	error: {
		what: 'Something failed',
		where: 'A toast that reports a failure, a file that would not probe, a download that gave up.'
	},
	expand_less: {
		what: 'Fold this up',
		where: "A download row's details, and any section that can be collapsed."
	},
	expand_more: {
		what: 'Open this',
		where: "A select's chevron, a folder list, a download's details."
	},
	favorite: {
		what: 'On the shortlist',
		where:
			'The heart, on a tile, on a card, and on a person, and the Favorites column of the filter panel on a wall of files. A shortlist, not a rating.'
	},
	keep: {
		what: 'Pinned to the top of its wall',
		where:
			'The pin, on a person, a Site, a collection, a tag or a Photo Set. It says WHERE a row sits; the heart beside it says what you think of it.'
	},
	keep_off: {
		what: 'Unpin',
		where:
			'The same pin lying down, on the menu row of something already pinned. The verb reads as the opposite of Pin, not as the same word twice.'
	},
	compress: {
		what: 'Compress it',
		where:
			"The convert verb on a file, and the compress sheet. Also the Created by line of a tag Sift made for a copy it compressed, on the tag's page and hover card."
	},
	draft: {
		what: 'What Sift wrote down about what it did',
		where:
			'The Settings section for the log. It does not count files: `article` is the one glyph for a file, app wide, so the folder label and the entity tabs say it the same way. This one is the BLANK sheet, which is a log nobody has opened rather than a document.'
	},
	folder: {
		what: 'A folder on disk',
		where:
			'The library tree, the folder filter and the destination on the Add panel. Also the library folder row of the Created by column.'
	},
	create_new_folder: {
		what: 'Create a folder on your disk and in your library',
		where: 'The explorer on Browse, and the folder tree in Settings.'
	},
	drive_file_move: {
		what: 'Move a folder somewhere else in the same library',
		where: "A folder's own menu in the explorer on Browse."
	},
	file_copy: {
		what: 'Exact Duplicates: the same file, byte for byte, in more than one place.',
		where: 'Its card on the Organize board, and the screen behind it.'
	},
	shield: {
		what: 'Quarantine: files Sift refused on the way in and moved somewhere of its own.',
		where: 'Its card on the Organize board, and the screen behind it.'
	},
	rule_folder: {
		what: 'Skipped Files: files in your own folders that Sift read and would not take.',
		where: 'Its card on the Organize board, and the screen behind it.'
	},
	fullscreen: {
		what: 'Full screen',
		where: 'The player, the pictures, and Theater.'
	},
	fullscreen_exit: {
		what: 'Leave full screen',
		where: 'The same places, once you are in it.'
	},
	close_fullscreen: {
		what: 'Put the identity band on an entity page away',
		where: 'Right of Options on every entity page, paired with open_in_full to bring it back.'
	},
	slideshow: {
		what: 'What a cell plays',
		where:
			'The theater bar, on the panel choosing which files each cell draws from. Not the funnel beside it: that one filters a wall of FILES by the query language, and it is dimmed on the theater screen.'
	},
	save: {
		what: 'A copy kept on your device',
		where:
			'Every Save button, where something is written down to keep, and the History row for a file saved to your device. Saved Filters use the funnel with a plus instead. A disk says a file is written, and a Saved Filter keeps a filter, not a file.'
	},
	publish: {
		what: 'Send a copy out of Sift',
		where:
			'Every Export button, such as the Export of a pack of faces under Settings > Faces. An arrow leaving the tray: what is exported is taken somewhere else, where a disk says it is kept here.'
	},
	bookmark_stacks: {
		what: 'The filters you have kept',
		where:
			'Not drawn on any screen now. A filter is kept with the funnel with a plus, at the end of the filters bar. The kept ones are listed at the foot of the Filter panel.'
	},
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
};
