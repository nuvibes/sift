// SPDX-License-Identifier: AGPL-3.0-or-later
/* The Remote's words, in the desk's own words for the same verbs (`ACTS` for the acts); the
 * one-word-per-thing gate reads this file. */
import type { ScreenOut } from './wire';
import { SKIP_SECONDS } from '$lib/player/skip';
import { ACTS } from '$lib/player/acts';
import { sayLength } from '$lib/shell/duration';

export const COPY = {
	title: 'Remote',
	emptyTitle: 'Nothing to control',
	empty:
		"Open a video or a Theater wall in Sift's app on your computer, signed in as you, and it shows up here. A browser tab shows up too once Let your phone control this browser is on in its Settings > Playback.",
	screens: 'Screens',
	/* A browser tab holding a player or a wall back, because its own switch is off. */
	notOffering:
		"A browser signed in on another computer isn't offering its screens. Turn on Settings > Playback > Remote > Let your phone control this browser there.",
	hidden: 'Something Hidden is playing',
	playing: 'Playing',
	paused: 'Paused',
	wall: 'Theater wall',
	player: 'Player',
	position: 'Position',
	/* Why a wall's scrubber is dimmed under Every cell: one position cannot be every cell's. */
	positionOfEvery: 'Cells have different lengths. Choose one cell to move through it.',
	volume: 'Volume',
	previous: ACTS.previous,
	next: ACTS.next,
	play: ACTS.play,
	pause: ACTS.pause,
	back: `Back ${SKIP_SECONDS} seconds`,
	forward: `Forward ${SKIP_SECONDS} seconds`,
	mute: ACTS.mute,
	unmute: ACTS.unmute,
	fill: ACTS.fullScreen,
	corner: ACTS.miniPlayer,
	playAll: ACTS.playEverything,
	pauseAll: ACTS.pauseEverything,
	silenceAll: ACTS.muteEverything,
	soundOn: ACTS.unmuteEverything,
	cells: 'Cells',
	everyCell: ACTS.everyCell,
	layouts: 'Layouts',
	presets: 'Saved Layouts',
	noPresets: 'No Saved Layouts yet',
	oneMore: 'One more',
	drawer: 'More controls',
	clip: ACTS.clip,
	screenshot: ACTS.screenshot,
	quality: ACTS.quality,
	oneSize: 'This file has one size',
	shuffle: ACTS.shuffle,
	randomize: ACTS.randomize,
	saveLoop: ACTS.saveLoop,
	markBoth: 'Mark both ends of a loop to save it',
	stats: ACTS.stats,
	solo: 'Hear only this',
	timer: 'Move on after',
	noTimer: 'No timer',
	/* Why a control is dimmed; the first three are presses only the desk can make. */
	fillAtTheDesk: 'Full screen needs a press on your computer',
	screenshotAtTheDesk: 'Screenshots are taken on your computer',
	statsAtTheDesk: 'Stats for nerds opens on your computer',
	notHere: 'Not available for what is showing'
} as const;

/** The A-B loop's one press, in the words the desk's button says for each of its three steps. */
export const LOOP_STEPS = ['Set the loop start', 'Set the loop end', 'Clear the loop'] as const;

/** The timer's choices on the phone, in seconds: none, then the lengths a wall is usually left on. */
export const TIMER_SECONDS = [0, 10, 30, 60, 300] as const;

export function timerWords(seconds: number): string {
	if (seconds === 0) return COPY.noTimer;
	return `After ${sayLength(seconds)}`;
}

/** The clip's choices: the desk's own menu words for the same lengths. */
export function clipWords(seconds: number): string {
	return `Last ${seconds} seconds`;
}

/** The O counter's name with its figure, as the file's own counter says it out loud. */
export function counterName(count: number): string {
	return `O counter: ${count}`;
}

/** A wall's cell, counted from one as the wall's own cells are named. */
export function cellLabel(index: number): string {
	return `Cell ${index + 1}`;
}

/** What a screen plays in words the phone may say: its name, Something Hidden, or null. */
export function whatPlays(
	screen: Pick<ScreenOut, 'file' | 'hidden'>,
	names: Readonly<Record<string, string>>
): string | null {
	if (screen.hidden) return COPY.hidden;
	return screen.file === null ? null : (names[screen.file] ?? null);
}
