// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The words of every act a player offers, and the key each answers on each keyboard (`ACT_KEYS`),
 * declared once. `acts-gate.test.ts` refuses a player's own copy, and the retired names anywhere.
 */

import type { ShortcutId } from '$lib/shell/shortcuts';

export const ACTS = {
	play: 'Play',
	pause: 'Pause',
	previous: 'Previous',
	next: 'Next',
	mute: 'Mute',
	unmute: 'Unmute',
	playEverything: 'Play everything',
	pauseEverything: 'Pause everything',
	muteEverything: 'Mute everything',
	unmuteEverything: 'Unmute everything',
	fullScreen: 'Full screen',
	leaveFullScreen: 'Leave full screen',
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
	everyCell: 'All cells'
} as const;

export type Act = keyof typeof ACTS;

/**
 * `mini` answers the player's keys less the step pair and Shuffle; `picture`'s bare arrows step
 * files.
 */
export type Keyboard = 'player' | 'mini' | 'picture' | 'theater';

/** No key where an act or a keyboard is absent. */
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

export function keyOf(act: Act, keyboard: Keyboard): ShortcutId | undefined {
	return ACT_KEYS[act]?.[keyboard];
}

/** One glyph, one act, so the gate can tell a button that IS the act. */
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

/** Retired names, refused anywhere in the client. */
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
