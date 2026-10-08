/*
 * Every keyboard shortcut in Sift, declared once, beside nothing.
 *
 * ## The failure this is for
 *
 * A shortcut written the other way is three separate things that nobody can keep in step: a
 * comparison against `event.key` buried in whichever component happens to listen, a sentence about
 * it in a comment, and a hand-written list of what the keys do. Such a list drifts: a sheet
 * can say the number keys go "1 to 4" while the code takes 1 to 9, because the list is not the
 * thing that changed when the wall grew.
 *
 * **A list written out beside the thing it describes is only ever as right as the last person to
 * edit both.** So there is one declaration per shortcut, the code asks it whether a press was that
 * shortcut, and anything that shows a list is generated from the same declarations. A sheet cannot
 * disagree with the keys, because there is nothing for it to disagree with.
 *
 * ## What is a shortcut, and what is not
 *
 * A shortcut is a key that reaches something you could otherwise reach with the mouse: a verb, a
 * screen, a control. It is app-wide: it works because you are in Sift, not because a particular box
 * has the focus.
 *
 * **A widget's own keyboard behaviour is not a shortcut and is deliberately not here.** Arrow keys
 * moving through a tree, Enter adding a row to a list, Space pressing the button you are on: those
 * belong to whatever has the focus, they are what makes it usable without a mouse at all, and
 * listing them would bury the eight keys somebody actually wants to know about under forty they
 * already expect.
 *
 * **Escape is the one that sits between the two, and it is here exactly once.** "Close what is
 * open" is a single app behaviour that happens to be implemented by every panel that can be opened.
 * It is declared once, as one row, and no panel needs to declare it again.
 *
 * ## Made to be rebindable later, without moving anything
 *
 * Nothing here can be rebound today. What is wanted first is a list somebody can keep track of, and
 * a list is not a settings screen full of controls that can conflict with each other. But every
 * call site asks `matches(event, id)` rather than comparing a key of its own, so the day a stored
 * binding exists it is read here, in one function, and every shortcut in the app follows. **That is
 * the whole reason for the indirection**, and it is worth the extra call: doing it afterwards means
 * finding every comparison again.
 */

/** What a shortcut is called in code. Stable, and what the gate and the list name it by. */
export type ShortcutId =
	| 'app.dismiss'
	| 'app.shutHidden'
	| 'app.lock'
	| 'app.search'
	| 'search.byMeaning'
	| 'search.forWords'
	| 'stage.toggleBar'
	| 'player.playPause'
	| 'player.back'
	| 'player.forward'
	| 'player.mute'
	| 'player.corner'
	| 'player.audioOnly'
	| 'player.save'
	| 'player.repeat'
	| 'player.shuffle'
	| 'player.loop'
	| 'player.louder'
	| 'player.quieter'
	| 'player.louderABit'
	| 'player.quieterABit'
	| 'player.previous'
	| 'player.next'
	| 'player.fill'
	| 'theater.cell'
	| 'theater.everyCell'
	| 'theater.mute'
	| 'theater.muteAll'
	| 'theater.pauseAll'
	| 'theater.pause'
	| 'theater.back'
	| 'theater.forward'
	| 'theater.previous'
	| 'theater.next'
	| 'theater.louder'
	| 'theater.quieter'
	| 'theater.louderABit'
	| 'theater.quieterABit'
	| 'theater.repeat'
	| 'theater.loop'
	| 'theater.fill'
	| 'theater.corner'
	| 'theater.keys'
	| 'select.all'
	| 'select.undo'
	| 'select.redo'
	| 'vault.panicLock'
	| 'view.previous'
	| 'view.next';

/** Which part of Sift a shortcut belongs to. What a list groups by, and in this order. */
export const AREAS = [
	'Anywhere',
	'In the search box',
	'Picking things',
	'Looking at a file',
	'Watching something',
	'Theater'
] as const;

/* Exported HERE and not before, which is the rule this file is held to: the public-surface
 * gate refuses a name nothing reads, and exporting for a consumer that does not exist yet is
 * exactly the claim that gate exists to stop. The settings section reads both. */
/* Not exported, for the reason `public-surface.test.ts` gives: every use of this name is in
   this file, reached from outside through the signatures below. */
type Area = (typeof AREAS)[number];

export interface Shortcut {
	id: ShortcutId;
	/**
	 * The `event.key` values this shortcut answers to.
	 *
	 * Several where a key means the same thing however it arrives: `m` and `M` are one shortcut,
	 * and `' '` and `'Spacebar'` are the same press spelled two ways by different browsers. Where
	 * the case genuinely means two different things (Theater's `m` mutes one cell and `M` mutes
	 * everything), they are two shortcuts, which is what makes them separately listable.
	 */
	keys: readonly string[];
	/**
	 * Whether Ctrl (or Cmd) has to be held.
	 *
	 * Absent means it must NOT be held, along with Alt and Meta. A shortcut that fired with a
	 * modifier down would steal a browser combination somebody meant for the browser.
	 */
	ctrl?: true;
	/**
	 * Whether Shift has to be held, must not be, or is not consulted.
	 *
	 * Three states rather than two, because all three are really in use and the difference is not
	 * cosmetic. **Undefined** is "not consulted", which is right where the key value already says
	 * it: Theater's `m` and `M` are two shortcuts distinguished by the letter that arrives, and
	 * asking about Shift as well would be asking the same question twice. **False** is a handler
	 * that turns down any modifier at all, which is what the full-size player does. **True** is the
	 * one shortcut that is only reachable with it: redo, which is undo with Shift held, and which
	 * cannot be told apart by the key value alone because Caps Lock produces the same letter.
	 */
	shift?: boolean;
	/**
	 * Whether it still fires while somebody is typing into a box.
	 *
	 * False for nearly everything, and that is the safe default: a bare `m` while a filter is being
	 * filled in is the letter m. The two locks say true, because a lock that will not work while
	 * the search box has the focus is a lock somebody cannot rely on, and both need Ctrl, so
	 * neither can be produced by typing.
	 */
	whileTyping?: true;
	/** How the keys read on screen. Written out rather than derived, because `' '` reads as Space. */
	shown: string;
	/** What it does, in Sift's own words. This is the sentence a list shows. */
	does: string;
	area: Area;
}

/**
 * Every shortcut, in the order a list should show them.
 *
 * Grouped by area rather than sorted, because the grouping is the useful part: somebody looking for
 * a key is looking for one that applies to what is in front of them.
 */
/**
 * How one shortcut reads on screen, by its id, or null where there is no such shortcut.
 *
 * Shortcuts are shown, not remembered. A control that has one should say so where
 * somebody meets it, and the only honest source for that is this list. A key written out beside a
 * button is a second copy that goes stale the day the key changes, silently, because nothing on
 * either side knows the other exists.
 *
 * Null rather than an empty string, so a caller that names an id nothing declares gets a tooltip
 * with no keys on it rather than one ending in a dangling separator.
 */
export function keysFor(id: string): string | null {
	return SHORTCUTS.find((one) => one.id === id)?.shown ?? null;
}

export const SHORTCUTS: readonly Shortcut[] = [
	{
		id: 'app.dismiss',
		keys: ['Escape'],
		shown: 'Esc',
		does: 'Close whatever is open — a panel, a menu, or a sheet',
		area: 'Anywhere'
	},
	{
		id: 'app.search',
		keys: ['f', 'F'],
		ctrl: true,
		shift: false,
		shown: 'Ctrl + F',
		does: 'Search your library',
		area: 'Anywhere'
	},
	/*
	 * The two positions of the switch in front of the search field, from the field itself: the
	 * sparkle is on the left and the glass on the right, so each arrow moves the switch the way it
	 * points. Shift is left out, so Ctrl + Shift + an arrow still selects a word at a time.
	 */
	{
		id: 'search.byMeaning',
		keys: ['ArrowLeft'],
		ctrl: true,
		shift: false,
		whileTyping: true,
		shown: 'Ctrl + Left arrow',
		does: 'Search by what things look like',
		area: 'In the search box'
	},
	{
		id: 'search.forWords',
		keys: ['ArrowRight'],
		ctrl: true,
		shift: false,
		whileTyping: true,
		shown: 'Ctrl + Right arrow',
		does: 'Search for the words',
		area: 'In the search box'
	},
	{
		id: 'app.shutHidden',
		keys: ['h', 'H'],
		ctrl: true,
		shift: false,
		whileTyping: true,
		shown: 'Ctrl + H',
		does: 'Shut Hidden — hidden files go off the screen, and you stay signed in',
		area: 'Anywhere'
	},
	{
		id: 'app.lock',
		keys: ['l', 'L'],
		ctrl: true,
		shift: false,
		whileTyping: true,
		shown: 'Ctrl + L',
		does: 'Lock Sift — the session shuts, and Hidden shuts with it',
		area: 'Anywhere'
	},
	{
		id: 'vault.panicLock',
		keys: ['L'],
		ctrl: true,
		shift: true,
		whileTyping: true,
		shown: 'Ctrl + Shift + L',
		does: 'Hide everything now — the panic key, and it works from inside a text box',
		area: 'Anywhere'
	},
	{
		id: 'stage.toggleBar',
		keys: ['b', 'B'],
		shown: 'B',
		does: 'Send the controls away, or bring them back — on Theater, and while the screen is filled',
		area: 'Anywhere'
	},
	{
		id: 'view.previous',
		keys: ['ArrowLeft'],
		shown: 'Left arrow',
		does: 'The file before this one — hold Shift or Ctrl for that while a video is open, where a bare arrow steps through it instead',
		area: 'Looking at a file'
	},
	{
		id: 'view.next',
		keys: ['ArrowRight'],
		shown: 'Right arrow',
		does: 'The file after this one — hold Shift or Ctrl for that while a video is open',
		area: 'Looking at a file'
	},
	{
		id: 'player.playPause',
		keys: [' ', 'Spacebar'],
		shift: false,
		shown: 'Space',
		does: 'Play or pause',
		area: 'Watching something'
	},
	{
		id: 'player.back',
		keys: ['ArrowLeft'],
		shift: false,
		shown: 'Left arrow',
		does: 'Step back',
		area: 'Watching something'
	},
	{
		id: 'player.forward',
		keys: ['ArrowRight'],
		shift: false,
		shown: 'Right arrow',
		does: 'Step forward',
		area: 'Watching something'
	},
	{
		id: 'player.mute',
		keys: ['m', 'M'],
		shift: false,
		shown: 'M',
		does: 'Mute or unmute',
		area: 'Watching something'
	},
	{
		id: 'player.corner',
		keys: ['i', 'I'],
		shift: false,
		shown: 'I',
		does: 'Open mini player, or go back to full size',
		area: 'Watching something'
	},
	{
		id: 'player.audioOnly',
		keys: ['a', 'A'],
		shift: false,
		shown: 'A',
		does: 'Open audio player, or go back to the mini player',
		area: 'Watching something'
	},
	{
		id: 'player.save',
		keys: ['s', 'S'],
		ctrl: true,
		shift: false,
		shown: 'Ctrl + S',
		does: 'Save what you are watching to this device',
		area: 'Watching something'
	},
	{
		id: 'player.repeat',
		keys: ['r', 'R'],
		shift: false,
		shown: 'R',
		does: 'Repeat: off, the whole list, or this one file',
		area: 'Watching something'
	},
	{
		/*
		 * S ON ITS OWN, beside the Ctrl + S above it that saves the file.
		 *
		 * The two cannot be confused: `matches` turns a bare key down the moment Ctrl or Cmd is held
		 * and turns a Ctrl shortcut down when it is not, so one letter carries both and neither can
		 * fire for the other's press. S is the letter shuffle is spelled with everywhere else a
		 * player has one, which is worth more here than avoiding a shared letter.
		 */
		id: 'player.shuffle',
		keys: ['s', 'S'],
		shift: false,
		shown: 'S',
		does: 'Play what is on screen in a random order, or back in order',
		area: 'Watching something'
	},
	{
		id: 'player.loop',
		keys: ['l', 'L'],
		shift: false,
		shown: 'L',
		does: 'Mark the start of a loop, then its end, then clear it',
		area: 'Watching something'
	},
	{
		/*
		 * THE FOUR VOLUME KEYS, and why they hang off a held M.
		 *
		 * The same arrangement Theater has, key for key, because the two surfaces are the same job:
		 * the arrows already mean "step through the clip" here, so a volume key cannot be a bare
		 * arrow, and M is the mute key, which is the one letter already about how loud this is.
		 * Held it is the modifier; on its own it still mutes, settled when it is LET GO.
		 */
		id: 'player.louder',
		keys: ['ArrowUp'],
		shift: false,
		shown: 'M + Up',
		does: 'Ten louder. Only while M is held',
		area: 'Watching something'
	},
	{
		id: 'player.quieter',
		keys: ['ArrowDown'],
		shift: false,
		shown: 'M + Down',
		does: 'Ten quieter. Only while M is held',
		area: 'Watching something'
	},
	{
		id: 'player.louderABit',
		keys: ['ArrowRight'],
		shift: false,
		shown: 'M + Right',
		does: 'Two louder. Only while M is held',
		area: 'Watching something'
	},
	{
		id: 'player.quieterABit',
		keys: ['ArrowLeft'],
		shift: false,
		shown: 'M + Left',
		does: 'Two quieter. Only while M is held',
		area: 'Watching something'
	},
	{
		/* Ctrl and an arrow, which is what Theater's two step keys are: a bare arrow is five
		   seconds through the clip on both surfaces, and a whole file is not a difference somebody
		   can undo by pressing again. Shift and an arrow does the same thing from the screen around
		   the player (`view.previous`), and both are kept: one is the file's own screen stepping
		   through a list, this one is the player under your hand. */
		id: 'player.previous',
		keys: ['ArrowLeft'],
		ctrl: true,
		shift: false,
		shown: 'Ctrl + Left',
		does: 'The file before this one, on every kind of file',
		area: 'Watching something'
	},
	{
		id: 'player.next',
		keys: ['ArrowRight'],
		ctrl: true,
		shift: false,
		shown: 'Ctrl + Right',
		does: 'The file after this one, on every kind of file',
		area: 'Watching something'
	},
	{
		id: 'player.fill',
		keys: ['f', 'F'],
		shift: false,
		shown: 'F',
		does: 'Full screen, or leave full screen',
		area: 'Watching something'
	},
	{
		/*
		 * ONE ROW FOR FIVE VERBS, and the alternative is worse.
		 *
		 * A number key means five things depending on how it is pressed: once, twice, three
		 * times, or held on any of those. Each of them could have a row of its own and the sheet
		 * would read better for it. What it would cost is the property this whole file exists for:
		 * a row is what `matches` reads, and a row called "the next file" carrying the digits would
		 * answer TRUE to a single press of 2, because a key is all `matches` can see. An id that
		 * must never be passed to the one function that reads ids is a trap with a name.
		 *
		 * So the keys are declared once, here, and which verb a press turns out to be is
		 * `$lib/theater/taps`'s question: a clock, which no key declaration can express. The
		 * sentence carries the gesture, and the badge over the cell says which one happened.
		 */
		id: 'theater.cell',
		keys: ['1', '2', '3', '4', '5', '6', '7', '8', '9'],
		shown: '1 to 9',
		does: 'Control that cell. Press twice for its next file, three times for the one before, or hold for slow motion, faster, or backwards',
		area: 'Theater'
	},
	{
		id: 'theater.everyCell',
		keys: ['`'],
		shown: 'Backtick',
		does: 'Control all cells together, with the same keys',
		area: 'Theater'
	},
	{
		id: 'theater.mute',
		keys: ['m', 'M'],
		shown: 'M',
		/* Held, it becomes the modifier for the two below, so what it does on its own is settled
		   when it is LET GO, and only if no arrow was pressed while it was down. */
		does: 'Mute or unmute the cell you are on. Hold it and use the arrows to change its volume',
		area: 'Theater'
	},
	{
		id: 'theater.muteAll',
		keys: ['m', 'M'],
		ctrl: true,
		shown: 'Ctrl + M',
		does: 'Mute everything, or unmute everything',
		area: 'Theater'
	},
	{
		id: 'theater.louder',
		keys: ['ArrowUp'],
		shown: 'M + Up',
		does: 'Ten louder, on the cell you are on. Only while M is held',
		area: 'Theater'
	},
	{
		id: 'theater.quieter',
		keys: ['ArrowDown'],
		shown: 'M + Down',
		does: 'Ten quieter, on the cell you are on. Only while M is held',
		area: 'Theater'
	},
	{
		id: 'theater.louderABit',
		keys: ['ArrowRight'],
		shown: 'M + Right',
		does: 'Two louder, on the cell you are on. Only while M is held',
		area: 'Theater'
	},
	{
		id: 'theater.quieterABit',
		keys: ['ArrowLeft'],
		shown: 'M + Left',
		does: 'Two quieter, on the cell you are on. Only while M is held',
		area: 'Theater'
	},
	{
		id: 'theater.repeat',
		keys: ['r', 'R'],
		shown: 'R',
		does: 'Repeat: off, the whole run, or this one file',
		area: 'Theater'
	},
	{
		id: 'theater.loop',
		keys: ['l', 'L'],
		shown: 'L',
		does: 'Mark the start of a loop, then its end, then clear it',
		area: 'Theater'
	},
	{
		id: 'theater.pauseAll',
		keys: [' ', 'Spacebar'],
		ctrl: true,
		shown: 'Ctrl + Space',
		does: 'Stop everything, including every timer',
		area: 'Theater'
	},
	{
		id: 'theater.pause',
		keys: [' ', 'Spacebar'],
		shown: 'Space',
		does: 'Stop the cell you are on, and nothing else',
		area: 'Theater'
	},
	{
		id: 'theater.back',
		keys: ['ArrowLeft'],
		shown: 'Left',
		does: 'Five seconds back, in the cell you are on',
		area: 'Theater'
	},
	{
		id: 'theater.forward',
		keys: ['ArrowRight'],
		shown: 'Right',
		does: 'Five seconds on, in the cell you are on',
		area: 'Theater'
	},
	{
		id: 'theater.previous',
		keys: ['ArrowLeft'],
		ctrl: true,
		shown: 'Ctrl + Left',
		does: 'Back to what this cell played before',
		area: 'Theater'
	},
	{
		id: 'theater.next',
		keys: ['ArrowRight'],
		ctrl: true,
		shown: 'Ctrl + Right',
		does: 'On to the next file in this cell',
		area: 'Theater'
	},
	{
		id: 'theater.fill',
		keys: ['f', 'F'],
		shown: 'F',
		does: 'Full screen, or leave full screen',
		area: 'Theater'
	},
	{
		id: 'theater.corner',
		keys: ['i', 'I'],
		shown: 'I',
		does: 'Open the wall in the mini player, or go back to full size',
		area: 'Theater'
	},
	{
		id: 'theater.keys',
		keys: ['h', 'H'],
		shown: 'H',
		does: 'This list, and away again',
		area: 'Theater'
	},
	{
		id: 'select.undo',
		keys: ['z', 'Z'],
		ctrl: true,
		shift: false,
		shown: 'Ctrl + Z',
		does: 'Take back the last thing you picked, or unpicked',
		area: 'Picking things'
	},
	{
		id: 'select.redo',
		keys: ['z', 'Z'],
		ctrl: true,
		shift: true,
		shown: 'Ctrl + Shift + Z',
		does: 'Do that picking again',
		area: 'Picking things'
	},
	{
		id: 'select.all',
		keys: ['a', 'A'],
		ctrl: true,
		shift: false,
		shown: 'Ctrl + A',
		does: 'Pick everything on the page, or let it all go',
		area: 'Picking things'
	}
];

const BY_ID = new Map<ShortcutId, Shortcut>(SHORTCUTS.map((one) => [one.id, one]));

/** One shortcut, by name. Throws on a name that is not declared, which is a typo rather than a state. */
export function shortcut(id: ShortcutId): Shortcut {
	const found = BY_ID.get(id);
	if (!found) throw new Error(`no shortcut is declared as ${id}`);
	return found;
}

/**
 * Whether somebody is typing into something, rather than pressing a shortcut.
 *
 * Exported because the shell, both players and the tile gesture all ask it, and four copies of a
 * rule about what counts as a text box are four chances for one of them to miss `contenteditable`.
 */
export function typingInto(target: EventTarget | null): boolean {
	const element = target as HTMLElement | null;
	if (!element?.tagName) return false;
	if (element.isContentEditable) return true;
	return ['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName);
}

/**
 * Whether this press is that shortcut.
 *
 * The whole of the matching, in one place: the key, the modifiers, and whether somebody is typing.
 * A call site that compared `event.key` itself would be a second answer to the same question, and
 * would be the one left behind the day a binding becomes something somebody can change.
 *
 * It deliberately does NOT call `preventDefault`. Whether the browser's own behaviour should be
 * suppressed depends on what the caller is about to do (Space belongs to a focused button, and
 * Escape leaving full screen is the browser's answer and must stay that way), so that decision
 * stays where the context is.
 */
export function matches(event: KeyboardEvent, id: ShortcutId): boolean {
	const one = shortcut(id);
	if (!one.keys.includes(event.key)) return false;
	if (event.altKey) return false;
	const held = event.ctrlKey || event.metaKey;
	if (one.ctrl ? !held : held) return false;
	if (one.shift !== undefined && one.shift !== event.shiftKey) return false;
	if (!one.whileTyping && typingInto(event.target)) return false;
	return true;
}

/** The file either side a press asks for: Ctrl or Shift on any kind, bare where no clip seeks. */
export function stepAsked(
	event: KeyboardEvent,
	on: { clip: boolean; clipSteps: boolean }
): 'next' | 'previous' | null {
	const next = matches(event, 'view.next') || matches(event, 'player.next');
	if (!next && !matches(event, 'view.previous') && !matches(event, 'player.previous')) return null;
	const ctrl = event.ctrlKey || event.metaKey;
	if (on.clip && !event.shiftKey && (!ctrl || on.clipSteps)) return null;
	return next ? 'next' : 'previous';
}

/** Every shortcut in an area, for a list that shows them grouped. */
export function shortcutsIn(
	area: Area,
	among: readonly Shortcut[] = SHORTCUTS
): readonly Shortcut[] {
	return among.filter((one) => one.area === area);
}

/**
 * Every area that has any shortcuts, with them, in the declared order.
 *
 * What a settings section showing the whole list is built from. An area with nothing in it is left
 * out rather than drawn empty: a heading over nothing is a promise the screen cannot keep.
 *
 * ## Why it takes the list it groups
 *
 * A LINE THAT CANNOT FAIL IS A LINE NOTHING KNOWS IS RIGHT. Reading `SHORTCUTS` directly, the
 * emptiness it guards against is unreachable: all five declared areas have keys, so removing the
 * `.filter` would leave this function's test AND the pane's test green. A comment saying so is not
 * a check.
 *
 * A default parameter rather than a mock, and rather than dropping the guard. This is a pure
 * grouping of a list; taking the list is its ordinary shape, not a seam cut for a test. The
 * settings search's `grouped(index, sections, typed)` is the same function written the same way,
 * and it is testable for the same reason. Every caller in the application passes nothing and gets
 * the real list.
 */
export function shortcutsByArea(
	among: readonly Shortcut[] = SHORTCUTS
): { area: Area; shortcuts: readonly Shortcut[] }[] {
	return AREAS.map((area) => ({ area, shortcuts: shortcutsIn(area, among) })).filter(
		(group) => group.shortcuts.length > 0
	);
}

/*
 * ## One table of actions per surface, answering the keyboard and the phone alike
 *
 * The player and Theater each have verbs (play, step, the next file, mute the wall) and two ways
 * of being asked for them: a key pressed at the desk, and a command from the signed-in user's
 * phone. Written as a branch inside a keyboard handler, each verb would need a second set of
 * branches beside it for a remote, doing the same things by other means: two
 * copies of every verb, and the one only a phone reaches is the one nobody would notice breaking.
 *
 * So each surface declares ONE table: an action's name, and what it does. A key press is matched
 * against the table's rows in the order they are written, and a phone's command names its row
 * directly. The names are the shortcut names above, plus the verbs a phone has and no key does
 * (a scrubber and a volume slider need a number, and a key has none; the drawer's presses have no
 * letter).
 */

/**
 * Every verb the player has: its shortcuts, and the ones only a phone asks for, because no key
 * does (a scrubber and a volume slider need a number, and the drawer's presses have no letter).
 */
export type PlayerAction =
	| Extract<ShortcutId, `player.${string}`>
	| 'player.seekTo'
	| 'player.volumeTo'
	| 'player.favorite'
	| 'player.count'
	| 'player.saveLoop'
	| 'player.clip'
	| 'player.random'
	| 'player.quality';

/** Every verb a Theater wall has: its shortcuts, and the ones only a phone asks for. */
export type TheaterAction =
	| Extract<ShortcutId, `theater.${string}`>
	| 'theater.seekTo'
	| 'theater.volumeTo'
	| 'theater.shuffle'
	| 'theater.saveLoop'
	| 'theater.random'
	| 'theater.quality'
	| 'theater.solo'
	| 'theater.timer'
	| 'theater.layout'
	| 'theater.preset';

type SurfaceAction = PlayerAction | TheaterAction;

/**
 * How an action was asked for.
 *
 * `key` is the press when it was a key, and null when it was the phone. `value` is what the phone
 * sent with it, already bounded by the server: a state wanted (1 on, 0 off) where the key is a
 * toggle, seconds for a step or a seek, a percent for the volume, a cell counted from 0.
 */
export type Asked = { key: KeyboardEvent; value: null } | { key: null; value: number | null };

/**
 * One surface's verbs, and what each does. A row answers true when it acted, which is what takes
 * a key press away from the browser; false means "not mine", and the next row is asked.
 */
export type Actions<A extends SurfaceAction> = { [Id in A]?: (asked: Asked) => boolean };

/** Either surface's table, for the two functions below that serve both. */
type AnyActions = Actions<PlayerAction> | Actions<TheaterAction>;

/**
 * The verbs a phone may ask for, exactly as the server names them.
 *
 * Held to the server's own list by a test on the server side, which reads this array. A verb in
 * one list and not the other is a button on the phone that the desk never answers.
 */
export const REMOTE_ACTIONS = [
	'player.playPause',
	'player.back',
	'player.forward',
	'player.seekTo',
	'player.previous',
	'player.next',
	'player.volumeTo',
	'player.mute',
	'player.fill',
	// The two the viewer answers itself, whatever it shows: a favourite (value 1 or 0,
	// the state wanted) and one more on the O counter. A picture has these as much as a clip does.
	'player.favorite',
	'player.count',
	'theater.pauseAll',
	'theater.muteAll',
	'theater.cell',
	'theater.previous',
	'theater.next',
	/* The rest of the player's bar and drawer, so the phone reaches every press the desk has. A
	   toggle carries the state wanted; repeat carries its place in the order the press cycles
	   through (`LOOP_MODES`); a size, a layout or a preset carries its place in the list the screen
	   reported. */
	'player.repeat',
	'player.shuffle',
	'player.loop',
	'player.saveLoop',
	'player.clip',
	'player.random',
	'player.quality',
	'player.corner',
	// And the wall's: the cell being talked to (or every cell), its sound, its place, its drawer.
	'theater.pause',
	'theater.mute',
	'theater.back',
	'theater.forward',
	'theater.seekTo',
	'theater.volumeTo',
	'theater.repeat',
	'theater.shuffle',
	'theater.loop',
	'theater.saveLoop',
	'theater.random',
	'theater.quality',
	'theater.solo',
	'theater.timer',
	'theater.everyCell',
	'theater.layout',
	'theater.preset',
	'theater.corner'
] as const satisfies readonly SurfaceAction[];

export type RemoteAction = (typeof REMOTE_ACTIONS)[number];

/**
 * Answer a key press from a surface's table: the first row whose shortcut this press is, and
 * that acts. True when one did, and then the browser's own use of the key is stopped here, once,
 * rather than by every row remembering to.
 *
 * Rows are asked in the order the table writes them, which is the whole of how keys that share a
 * letter are told apart: the volume rows come before the step rows because both are arrows, and
 * a volume row declines unless M is held. A row with no shortcut (the phone's own) is never asked.
 */
export function pressed<A extends SurfaceAction>(event: KeyboardEvent, table: Actions<A>): boolean {
	for (const [id, run] of Object.entries(table) as [A, (asked: Asked) => boolean][]) {
		if (!BY_ID.has(id as ShortcutId)) continue;
		if (!matches(event, id as ShortcutId)) continue;
		if (!run({ key: event, value: null })) continue;
		event.preventDefault();
		return true;
	}
	return false;
}

/**
 * Answer a command from the phone from the same table. True when the row was there and acted.
 *
 * The action arrives as a string off the wire, and a name this table does not have is answered
 * false rather than guessed at: a desktop one version behind a phone may be asked for a verb it
 * has never heard of.
 */
export function commanded(table: AnyActions, action: string, value: number | null): boolean {
	const run = (table as Record<string, ((asked: Asked) => boolean) | undefined>)[action];
	return run !== undefined && run({ key: null, value });
}

/**
 * The verbs a surface can offer the phone: every row of its table the phone has a name for.
 *
 * Read off the table rather than listed beside it, so a surface cannot offer a verb it does not
 * answer, and a row added to the table is offered without anybody remembering to.
 */
export function offerable(table: AnyActions): RemoteAction[] {
	const rows = new Set<string>(Object.keys(table));
	return REMOTE_ACTIONS.filter((action) => rows.has(action));
}
