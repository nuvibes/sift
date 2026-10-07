/* What every screen draws, and so which bells it has to hear: the live matrix.
 *
 * A screen keeps what it read, so each thing it draws needs the bell the server rings when that
 * thing moves (`changes.svelte.ts`). `scripts/check_live_matrix.js` holds every route and every
 * Settings section to a row here, and each row to hearing those bells in its own code or the
 * modules it imports. `owes` names a thing drawn whose bell is not heard yet; the check prints it on
 * every run. The two literals below are read as data by that script: plain values only.
 */

export type Bell =
	| 'libraryChanges'
	| 'arrivals'
	| 'settled'
	| 'assetState'
	| 'mine'
	| 'jobChanges'
	| 'downloadChanges'
	| 'settingChanges'
	| 'screenChanges';

export type Thing =
	| 'files'
	| 'opinions'
	| 'people'
	| 'tags'
	| 'sites'
	| 'collections'
	| 'photoSets'
	| 'songs'
	| 'loops'
	| 'faces'
	| 'folders'
	| 'history'
	| 'record'
	| 'counts'
	| 'jobs'
	| 'downloads'
	| 'settings'
	| 'mine'
	| 'screens';

/** The bells the server rings when each thing moves. `settled` is a scan or a download ending. */
export const THING_BELLS: Record<Thing, Bell[]> = {
	files: ['arrivals', 'libraryChanges'],
	opinions: ['assetState'],
	people: ['libraryChanges'],
	tags: ['libraryChanges'],
	sites: ['libraryChanges'],
	collections: ['libraryChanges'],
	photoSets: ['libraryChanges'],
	songs: ['libraryChanges'],
	loops: ['libraryChanges'],
	faces: ['libraryChanges'],
	folders: ['libraryChanges'],
	history: ['libraryChanges'],
	/* What the server says about a file or a thing beyond its row: its name, details, reach. */
	record: ['libraryChanges'],
	counts: ['libraryChanges', 'settled'],
	jobs: ['jobChanges'],
	downloads: ['downloadChanges'],
	settings: ['settingChanges'],
	mine: ['mine'],
	screens: ['screenChanges']
};

interface MatrixRow {
	/** The route file under `src/`, or `settings/<section id>`. */
	screen: string;
	/** The file that draws it, under `src/`: the route itself, a pane, or the view a route opens. */
	file: string;
	/** What keeps it, where that is not the file itself: a wall's grid and its catch-up. Imported by it. */
	via?: string[];
	shows: Thing[];
	/** Whether what belongs on it hangs on this account's heart, stars or views (Favorites). */
	membership?: 'opinions';
	owes?: Thing[];
	why?: string;
}

export const LIVE_MATRIX: MatrixRow[] = [
	{
		screen: 'routes/asset/[id]/+page.svelte',
		file: 'lib/components/AssetView.svelte',
		via: ['lib/library/judgement.svelte.ts'],
		shows: ['opinions', 'people', 'tags', 'collections', 'faces', 'jobs', 'history']
	},
	{
		screen: 'routes/browse/+page.svelte',
		file: 'routes/browse/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['files', 'opinions']
	},
	{
		screen: 'routes/collections/+page.svelte',
		file: 'routes/collections/+page.svelte',
		shows: ['collections', 'counts', 'mine']
	},
	{
		screen: 'routes/collections/[id]/+page.svelte',
		file: 'routes/collections/[id]/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['collections', 'files', 'opinions', 'mine']
	},
	{
		screen: 'routes/collections/new/+page.svelte',
		file: 'routes/collections/new/+page.svelte',
		shows: [],
		why: 'a blank form'
	},
	{
		screen: 'routes/connect/+page.svelte',
		file: 'routes/connect/+page.svelte',
		shows: [],
		why: 'a sign-in step, before any library'
	},
	{
		screen: 'routes/downloads/+page.svelte',
		file: 'routes/downloads/+page.svelte',
		shows: ['downloads', 'settings']
	},
	{
		screen: 'routes/favorites/+page.svelte',
		file: 'routes/favorites/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['files', 'opinions'],
		membership: 'opinions'
	},
	{
		screen: 'routes/hidden/+page.svelte',
		file: 'routes/hidden/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['files', 'opinions']
	},
	{
		screen: 'routes/insights/+page.svelte',
		file: 'routes/insights/+page.svelte',
		shows: ['counts', 'opinions', 'people', 'tags']
	},
	{
		screen: 'routes/insights/recaps/+page.svelte',
		file: 'routes/insights/recaps/+page.svelte',
		via: ['lib/library/recaps.svelte.ts'],
		shows: ['history', 'mine']
	},
	{
		screen: 'routes/insights/recaps/[id]/+page.svelte',
		file: 'routes/insights/recaps/[id]/+page.svelte',
		shows: ['history', 'mine']
	},
	{
		screen: 'routes/library/+page.svelte',
		file: 'routes/library/+page.svelte',
		shows: [],
		why: 'the list of the library screens, fixed'
	},
	{
		screen: 'routes/library-location/+page.svelte',
		file: 'routes/library-location/+page.svelte',
		shows: [],
		why: 'a setup step, before any library'
	},
	{
		screen: 'routes/locked/+page.svelte',
		file: 'routes/locked/+page.svelte',
		shows: [],
		why: 'the lock screen, which draws nothing of the library'
	},
	{
		screen: 'routes/login/+page.svelte',
		file: 'routes/login/+page.svelte',
		shows: [],
		why: 'signing in, before any library'
	},
	{
		screen: 'routes/loops/+page.svelte',
		file: 'routes/loops/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['loops', 'files', 'opinions']
	},
	{
		screen: 'routes/more/+page.svelte',
		file: 'routes/more/+page.svelte',
		shows: [],
		why: 'the phone menu, fixed'
	},
	{
		screen: 'routes/opening/+page.svelte',
		file: 'routes/opening/+page.svelte',
		shows: [],
		why: 'the window opening, before any library'
	},
	{
		screen: 'routes/organize/+page.svelte',
		file: 'routes/organize/+page.svelte',
		shows: ['faces', 'people', 'tags', 'counts']
	},
	{
		screen: 'routes/organize/[queue]/+page.svelte',
		file: 'routes/organize/[queue]/+page.svelte',
		via: ['lib/components/organize/DuplicatesPanel.svelte'],
		shows: ['faces', 'people', 'tags', 'counts', 'settings']
	},
	{
		screen: 'routes/organize/[queue]/[id]/+page.svelte',
		file: 'routes/organize/[queue]/[id]/+page.svelte',
		via: ['lib/components/faces/PileDetail.svelte'],
		shows: ['faces', 'people']
	},
	{
		screen: 'routes/organize/may-be/[person]/[pile]/+page.svelte',
		file: 'lib/components/organize/MayBeReview.svelte',
		shows: ['faces', 'people']
	},
	{
		screen: 'routes/people/+page.svelte',
		file: 'routes/people/+page.svelte',
		shows: ['people', 'counts', 'mine']
	},
	{
		screen: 'routes/people/[id]/+page.svelte',
		file: 'routes/people/[id]/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['people', 'files', 'opinions', 'mine']
	},
	{
		screen: 'routes/people/new/+page.svelte',
		file: 'routes/people/new/+page.svelte',
		shows: [],
		why: 'a blank form'
	},
	{
		screen: 'routes/photo-sets/+page.svelte',
		file: 'routes/photo-sets/+page.svelte',
		shows: ['photoSets', 'counts', 'mine']
	},
	{
		screen: 'routes/photo-sets/[id]/+page.svelte',
		file: 'routes/photo-sets/[id]/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['photoSets', 'files', 'opinions']
	},
	{
		screen: 'routes/photo-sets/new/+page.svelte',
		file: 'routes/photo-sets/new/+page.svelte',
		shows: [],
		why: 'a blank form'
	},
	{
		screen: 'routes/recent/+page.svelte',
		file: 'routes/recent/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['files', 'opinions'],
		membership: 'opinions'
	},
	{
		screen: 'routes/remote/+page.svelte',
		file: 'routes/remote/+page.svelte',
		via: ['lib/remote/screens.svelte.ts'],
		shows: ['screens']
	},
	{
		screen: 'routes/settings/[[section]]/+page.svelte',
		file: 'lib/settings-ui/SettingsPane.svelte',
		shows: [],
		why: 'the frame; each section has its own row'
	},
	{
		screen: 'routes/setup/+page.svelte',
		file: 'routes/setup/+page.svelte',
		shows: [],
		why: 'a setup step, before any library'
	},
	{
		screen: 'routes/sites/+page.svelte',
		file: 'routes/sites/+page.svelte',
		shows: ['sites', 'counts', 'mine']
	},
	{
		screen: 'routes/sites/[id]/+page.svelte',
		file: 'routes/sites/[id]/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['sites', 'files', 'opinions', 'mine']
	},
	{
		screen: 'routes/sites/new/+page.svelte',
		file: 'routes/sites/new/+page.svelte',
		shows: [],
		why: 'a blank form'
	},
	{
		screen: 'routes/songs/+page.svelte',
		file: 'routes/songs/+page.svelte',
		shows: ['songs', 'counts', 'mine']
	},
	{
		screen: 'routes/songs/[id]/+page.svelte',
		file: 'routes/songs/[id]/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['songs', 'files', 'opinions']
	},
	{
		screen: 'routes/songs/new/+page.svelte',
		file: 'routes/songs/new/+page.svelte',
		shows: [],
		why: 'a blank form'
	},
	{
		screen: 'routes/start/+page.svelte',
		file: 'routes/start/+page.svelte',
		shows: [],
		why: 'a setup step, before any library'
	},
	{ screen: 'routes/swap/+page.svelte', file: 'routes/swap/+page.svelte', shows: ['jobs'] },
	{
		screen: 'routes/tags/+page.svelte',
		file: 'routes/tags/+page.svelte',
		shows: ['tags', 'counts', 'mine']
	},
	{
		screen: 'routes/tags/[id]/+page.svelte',
		file: 'routes/tags/[id]/+page.svelte',
		via: ['lib/components/AssetGrid.svelte', 'lib/components/wall-catch-up.svelte.ts'],
		shows: ['tags', 'files', 'opinions', 'mine']
	},
	{
		screen: 'routes/tags/new/+page.svelte',
		file: 'routes/tags/new/+page.svelte',
		shows: [],
		why: 'a blank form'
	},
	{
		screen: 'routes/theater/+page.svelte',
		file: 'routes/theater/+page.svelte',
		via: ['lib/components/theater/TheaterWall.svelte'],
		shows: ['settings', 'files']
	},
	{
		screen: 'settings/library',
		file: 'lib/library/LibraryScreen.svelte',
		shows: ['folders', 'settings']
	},
	{
		screen: 'settings/importing',
		file: 'lib/settings-ui/Importing.svelte',
		via: ['lib/jobs/tasks.svelte.ts'],
		shows: ['settings', 'jobs']
	},
	{
		screen: 'settings/tasks',
		file: 'lib/jobs/JobsScreen.svelte',
		via: ['lib/settings-ui/ScheduledTasks.svelte'],
		shows: ['jobs', 'settings']
	},
	{
		screen: 'settings/sites',
		file: 'lib/settings-ui/Sites.svelte',
		via: ['lib/settings-ui/SwapTunnels.svelte'],
		shows: ['settings']
	},
	{
		screen: 'settings/downloads',
		file: 'lib/settings-ui/DownloadsSection.svelte',
		via: ['lib/settings-ui/Downloads.svelte'],
		shows: ['settings']
	},
	{ screen: 'settings/editing', file: 'lib/settings-ui/Editing.svelte', shows: ['settings'] },
	{ screen: 'settings/playback', file: 'lib/settings-ui/Playback.svelte', shows: ['settings'] },
	{
		screen: 'settings/profile',
		file: 'lib/settings-ui/Profile.svelte',
		shows: [],
		why: 'the profile of whoever is signed in, written only from this pane'
	},
	{ screen: 'settings/appearance', file: 'lib/settings-ui/Appearance.svelte', shows: ['settings'] },
	{
		screen: 'settings/get-to-know',
		file: 'lib/settings-ui/GetToKnow.svelte',
		shows: [],
		why: 'a fixed tour'
	},
	{ screen: 'settings/privacy', file: 'lib/settings-ui/Privacy.svelte', shows: ['settings'] },
	{ screen: 'settings/users', file: 'lib/settings-ui/Users.svelte', shows: ['settings'] },
	{
		screen: 'settings/shortcuts',
		file: 'lib/settings-ui/Shortcuts.svelte',
		shows: [],
		why: 'the fixed list of keys'
	},
	{ screen: 'settings/faces', file: 'lib/settings-ui/Faces.svelte', shows: ['settings', 'jobs'] },
	{ screen: 'settings/semantic', file: 'lib/settings-ui/Semantic.svelte', shows: ['settings'] },
	{
		screen: 'settings/stash-boxes',
		file: 'lib/settings-ui/StashBoxesPane.svelte',
		via: ['lib/settings-ui/Enrichment.svelte'],
		shows: ['settings']
	},
	{
		screen: 'settings/music',
		file: 'lib/settings-ui/Music.svelte',
		via: ['lib/settings-ui/MusicLookup.svelte'],
		shows: ['settings', 'jobs']
	},
	{ screen: 'settings/watermarks', file: 'lib/settings-ui/Watermarks.svelte', shows: ['settings'] },
	{
		screen: 'settings/general',
		file: 'lib/settings-ui/General.svelte',
		via: ['lib/settings-ui/settings.ts'],
		shows: ['settings']
	},
	{
		screen: 'settings/performance',
		file: 'lib/settings-ui/Performance.svelte',
		shows: ['settings']
	},
	{
		screen: 'settings/maintenance',
		file: 'lib/settings-ui/Maintenance.svelte',
		shows: ['settings', 'jobs']
	},
	{
		screen: 'settings/backup',
		file: 'lib/settings-ui/Backup.svelte',
		via: ['lib/jobs/tasks.svelte.ts'],
		shows: ['settings', 'jobs']
	},
	{
		screen: 'settings/updates',
		file: 'lib/settings-ui/Updates.svelte',
		shows: ['settings', 'jobs']
	},
	{
		screen: 'settings/documentation',
		file: 'lib/settings-ui/Documentation.svelte',
		shows: [],
		why: 'fixed pages'
	},
	{ screen: 'dialog/delete', file: 'lib/components/DeleteDialog.svelte', shows: ['record'] },
	{
		screen: 'dialog/visibility',
		file: 'lib/components/VisibilityDialog.svelte',
		shows: ['record']
	},
	{ screen: 'dialog/share', file: 'lib/components/ShareDialog.svelte', shows: ['record'] },
	{ screen: 'dialog/compress', file: 'lib/components/CompressDialog.svelte', shows: ['record'] },
	{
		screen: 'dialog/move',
		file: 'lib/components/MoveDialog.svelte',
		shows: ['folders'],
		owes: ['folders'],
		why: 'draws the folders its host hands it'
	},
	{
		screen: 'dialog/edit',
		file: 'lib/components/EditDialog.svelte',
		shows: ['record'],
		owes: ['record'],
		why: 'draws the record its host hands it'
	},
	{
		screen: 'hover/preview',
		file: 'lib/components/EntityPreview.svelte',
		shows: ['record', 'counts']
	},
	{ screen: 'player/audio-strip', file: 'lib/components/player/MiniBar.svelte', shows: ['record'] },
	{ screen: 'player/mini', file: 'lib/components/player/MiniPlayer.svelte', shows: ['record'] }
];
