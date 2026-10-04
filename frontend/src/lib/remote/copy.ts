// SPDX-License-Identifier: AGPL-3.0-or-later
/*
 * The Remote's words: one table, so the screen and its tests read the same sentences.
 *
 * ONE VOCABULARY. The controls wear the desk's own words and glyphs for the same verbs (the popout's
 * bar and drawer, the mini player, Theater's bar and a cell's drawer), so a press named on the
 * phone is the press of that name at the desk. The acts' words are `ACTS`, the one table every
 * player reads. A word here that the desk does not say is a second
 * name for one thing, and the one-word-per-thing gate reads this file.
 */
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
	presets: 'Layout Presets',
	noPresets: 'No saved Layout Presets yet',
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
	/* Why a control is dimmed. The first three are presses the phone cannot make for the desk: a
	   browser fills the screen only for a press made there, and a screenshot and the facts panel are
	   drawn on the computer's own screen. */
	fillAtTheDesk: 'Full screen needs a press on your computer',
	screenshotAtTheDesk: 'Screenshots are taken on your computer',
	statsAtTheDesk: 'Stats for nerds opens on your computer',
	notHere: 'Not available for what is showing'
} as const;

/** The A-B loop's one press, in the words the desk's button says for each of its three steps. */
export const LOOP_STEPS = ['Set the loop start', 'Set the loop end', 'Clear the loop'] as const;

/** The timer's choices on the phone, in seconds: none, then the lengths a wall is usually left on. */
export const TIMER_SECONDS = [0, 10, 30, 60, 300] as const;

/** A timer's length as the chooser says it. */
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

/**
 * What a screen is playing, in the words the phone may say, or null when it may name nothing.
 *
 * Its file's name where this phone's session may open it and the name has been read; Something
 * Hidden is playing where the phone's session keeps the file in Hidden (whatever the desk has
 * open). Null otherwise, and the page says only whether it plays: the list carries no file both for
 * a screen with nothing loaded and for a file this phone cannot reach, and telling those apart
 * here would be a guess.
 */
export function whatPlays(
	screen: Pick<ScreenOut, 'file' | 'hidden'>,
	names: Readonly<Record<string, string>>
): string | null {
	if (screen.hidden) return COPY.hidden;
	return screen.file === null ? null : (names[screen.file] ?? null);
}
