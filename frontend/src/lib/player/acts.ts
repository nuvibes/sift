// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The words of every act a player offers, declared once, and every player reads them from here.
 *
 * ## One act, one tooltip
 *
 * Sift has several players: the Player (on the file page and in its own window), the mini player,
 * the Audio player, Theater's wall and its cells, the phone's viewer and its Remote. The same press
 * turns up on several of them, and words each wrote for itself would let the one act of filling
 * the screen read "Full screen" on one and "Fill the screen" on another. Somebody who learns a
 * press on one player should meet the same words for it on the next.
 *
 * So the words live here and nowhere else. A player names `ACTS.fullScreen`, never the words
 * themselves, and `acts-gate.test.ts` holds it: inside a player a string equal to one of these
 * words is refused (it must be read from here), a button wearing an act's glyph takes its words
 * from here, and anywhere in the client the retired second names below are refused.
 *
 * A dimmed control keeps its reason as its words ("A picture has no seconds to clip"); a reason is
 * about what is on screen, not a name for the act.
 *
 * ## One act, one key, on every player that answers it
 *
 * An act with a key shows it on its tooltip, and the key comes from here too: `ACT_KEYS` joins each
 * act to the shortcut that answers it on each keyboard (`shortcuts.ts` declares the keys), and a
 * tooltip asks `keyOf(act, keyboard)`. Written by hand beside each button, the keys would go
 * uneven: I on one "Open mini player" and nothing on another that answers I as well. A player that does not answer a key on some screen (a mini
 * player with nothing playing has no M) passes no key there, never a different one. The gate holds
 * a tooltip inside a player to reading its key from here.
 */

import type { ShortcutId } from '$lib/shell/shortcuts';

/** Every act's words, as its tooltip and its button's accessible name both say them. */
export const ACTS = {
	play: 'Play',
	pause: 'Pause',
	previous: 'Previous',
	next: 'Next',
	mute: 'Mute',
	unmute: 'Unmute',
	/* Theater's tools, over the whole wall: the player's own words with "everything". */
	playEverything: 'Play everything',
	pauseEverything: 'Pause everything',
	muteEverything: 'Mute everything',
	unmuteEverything: 'Unmute everything',
	fullScreen: 'Full screen',
	leaveFullScreen: 'Leave full screen',
	/* The three sizes of a player: full size, the mini player, and the Audio player (the strip
	   along the foot of the window that keeps the sound). */
	miniPlayer: 'Open mini player',
	audioPlayer: 'Open audio player',
	fullSize: 'Back to full size',
	close: 'Close',
	shuffle: 'Shuffle',
	randomize: 'Randomize',
	clip: 'Clip',
	screenshot: 'Screenshot',
	quality: 'Quality',
	stats: 'Stats for nerds',
	saveLoop: 'Save as Loop',
	/* Theater's chip that points the bar's controls at the whole wall. */
	everyCell: 'Every cell'
} as const;

export type Act = keyof typeof ACTS;

/**
 * Whose keys a player answers. `player`: the full-size Player, where Ctrl + Left is the file
 * before. `mini`: the mini player and the Audio player, which answer the player's keys less the
 * file before and after and Shuffle. `picture`: a picture's bar, where the bare arrows are the
 * viewer's own and step between files. `theater`: Theater's wall and its cells.
 */
export type Keyboard = 'player' | 'mini' | 'picture' | 'theater';

/**
 * The shortcut that answers each act, on each keyboard that has one. An act absent here, or a
 * keyboard absent under it, has no key there, and its tooltip shows none.
 */
export const ACT_KEYS: Readonly<
	Partial<Record<Act, Readonly<Partial<Record<Keyboard, ShortcutId>>>>>
> = {
	play: {
		player: 'player.playPause',
		mini: 'player.playPause',
		picture: 'player.playPause',
		theater: 'theater.pause'
	},
	pause: {
		player: 'player.playPause',
		mini: 'player.playPause',
		picture: 'player.playPause',
		theater: 'theater.pause'
	},
	previous: { player: 'player.previous', picture: 'view.previous', theater: 'theater.previous' },
	next: { player: 'player.next', picture: 'view.next', theater: 'theater.next' },
	mute: { player: 'player.mute', mini: 'player.mute', theater: 'theater.mute' },
	unmute: { player: 'player.mute', mini: 'player.mute', theater: 'theater.mute' },
	playEverything: { theater: 'theater.pauseAll' },
	pauseEverything: { theater: 'theater.pauseAll' },
	muteEverything: { theater: 'theater.muteAll' },
	unmuteEverything: { theater: 'theater.muteAll' },
	fullScreen: { player: 'player.fill', picture: 'player.fill', theater: 'theater.fill' },
	leaveFullScreen: { player: 'player.fill', picture: 'player.fill', theater: 'theater.fill' },
	miniPlayer: {
		player: 'player.corner',
		mini: 'player.corner',
		picture: 'player.corner',
		theater: 'theater.corner'
	},
	fullSize: { mini: 'player.corner', theater: 'theater.corner' },
	audioPlayer: { player: 'player.audioOnly', mini: 'player.audioOnly' },
	shuffle: { player: 'player.shuffle', picture: 'player.shuffle' },
	everyCell: { theater: 'theater.everyCell' }
};

/** The key a tooltip for `act` shows on `keyboard`, or undefined where that keyboard has none. */
export function keyOf(act: Act, keyboard: Keyboard): ShortcutId | undefined {
	return ACT_KEYS[act]?.[keyboard];
}

/**
 * The glyph each act wears on a player, so the gate can tell a button that IS the act from one
 * that only sits beside it. One glyph, one act: a button wearing it names that act from `ACTS`.
 */
export const ACT_GLYPHS: Readonly<Record<string, readonly Act[]>> = {
	play_arrow: ['play', 'pause'],
	pause: ['play', 'pause'],
	skip_previous: ['previous'],
	skip_next: ['next'],
	volume_up: ['mute', 'unmute', 'muteEverything', 'unmuteEverything'],
	volume_off: ['mute', 'unmute', 'muteEverything', 'unmuteEverything'],
	autoplay: ['playEverything', 'pauseEverything'],
	autostop: ['playEverything', 'pauseEverything'],
	fullscreen: ['fullScreen', 'leaveFullScreen'],
	fullscreen_exit: ['fullScreen', 'leaveFullScreen'],
	picture_in_picture: ['miniPlayer', 'fullSize'],
	cadence: ['audioPlayer'],
	open_in_full: ['fullSize'],
	close: ['close'],
	shuffle: ['shuffle'],
	casino: ['randomize'],
	content_cut: ['clip'],
	screenshot_region: ['screenshot'],
	video_settings: ['quality'],
	cognition_2: ['stats'],
	bookmark_add: ['saveLoop']
};

/**
 * The second names an act has had, each with the act that replaced it. Refused anywhere in the
 * client as a tooltip or a name, so a player cannot drift back to its own words for a shared act.
 */
export const RETIRED: Readonly<Record<string, Act>> = {
	'Fill the screen': 'fullScreen',
	'Go fullscreen': 'fullScreen',
	'Go full screen': 'fullScreen',
	Fullscreen: 'fullScreen',
	'Leave fullscreen': 'leaveFullScreen',
	'Exit full screen': 'leaveFullScreen',
	'Audio only': 'audioPlayer',
	'Show the picture': 'miniPlayer',
	'Send the wall to the corner': 'miniPlayer',
	'Bring the wall back': 'fullSize',
	'Close the mini player': 'close',
	'Back one': 'previous',
	'Silence everything': 'muteEverything'
};
