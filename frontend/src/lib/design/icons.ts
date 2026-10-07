/* The icons the app is allowed to use: the build cuts the icon font down to exactly these, and
 * `as const` makes a name that is not here a type error. */
export const ICON_NAMES = [
	/* The Settings sections' own glyphs. A section's icon is the only thing telling them apart
	   at a glance in a list of thirteen, so the nearest general-purpose glyph (a tuning slider
	   for Performance, a recycle arrow for Maintenance) costs more than it saves. */
	'palette',
	'shield_person',
	'supervised_user_circle',
	'readiness_score',
	'mop',
	'settings_backup_restore',
	'video_settings',
	'menu_book',
	/* A newer Sift waiting, on the desktop app's title bar. */
	'browser_updated',
	/* A Theater cell's drawer says what it plays and when it moves on with icons rather than words:
	   a row of three selectors is most of the drawer, and the words in them are longer than the
	   controls beside them. */
	'videocam',
	'video_camera_back_add',
	'timer',
	/* And what SHAPE a cell is, which is the same kind of question one row further out: the shape of
	   whatever it happens to be playing is one answer of several. */
	'aspect_ratio',
	'account_circle',
	'add',
	/* Paste a link straight into the library, from the trailing half of the Add button. The
	   clipboard glyph beside it is the one the panel already uses for the same act; this is the
	   one that says it lands somewhere. */
	'assignment_add',
	'arrow_back',
	'arrow_downward',
	/* On a suggestion that LEAVES the page rather than filtering the box. Two kinds of row on
	   one list have to be told apart before they are pressed, not after. */
	'arrow_forward',
	'arrow_upward',
	/* Restrict, in the sharing panel. The no-entry ring rather than a crossed-out eye: what a
	   restrict does is refuse access, not conceal something that is otherwise reachable. */
	/* Searching by meaning, on the search box. The four-pointed spark rather than a magnifying
	   glass with something added: the plain glass already means search, and this control changes
	   what searching MEANS rather than doing more of it. */
	'auto_awesome',
	/* Auto-enriching from a stash-box: the wand. The queue's card, the verb and the sweep button
	   all wear it so the pile and the action that fills it are recognisably one thing. */
	'auto_fix_high',
	/* Enrich: the file's own verb, which opens the chooser with the name already in it. The
	   backlight: a magnifying glass already means search. */
	'backlight_low',
	/* The four ways a file comes to be enriched without a person doing it, as the marks on the
	   Enriched by facet's rows and on a card. Four glyphs because they are four different
	   authors: a stash-box, Sift reading a face, Sift reading a folder name, Sift reading the
	   file's own name.

	   The last one is the document scanner: a page passing under a reader. Not a file with a
	   pen (`drive_file_rename_outline`): there is a glyph for reading, and a pen on a page is
	   the one thing this pass never does. Nothing is renamed by it. */
	'inventory_2',
	/* PMVStash's and FansDB's own marks, so each stash-box is told apart by its glyph. */
	'movie',
	'person_celebrate',
	/* The two marks a face card wears at its foot. `face` says Sift recognizes this person BY
	   this picture: a face, because that is what it is; the four-pointed spark means "clever"
	   and is the search box's word. `help` is the face nobody has answered for yet: a question
	   mark, which is the only thing it is. The third mark on that card is `auto_fix_high`,
	   already here for the enrichment wand. */
	'face',
	'help',
	/* A face turned too far from the camera for Sift to name it alone: the face in profile, beside
	   the name on Who is in this. */
	'face_left',
	/* The Disagreements tab's own icon, named by the server: a person with a warning beside
	   them, which is exactly what the tab holds. The declared-icons gate refuses a name the
	   server uses and this list does not declare. */
	'person_alert',
	'familiar_face_and_zone',
	/* A file's own "Look for faces again": a person with a magnifier. Not drawn at present; kept
	   in the subset until it is cut again. */
	'person_search',
	'folder_supervised',
	'document_scanner',
	/* The fifth: Sift read a site's own mark printed on the picture. A frame with a small solid
	   block in its BOTTOM-RIGHT corner (which is where a site stamps its address and the
	   corner this pass reads, beside the bottom band), for every place a watermark is drawn:
	   the Enriched-by mark, the History line, the facet row and the Settings section, so the
	   pane and the mark on a file say the same thing. The corner is where the mark is, and this
	   one draws nothing but the corner.

	   One name in five places: the `IconName` type refuses a name used and not declared, and
	   `icon-uses.ts` one declared and not described. */
	'position_bottom_right',
	/* The sixth Enriched-by mark: Sift read the FILE'S OWN description of itself (two embedded
	   fields and no others) to find out what the Site's number for a username in a file's name
	   is called. A page with a magnifier on it: what was read is the record carried inside the
	   file, so the mark is the record being looked up rather than the picture being looked at
	   (not `image_search`, a magnifier over a PICTURE). NOT `data_info_alert`, which sits one
	   screen away meaning a stash-box disagrees with a record, and NOT `document_scanner`, which
	   is the row beside this one and means the file's NAME was read. */
	'quick_reference',
	/* A library scan: reading folders for what has arrived. The folder row's Scan now and the
	   Importing pane's. Not a refresh and not "group the faces again": those are "look again"
	   and keep `cached`. A run of frames being divided up, which is nearer what
	   a scan does to a folder than the bare viewfinder frame is. */
	'split_scene',
	/* Generate: making the pictures and fingerprints for the files already here. The Generate
	   now row on the Importing pane. */
	'error_med',
	/* Center stage's two modes, on the wall's own drawer: a hand for 'it comes up when you press
	   it', a bolt for 'it comes up the moment it starts something new'. The pair has to read as two
	   states of ONE control, which is why they are a gesture and an event rather than, say, a hand
	   and a clock. */
	/* The handle at the foot of a wall in a window: a sliver of the bar's surface with this on it,
	   saying the controls are down here and come up when you point at them. */
	'expand_circle_up',
	'bolt',
	/* The rail's bolt: turbo mode, all of this device pressed for although it is in use. */
	'bolt_boost',
	/* The rail's leaf, its other state: background work using less of this device because it is in
	   use. A leaf for consideration rather than a gauge for a number: what it says is that Sift is
	   keeping out of the way, not by how much. */
	'energy_savings_leaf',
	'touch_app',
	'block',
	/* A track, on a search suggestion and wherever a file's music is named. The single note
	   rather than a pair or a speaker: a speaker is sound coming out, and what this names is a
	   piece of music the file is set to. */
	'music_note_2',
	/* Restrict, wherever it is shown as a state rather than as the button that sets it.
	 *
	 * The crossed-out pair of figures, which is the `group` glyph with a line through it. So the
	 * two marks a tile can carry are the same picture, one of them negated, and the pair reads at a
	 * glance instead of asking somebody to learn two unrelated symbols. Filled when the decision was
	 * made on this file, outlined when it comes from something above it. */
	'group_off',
	/* "Look again": a refresh, a scan run now. A processing circle, distinct from `sync`'s two-arrow
	   refresh, which reads as "reload" rather than "working". */
	'cached',
	/* Moving faces between groups: merging some into another, or splitting them out into one of
	   their own. The forking arrow says "these go elsewhere" and covers both directions, which is
	   right: they are one operation with two destinations. */
	'call_split',
	/* A whole PILE as the thing a verb is aimed at: the "All N in this group" half of the
	   split rows on a pile's own screen. THREE figures rather than the two `group` wears: two is
	   sharing and the restrict mark negates it, so a third glyph in that family would read as a
	   third state of the same thing. A pile is a crowd, and this is the one place a crowd is the
	   subject. ("Show the face group" wears the Organize glyph: a verb is marked by where it
	   SENDS you.) */
	'groups',
	'check',
	/* A menu row that is a setting rather than an act: the Jobs screen's row density lives in
	   its Options menu, and a row that toggles has to say which way it is set. A tick that is
	   present or absent shifts the words beside it; a box that is filled or empty does not. */
	'check_box',
	'check_box_outline_blank',
	/* The Downloads row's "Mark downloads as seen": two ticks, because it acknowledges every
	   outcome the dot is showing in one go rather than confirming one thing, which is what the
	   single `check` means everywhere else. */
	'done_all',
	'chevron_left',
	'chevron_right',
	/* The mark on every `<details>` in the app, drawn by app.css rather than by `Icon`: it is
	   generated content, so it stays out of the DOM and cannot land in a summary's text. That
	   is not a nicety here: an icon ligature becoming part of a control's accessible name
	   breaks locators. It is still listed here because this list is what
	   the subsetter keeps, and a glyph left out of it renders as its own letters. */
	'keyboard_arrow_right',
	/* The popout's Info press on a phone: it brings up, under the picture, what Expand opens on the
	   desk, so the glyph says "up" twice rather than naming a document. */
	'keyboard_double_arrow_up',
	/* The two ends of a paged list. A chevron against a bar, which is the shape every pager and
	   every media transport uses for "all the way", so the pair beside the plain chevrons reads
	   as one step versus the whole distance without a word explaining it. */
	'first_page',
	'last_page',
	'close',
	/* Photo sets. A stack of pictures, which is what a set is: the frames of one shoot sitting one
	   behind another. Deliberately NOT the box that means Collections: a set is not something you
	   put things into, it is a group of pictures that arrived together, and one glyph on both would
	   read as a single feature drawn twice. */
	'photo_library',
	/* Library, the phone's tab for every kind of thing a file is grouped into, and its screen's
	   title. The newsstand, a rack of every kind of title side by side, which is what the screen is:
	   it names no one kind the way each row does, and it is not the Photo Sets stack beside it. */
	'newsstand',
	/* Remote, the phone's tab for driving what is playing at the desk. The handset with its signal
	   rather than a cast glyph: nothing is sent to the screen, the screen is told what to do. */
	'settings_remote',
	/* Theater's two sends, which hand the wall's cells to the other device and replace the wall
	   there: to the desk, the screen on its stand (a person's own words for it); to the phone, the
	   handset with the arrow going into it. */
	'connected_tv',
	/* Keeping the stretch marked on the timeline, as a row on the Loops screen. A bookmark with a
	   plus: it is not `add` (that makes a new thing out of nothing) and not the lemniscate beside it
	   (that MARKS the two ends). What this does is keep what has already been marked. */
	'bookmark_add',
	/* Collections. A box: a thing you PUT things into, which is what a collection is. A
	   bookmarked-images glyph says "saved pictures" and would read as a second Favorites. */
	'box',
	'content_copy',
	/* The Paste button in the Add panel. Its twin above, so the pair of them read as one idea:
	   a clipboard glyph beside a copy glyph would have looked like two unrelated verbs. */
	'content_paste',
	/* The editor's five verbs: trimming the ends and taking out the middle are two intentions, so
	 * two glyphs; a half turn has none, since every glyph for it reads as a mirror. */
	'crop',
	/* On the corner panel: make the panel exactly the shape of what is in it, so there are no bars
	   down the sides. The one control there whose whole meaning is a rectangle. */
	'fit_screen',
	'content_cut',
	/* Making a GIF out of the marked piece, in the editor's clip panel. The one verb there
	   whose output is a different KIND of file rather than a shorter version of the same one, so it
	   gets a glyph naming the format rather than one naming a cut. */
	'gif',
	'rotate_left',
	'rotate_right',
	/* Mirroring the picture. ONE name for two buttons: the font has a glyph for a mirror across a
	   vertical line and none for a mirror across a horizontal one, so the second button draws this
	   same glyph turned a quarter. That is honest rather than a workaround: mirroring top to
	   bottom IS mirroring left to right, on a picture that has been turned. */
	'flip',
	'delete',
	'download',
	'drag_indicator',
	/* Changing what is IN a file: the editor, the trim, the picture panel.
	 *
	 * Not the rename glyph as well: a pencil for "give this a different name" and a pencil for
	 * "cut this clip up" would be one shape standing for two unrelated acts, next to each other
	 * in the same menu. Renaming is `edit_square` and changing the file is `design_services`;
	 * this is what is left over the places that are neither.
	 */
	'edit',
	/* Rename. The pen over a square: writing INTO a box, which is what a name field is. One
	   glyph for every rename in the app (a file, a person, a saved search, a user, a folder, a
	   wall), so the row reads the same wherever it is. */
	'edit_square',
	/* Make a GIF out of a clip: its own row on a file's menu, out of Trim, with the boxed GIF
	   glyph so the row says what comes out of it. */
	'gif_box',
	/* A FILE, wherever the app means one: a count, a tab, the name on disk. The page of ruled
	   lines, which reads as "a document" where the grid glyph beside it means the wall itself. A
	   folded newspaper is a publication, not a file, and at 14px the fold is all of it.

	   A count of files is said in four places, and all four say it with this glyph. */
	'article',
	/* Seen with: three people, which is what "who else is in these files" looks like. */
	'diversity_3',
	/* Changing the file itself: the editor, and the Modify row on a file's own menu. The drafting
	   tools, which say a thing is being WORKED ON rather than annotated. That is the distinction from the
	   pencil beside it, which belongs to renaming. */
	'design_services',
	'error',
	'expand_less',
	'expand_more',
	'favorite',
	/* Kept at the top of a wall. Material's drawing-pin, pushed in and standing up, and the pair is
	   the point: `keep_off` is the same pin lying down with a line through it, so the verb reads as
	   its own opposite on a row that is already pinned. Not `bookmark`, which is already the mark
	   for "keep this" on a saved search, and not `star`, which is the rating. */
	'keep',
	'keep_off',
	/* Making a smaller copy. The two arrows squeezing inward, which reads as "make this smaller"
	   without implying anything is thrown away: the original is never touched. */
	'compress',
	'folder',
	/* What Sift wrote down about what it did: the Settings log. The blank sheet, which is what a
	   log somebody has not opened looks like and is deliberately not what a FILE looks like.
	   The count of files is `article`, said the same way in all four places it appears (a
	   folder's hover label, an entity's Files tab, a person's hover card, a related wall). */
	'draft',
	/* Arranging the folders themselves, from the explorer on Browse. A folder with a plus for making
	   one and a folder with an arrow for moving one: both say "folder" first, which is what tells
	   them apart from the file verbs sitting a line above in the same menu. */
	'create_new_folder',
	'drive_file_move',
	/* The three cards that came out of Maintenance and onto the Organize board. Each needed a glyph
	   of its own: they sit in a grid beside Near Duplicates, and the icon is what tells four
	   similar-sounding piles apart before their titles are read. */
	'file_copy',
	'shield',
	'rule_folder',
	'fullscreen',
	'fullscreen_exit',
	/* An entity page's identity band giving up its room, so the tabs under it start at the top of the
	   screen. Deliberately NOT the pair above: those two mean the SHELL filling the window, which is a
	   different act on a different element, and one glyph for two acts teaches somebody that the first
	   of them was a lie. This pair is about a region shrinking to nothing and coming back. */
	'close_fullscreen',
	'grid_view',
	/* What a cell on the theater wall draws from. A frame with a play mark: a wall of cells is a
	   wall of things PLAYING, and the panel behind this chooses what feeds each one.

	   Deliberately not the funnel. Filter is drawn on every screen, merely dimmed on this one,
	   and two funnels a few pixels apart meaning two different things, one of them dead, is
	   exactly the confusion drawing Filter everywhere is meant to remove. */
	'slideshow',
	/* Browse, wherever the screen itself is named: the rail, its own heading, and the folder
	   menu's way of opening one. The window with a picture in it rather than the four squares,
	   which stay where they mean a grid: the tile-size control and the folder view's switch to
	   pictures. The search dropdown's Media row wears `camera_roll`, because four squares say "a
	   grid of things" and the question there is what KIND of thing. */
	'browse',
	/* Share, in the sharing panel, and the people it is shared with. Two figures rather than one,
	   so it is not read as the Person entity the library is organised by. */
	'group',
	/* Visibility: the report of who can actually reach a thing, beside Share in every menu that
	   offers it. A shield with a magnifier: it is a CHECK on the decisions rather than one of them,
	   which is what keeps it apart from the two figures above it. Not `visibility`, which is already
	   the eye the Unhide verb wears, and this is not about the vault. */
	'policy',
	'hide_image',
	/* A file whose bytes Sift cannot find, as the first mark on its tile. A DOCUMENT with a
	   question mark on it: the mark is about the file rather than about its picture, and the
	   picture is fine: a thumbnail lives beside the library, so a tile whose file has gone
	   draws exactly as it always did, which is the whole reason the mark exists.

	   Not `visibility_off`, the crossed-out eye, which is the HIDDEN mark on the same row of the
	   same tile; the two would be told apart by colour alone.

	   Not `broken_image`, which is the obvious reach: in the rounded face this app ships it is a
	   plain picture frame with no tear in it at all, it is `hide_image` (the same frame,
	   slashed) with one line changed, and `hide_image` already means "no picture" on this very
	   tile. Not `scan_delete` either: a page with a cross reads as deleted, and the sentence
	   beside this mark exists to say the file is still in the library. The question mark is the
	   true state: a drive that is not mounted, a folder that moved and an archive that was
	   thrown away all look the same from here. */
	'unknown_document',
	/* The date range's own glyph. Not the history dial, which is the glyph for "what happened
	   before", close enough to a date to look deliberate and wrong enough to send somebody looking
	   for a list of past searches. */
	'calendar_month',
	'history',
	'info',
	/* Organize, on the rail and on its own heading. The tray, which is what a pile of things
	   waiting on somebody looks like, and it empties, which is the state that screen is for. */
	'inbox',
	/* Insights, in the rail and on the screen's own heading: a line rising over points. The one word
	   the screen is called, and the glyph of that name. Not a bar chart, because the screen's bars
	   are one block of many, and not the pulse of Activity, which is about the work in flight. */
	'insights',
	/* Tags, everywhere they are named: the rail, the screen's own heading, the verb on a
	   selection, and every row the search box offers that is not something more specific. The
	   price-tag outline rather than the bookmark-shaped one: it reads as a thing attached to
	   something else, which is what a tag is, and it is not mistaken for a saved item. */
	'shoppingmode',
	/* THE SEARCH DROPDOWN'S FIELD MARKS: one glyph per kind of filter, so a field does not fall
	 * through to the tag's mark; the other fields reuse a mark the app already has. */
	'camera_roll',
	'file_png',
	'calendar_clock',
	'4k',
	'av1',
	'audio_file',
	/* The name somebody GAVE a file, on the search dropdown's Title row. `titlecase` and not
	   `title`: the plain one is a capital T on a baseline, which reads as "text" rather than as
	   a name, and the pair of cases says "this is what it is called". */
	'titlecase',
	'undereye',
	'mobile_rotate',
	/* The sidebar toggle, in its two directions. Material draws the panel filled when it is open
	   and outlined when it is not, so the pair reads as a state rather than as two arrows. */
	'left_panel_close',
	'left_panel_open',
	'link',
	/* The mark on a folder that lives on another machine, and says whether it is answering. */
	'lan',
	/* Whose IP a download used: your own connection, or a tunnel's. A pair, so the two
	   answers to one question are told apart at a glance rather than read. */
	'language',
	'private_connectivity',
	'open_in_full',
	'lock',
	/* The words somebody typed, offered as words. The one glyph on the suggestion list that names
	   no kind of thing, which is exactly its job: it is the row that is NOT a person or a tag. */
	'match_case',
	'menu',
	/* A breadcrumb trail's folded steps: the steps between the first and the last two, in one press. */
	'more_horiz',
	'more_vert',
	'pause',
	'person',
	/* Who a song credits: a person with a note, so the Artists column does not wear the People
	   glyph and read as a column of people. */
	'artist',
	/* Sift's verdict on a person: the green check beside "Sift identifies Ada well" under the cover. */
	'person_check',
	/* Saying who a handle belongs to, and taking that back. `person_add` is the offer and
	   `link_off` is the undo: a broken chain rather than a crossed-out person, because what is
	   being undone is the JOIN and the person stays exactly where they were. */
	'person_add',
	/* Taking a name back off a face: the person with a minus, because what goes is who it is,
	   and the face stays. Said as "Remove {name} from this" wherever a face is named. */
	'person_remove',
	/* Two people who turn out to be one, and two answers about one field. Both are kinds of
	   decision Sift can be asked to make, and neither reads as any glyph already here:
	   `merge` is two lines becoming one, `compare_arrows` is two pointing at each other. */
	'merge',
	'compare_arrows',
	/* A swap with another Sift: two arrows passing each other, each way. Not `compare_arrows`,
	   which points two answers AT each other. A swap is files going across in both directions. */
	'swap_horiz',
	/* The same arrows in a filled disc: the mark on a thing picked for a swap. A pick's mark is a
	   filled shape (the funnel is the other), and the bare arrows have no filled form. */
	'swap_horizontal_circle',
	'link_off',
	/* The mini player. The panel-in-a-panel is what every video site draws for this, so it is the
	   one glyph somebody will already recognise. */
	'picture_in_picture',
	/* The audio-only bar: the player minimised to a strip that keeps the sound. A waveform, because
	   what is left when the picture goes is what is heard. */
	'cadence',
	/* Screenshot, wherever it is offered: a region with its corners marked, the capture glyph
	   rather than a camera, which would read as taking a photograph with this device. The rounded
	   region rather than the plain frame, one glyph for the act across the app. */
	'screenshot_region',
	/* Read a database somebody else's software wrote, and say what is in it before anything
	   changes. A page with a lens on it: looking inside, not sending up. */
	'find_in_page',
	/* A network: a Site that other Sites are part of. One glyph wherever a network is meant: the
	   mark on its card and its page, the Network facet and chip, and the note in the Share and Hidden
	   dialogs that a decision reaches the Sites within. The nodes joined to a centre say "these
	   belong together"; a cluster of houses beside it would read as a second thing, and one idea
	   with two pictures is two ideas to a reader. */
	'hub',
	/* Preview everything visible, on the shared bar. The play triangle inside a repeat, not the
	   plain triangle, which means START THIS. A control that plays a whole screen at the same time
	   is not the same instruction. */
	'autoplay',
	/* A floppy disk, stacked bookmarks, a stop, and two window shapes. The disk is for the one
	   thing a disk has always meant, a copy saved to somebody's own device: the History row
	   for that act; keeping the filters on screen is `filter_plus`, beside the funnel below.
	   Stacked bookmarks for the list of what was kept, a stop for a preview toggle that is
	   already running, and two window shapes for the Theater's layout and its saved ones. */
	'save',
	/* Export: a copy sent out of Sift, which a disk (written here) does not say. */
	'publish',
	'bookmark_stacks',
	'autostop',
	'view_array',
	/* The third way of looking at a folder's folders, beside the plain list and the pictures: the
	   same names flowed down and then across, which is what a file manager calls List. Its glyph is
	   the only one of the three that says "columns" rather than "rows" or "grid". */
	'view_column',
	'table_view',
	'play_arrow',
	/* The five-second jumps either side of Play. The number is drawn into the glyph, so the pair
	   says how far it goes without a label beside it. */
	'replay_5',
	'forward_5',
	/* WHAT A HELD NUMBER KEY IS DOING to a Theater cell, reported over the picture. The three rates
	   a hold can put a cell in need three glyphs and not one: the badge says "0.5x" or "2x" beside
	   the mark, and a single speedometer standing for all three would leave the direction (which
	   is the half somebody is actually watching for) to the number alone. The double chevrons are
	   what every transport in the world means by fast, and the ring of dots is the film-speed mark
	   rather than a clock, because slow motion is about how a picture moves and not about time. */
	'fast_forward',
	'fast_rewind',
	'slow_motion_video',
	'public',
	'restore_from_trash',
	'schedule',
	/* Settings > Scheduled tasks. A clock with a repeat arrow on it, against Activity's plain clock
	   beside it in the same group: one section is what Sift is doing NOW, the other is what it will
	   do again. `schedule` was taken and the difference between the two is the whole of the label's
	   job otherwise. */
	'event_repeat',
	/*
	 * THE FOUR ORDERS THAT ARE ABOUT TIME, on the Sort menu: a clock or an hourglass with the
	 * direction drawn onto it.
	 *
	 * The arrow is the whole of it. Which of two clocks (one struck through) means newest is a fact
	 * somebody has to learn, where an arrow up and an arrow down is a fact they already have. And
	 * one glyph for two opposite duration orders would leave the words carrying the whole
	 * difference.
	 *
	 * A clock for when a file arrived and an hourglass for how long it runs, so the two questions
	 * are told apart by the SHAPE and the two directions by the arrow. `timer` stays in this list:
	 * it is the Theater cell's "move on after", which is a countdown and not an order.
	 */
	'clock_arrow_up',
	'clock_arrow_down',
	'hourglass_arrow_up',
	'hourglass_arrow_down',
	/* Name order, either way, on the Sort menu. One glyph for both directions on purpose: the two
	   rows say A-Z and Z-A in words, and two nearly identical alphabet glyphs beside them would be
	   a difference to decode rather than to read. */
	'sort_by_alpha',
	/* How much room something takes, on the Sort menu's largest and smallest rows. The filling dial
	   rather than a disk: `hard_disk` already means WHERE a file is in this app, and one glyph
	   meaning two things on one screen is the fault the whole of this list exists to refuse. */
	'data_usage',
	'search',
	/* A saved search: the glass with a bookmark's corner folded into it, not the plain
	   bookmark, which is the mark for "keep this", so the act and the collection it goes into do
	   not share a glyph on two controls a few pixels apart. */
	'saved_search',
	'settings',
	/* The cookies saved for a site, on the door in the Downloads header and on the sheet behind it.
	   A cookie, because that is the thing itself and the screen says the word everywhere. A key or
	   a shield would be a picture of "credentials", which is the word this surface deliberately
	   does not use. */
	'cookie',
	/* ANY of these, on a filter chip carrying more than one value, where the difference
	   decides what the screen holds. The road that forks, because that is what "either of these"
	   is.

	   Its opposite is not here and cannot be: ALL of these is drawn as a literal `&`, since
	   Material Symbols ships no ampersand at all: the name shapes as its own eight letters
	   rather than as one glyph, which is how a missing icon ships looking like a decision. See
	   `FilterChip`.

	   `checklist` and `shuffle` stay in the set for what they actually mean: choosing several
	   on the library screen, and the player's random order. */
	'arrow_split',
	'checklist',
	/* The run controls on the player's bar. Shuffle plays what is on screen in a random order; the
	   other two are one control's two answers (repeat this clip, or carry on through the list),
	   drawn as the same arrows with and without a "1" so the pair reads as one question. */
	'shuffle',
	'repeat',
	'repeat_one',
	/* The third answer to the same question: stop where the file ends. Not a repeat arrow with
	   something crossing it: what it describes is the absence of a repeat, so it is the transport
	   glyph for stopping rather than a fourth variation on the arrows. */
	'stop_circle',
	/* Theater: a wall of several videos, each drawing from a filter of its own. */
	'hearing',
	'interactive_space',
	/* The list of what the keys do, on Theater's bar. Theater overrides bindings every video site
	   binds, so the way to read them has to be on screen rather than assumed. */
	'keyboard',
	/* The stretch between two points on the timeline, played over and over. The lemniscate rather
	   than the repeat arrows, which belong to the control above: a loop between two marks is
	   endless in a way that "play this file again" is not. */
	'all_inclusive',
	/* Play something else out of the whole library. A die rather than another pair of arrows: it is
	   a jump to somewhere unrelated, not a way through the list on screen. */
	'casino',
	/* The panel of facts about the file and about what is playing it: Stats for nerds, on the
	   player, the still view and a Theater cell. Not `analytics`, a bar chart: what this panel
	   holds is a read-out somebody went looking for rather than a chart of anything. */
	/* What opens the rest of the player's controls. The transport row keeps the handful of things
	   a hand reaches for without looking; everything that is about the clip rather than about the
	   playhead sits behind this, a grid of icons that appears under the pointer. */
	'home_storage',
	/* Sift filed this one itself: on a person's name on a file, where a pass put the name there and
	   nobody was asked. The layered-shapes glyph, which reads as "worked out from what is here"
	   rather than as a warning. An inference is not a fault. */
	'style',
	/* Similar to this: the files that look like one file, from the file menu and the search box's
	   list. A magnifier over a picture, because the question is asked OF a picture: which files
	   look like this one. Not `style`, the stacked shapes the Design page wears. */
	'image_search',
	'star',
	/* Highest rated, on the Sort menu. The solid star rather than `star`, which is the rating
	   control's own outline. An order is not a rating being set, and the filled mark reads as
	   "the most of this" at the size a menu row draws it. */
	'star_rate',
	/* The O counter's mark, on the file's action row and on its facet column. A drop rather
	   than a face: the tally is a count of a thing that happened, and a smiling face would be
	   the app having an opinion about it. */
	'water_drop',
	'skip_next',
	'skip_previous',
	'sync',
	/* The filter and sort controls at the head of the bar. Named ones rather than
	   general-purpose sliders: a tuning slider means "settings" everywhere else in this app, and
	   the two controls beside each other have to read as two different things. */
	'filter_alt',
	/* Keeping the filters on screen as a Saved Filter. The same funnel, with a plus: what is kept
	   is the FILTERING, and the funnel is the mark that filtering wears everywhere else here, so the
	   control reads as "add this filter to the kept ones" rather than "write something to disk".
	   A floppy disk would say the second, and it is also what a copy saved to the device wears.

	   New in material-symbols 0.47.5 (0.47.2 to 0.47.4 do not have it), and at U+FFEA3, past
	   the Basic Multilingual Plane. That needs nothing new: `prepare_fonts.js` reads the whole
	   character map and `Icon` draws with `String.fromCodePoint`, and three glyphs already sat out
	   there before it. */
	'filter_plus',
	/* How much is under a row, on the walls of entities. A sigma rather than a hash: it is a
	   question about the SIZE of what is under each row, not about numbering them. */
	'functions',
	'sort',
	'undo',
	'upload',
	/* Two jobs, one glyph, on purpose: the vault's reveal control, and the count of how many times
	   something has been opened on a tile. They are the same idea (an eye is "seen"), and a
	   library that spells one of them differently is a library with two words for one thing. */
	'visibility',
	'visibility_off',
	/* DISCARD, wherever the act is drawn: the face and face-group verbs and the Discarded tab.
	   The minus: taken off the list of what is waiting, and still there. Not `visibility_off`,
	   which is HIDE: a discarded face is not hidden from anybody. Not `delete`, which is the bin
	   beside it on the same card and the act that cannot be taken back. See `DISCARD_ICON`. */
	'remove',
	'volume_off',
	'volume_up',
	'warning',
	/* A question waiting on a record, beside the History tab on a person, a site or a tag. NOT
	   `warning`, which is the one above: a warning is something that has gone wrong, and two
	   answers about somebody's birth date is neither wrong nor anybody's fault. This is the
	   glyph for "there is something to read here", which is what the mark means. */
	'data_info_alert',
	/*
	 * The state of a piece of work, as a mark rather than a coloured dot.
	 *
	 * `Badge` is one component drawing every one of these (the jobs list and the download queue
	 * both read from it), so these belong together and are listed together. A coloured dot alone
	 * would ask somebody to learn a colour code and leave two amber states looking identical to
	 * each other.
	 *
	 * Chosen so the SHAPE carries the meaning without the colour: a ring around a tick for done, a
	 * ring around a bar for blocked, a ring around a square for stopped, a ring around a cross for
	 * failed. The rings are deliberately a family (these are all the same kind of fact), while
	 * the three that are not a ring are the three that are not an ending: queued is a list with a
	 * tick added, skipped is a step past, and a duplicate is two coins overlapping.
	 *
	 * `chronic` is the odd one and it earns its place: it is the download queue's "waiting for
	 * cookies", which is not a failure and not a stop. It is time passing while something waits
	 * for a person. `stop_circle`, `skip_next` and `shield` are already in this list above, doing
	 * their own jobs elsewhere, and are reused here rather than duplicated.
	 */
	'playlist_add_check',
	'check_circle',
	'do_not_disturb_on',
	'chronic',
	'cancel',
	'toll',
	/*
	 * DOWNLOADS IS `download`, THE BARE ARROW, EVERYWHERE.
	 *
	 * Three arrows in three different surrounds for one idea (the verb, the rail row, the toast) is
	 * three things to learn. One arrow; which job it is doing is said by what is AROUND it: a
	 * status dot cut into its corner while a queue is running, and a movement on the toast while a
	 * fetch is in flight. Both of those are true only when they are true, which a permanent ring
	 * never is.
	 */
	/* Quarantine, as a badge mark. The shield with a question in it rather than the plain shield:
	   a plain shield says PROTECTED, which is the opposite of what a quarantined file is. This one
	   says "something about this is unresolved", which is exactly the state. */
	'gpp_maybe',
	/* A folder that lives on a disk in this machine, opposite `lan` for one reached over the
	   network. With a mark for only the network case, "on this computer" would be something to
	   infer from the absence of a badge. */
	'hard_disk',
	/*
	 * Everything, with nothing taken out: the grid of squares.
	 *
	 * It is the "All" tally on the Jobs screen: the four tallies are one row of one kind of
	 * control, and a row where three lead with a mark and the fourth with a word reads as a row
	 * that does not line up.
	 *
	 * Not a state, and deliberately not from the family above: those are answers to "how did this
	 * piece of work end", and "all of them" is not one of the answers. A grid of squares says "the
	 * whole set" without pretending to be another outcome.
	 */
	'apps',
	/* The facets every wall carries: one glyph per dimension a person, a site, a tag or a file
	   can be filtered by, each the plainest glyph in the set for its question. */
	'wc',
	'face_3',
	'diversity_2',
	'flag',
	/* Breast type. The spectacles rather than the lens-blur circle, which is a photographic term
	   and says nothing about a person. */
	'eyeglasses_3',
	/* How tall somebody is: two figures of different heights, which says the comparison the band
	   is. A plain double-headed arrow is a measurement of anything at all. */
	'people_size_increase',
	'cake',
	'work_history',
	'category',
	'event',
	/* An account on a site: the at-sign. The mark on a name a handle filed. */
	'alternate_email',
	/* Stats for nerds, on the popout player, the still view and a theater cell. The head with a
	   circuit in it rather than the laboratory flask: the panel is what the machine knows about
	   the file, and a flask reads as an experimental feature. */
	'cognition_2',
	/* The PMV-creator mark, beside a creator's name, on their card and on a preview of them. The
	   lens with a soft focus behind it: what a PMV is MADE of is footage cut to music, so the
	   mark says the craft rather than a status.

	   Not `verified`: a rosette reads, from every other application, as a site vouching for a
	   username, and nothing here vouches for anybody. Drawn in the ordinary ink of a glyph, with
	   no colour of its own. */
	'cinematic_blur'
] as const;

export type IconName = (typeof ICON_NAMES)[number];
