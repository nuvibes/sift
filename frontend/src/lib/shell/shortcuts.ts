/*
 * Every keyboard shortcut in Sift, declared once: call sites ask `matches(event, id)` and every
 * list is generated from here, so a sheet cannot disagree with the keys, and a binding can one day
 * be read in this one place. A widget's own keys are not shortcuts; Escape is here once.
 */

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

export const AREAS = [
	'Anywhere',
	'In the search box',
	'Picking things',
	'Looking at a file',
	'Watching something',
	'Theater'
] as const;

/* Not exported: every use is in this file (`public-surface.test.ts`). */
type Area = (typeof AREAS)[number];

export interface Shortcut {
	id: ShortcutId;
	/** Several spellings of one press; where case means two things, two shortcuts. */
	keys: readonly string[];
	/** Absent means it must NOT be held, nor Alt or Meta, so no browser combination is stolen. */
	ctrl?: true;
	/**
	 * Undefined: not consulted; false: refused; true: required (redo, which Caps Lock cannot fake).
	 */
	shift?: boolean;
	/** False nearly everywhere; the two locks fire while typing, needing Ctrl. */
	whileTyping?: true;
	/** Written out: `' '` reads as Space. */
	shown: string;
	does: string;
	area: Area;
}

/** How a shortcut reads, for a tooltip, or null for an undeclared id. */
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
	 * The search field's switch: each arrow moves it the way it points; Shift is left for
	 * selecting.
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
		/* S on its own beside Ctrl + S: `matches` keeps the two apart. */
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
		 * The volume keys hang off a held M, as in Theater: the arrows already step; M settles on
		 * release.
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
		/* Ctrl and an arrow, as in Theater; Shift and an arrow is the screen around the player. */
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
		 * ONE ROW FOR FIVE VERBS: which a press is is `$lib/theater/taps`'s clock, not a key
		 * declaration.
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
		/* Held, it becomes the modifier, so its own act is settled on release. */
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

export function shortcut(id: ShortcutId): Shortcut {
	const found = BY_ID.get(id);
	if (!found) throw new Error(`no shortcut is declared as ${id}`);
	return found;
}

/** The one rule for what counts as a text box. */
export function typingInto(target: EventTarget | null): boolean {
	const element = target as HTMLElement | null;
	if (!element?.tagName) return false;
	if (element.isContentEditable) return true;
	return ['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName);
}

/**
 * The key, the modifiers and typing, in one place; never `preventDefault`, which is the caller's.
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

export function shortcutsIn(
	area: Area,
	among: readonly Shortcut[] = SHORTCUTS
): readonly Shortcut[] {
	return among.filter((one) => one.area === area);
}

/** Every area with shortcuts; taking the list makes the empty-area guard testable. */
export function shortcutsByArea(
	among: readonly Shortcut[] = SHORTCUTS
): { area: Area; shortcuts: readonly Shortcut[] }[] {
	return AREAS.map((area) => ({ area, shortcuts: shortcutsIn(area, among) })).filter(
		(group) => group.shortcuts.length > 0
	);
}

/*
 * ONE table of actions per surface, answering a key and the phone alike, so a verb only a phone
 * reaches cannot break unnoticed.
 */

/** Shortcuts, plus what only a phone asks for. */
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

/** `key` null means the phone; `value` is bounded by the server. */
export type Asked = { key: KeyboardEvent; value: null } | { key: null; value: number | null };

/** True when it acted, which takes the key from the browser. */
export type Actions<A extends SurfaceAction> = { [Id in A]?: (asked: Asked) => boolean };

type AnyActions = Actions<PlayerAction> | Actions<TheaterAction>;

/** Held to the server's own list by a server-side test. */
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
	'player.favorite',
	'player.count',
	'theater.pauseAll',
	'theater.muteAll',
	'theater.cell',
	'theater.previous',
	'theater.next',
	'player.repeat',
	'player.shuffle',
	'player.loop',
	'player.saveLoop',
	'player.clip',
	'player.random',
	'player.quality',
	'player.corner',
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
 * Rows in table order, which tells shared letters apart; the browser's use is stopped once here.
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

/** An unknown name is false: an older desktop may be asked for a newer verb. */
export function commanded(table: AnyActions, action: string, value: number | null): boolean {
	const run = (table as Record<string, ((asked: Asked) => boolean) | undefined>)[action];
	return run !== undefined && run({ key: null, value });
}

/** Read off the table, so nothing is offered that is not answered. */
export function offerable(table: AnyActions): RemoteAction[] {
	const rows = new Set<string>(Object.keys(table));
	return REMOTE_ACTIONS.filter((action) => rows.has(action));
}
