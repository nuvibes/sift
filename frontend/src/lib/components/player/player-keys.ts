/**
 * The player's verbs, answering the keyboard and the phone from one table.
 *
 * A key is matched against the rows in the order they are written and a command from the phone
 * names its row, so a verb cannot work from one and not the other (`pressed` and `commanded` in
 * `$lib/shell/shortcuts`). A row answers true when it acted; false is "not mine". Where a key is a
 * toggle, the phone sends the state it wants, so a press at the desk a moment earlier stands.
 */

import { loudness } from '$lib/player/loudness.svelte';
import { dwell } from '$lib/player/dwell.svelte';
import { run } from '$lib/player/run.svelte';
import { requestPlay, type PlaybackPlan, type Quality } from '$lib/player/playback';
import { toggleShuffle } from '$lib/player/asset-view';
import { nextLoopMode, type LoopMode } from '$lib/player/loop-modes';
import { SKIP_SECONDS } from '$lib/player/skip';
import {
	matches,
	shortcut,
	type Actions,
	type Asked,
	type PlayerAction
} from '$lib/shell/shortcuts';
import { fillEcho, muteEcho, playEcho, skipEcho, volumeEcho, type Echo } from '$lib/theater/echoes';
import type { getStage } from './stage.svelte';

/** The stage a player is drawn in. */
type Stage = NonNullable<ReturnType<typeof getStage>>;

/** How much the up and down keys move the volume: a few presses cross the range. */
const VOLUME_STEP = 10;
/** And the two beside them, for settling on a level rather than finding one. */
const VOLUME_NUDGE = 2;

/** What the table reads and drives in the player. */
interface PlayerParts {
	video(): HTMLVideoElement | null;
	/** The stage the player is drawn in, which owns the screen and the idle fade. */
	frame(): Stage | null;
	/** The file being watched. */
	watching(): string;
	loop(): LoopMode;
	muted(): boolean;
	plan(): PlaybackPlan | null;
	/** Whether this file has more than one size to choose from. */
	choosable(): boolean;
	/** Whether the marked stretch can be saved as a Loop now. */
	savable(): boolean;
	/** Whether something can open a random file from here. */
	canOpen(): boolean;
	/** The file before and after, where the list has them. */
	previous(): (() => void) | undefined;
	next(): (() => void) | undefined;
	toggle(): void;
	seekBy(seconds: number): void;
	toggleMute(): void;
	resetZoom(): void;
	setLoop(mode: LoopMode): void;
	saveLoop(): void;
	clipTheLast(seconds: number): void;
	randomize(): void;
	pickQuality(quality: Quality): void;
	markPoint(): void;
	toMini(bar?: boolean): void;
	/** Whether the Audio player is offered: not on a phone, and not without a clip. */
	audioOffered(): boolean;
	/** Say in the corner what a key did. */
	echo(what: Echo): void;
	/** Count a press of one of the three keys whose control is in the shut drawer. */
	pressed(which: 'repeat' | 'shuffle' | 'loop'): void;
}

/** Space belongs to a focused button or link: it is how one is pressed. */
function pressable(target: EventTarget | null): boolean {
	const element = target as HTMLElement | null;
	return element?.tagName === 'BUTTON' || element?.tagName === 'A';
}

export class PlayerKeys {
	/*
	 * Whether M is held, which makes the four volume keys: M down arms, and the mute happens on the
	 * way UP, only if no arrow was used meanwhile. A key repeat does not re-arm it.
	 */
	private holdingM = false;
	private usedM = false;
	private parts: PlayerParts;
	readonly actions: Actions<PlayerAction>;

	constructor(parts: PlayerParts) {
		this.parts = parts;
		this.actions = this.table();
	}

	/** Change how loud it is from the keyboard, remembered the way the slider is. */
	private louder(by: number): void {
		loudness.set(loudness.level + by);
		this.parts.frame()?.wake();
	}

	/** A volume key, which is one only while M is held down. */
	private louderWhileM({ key }: Asked, by: number): boolean {
		if (key === null || !this.holdingM) return false;
		this.usedM = true;
		const before = loudness.level;
		this.louder(by);
		this.parts.echo(volumeEcho(before, loudness.level));
		return true;
	}

	/** The file either side, where the list has one. */
	private stepTo(go: (() => void) | undefined): boolean {
		if (!go) return false;
		go();
		return true;
	}

	private table(): Actions<PlayerAction> {
		const p = this.parts;
		return {
			'player.playPause': ({ key, value }) => {
				const video = p.video();
				if (key !== null) {
					if (pressable(key.target)) return false;
					p.toggle();
					if (video) p.echo(playEcho(video.paused));
					return true;
				}
				if (!video) return false;
				if (value === 1 && video.paused) requestPlay(video);
				if (value === 0 && !video.paused) video.pause();
				return true;
			},
			/* The volume keys, asked FIRST: two are the arrows that otherwise seek. Up and down move
			   ten, left and right two, as on the wall. The phone sets a level instead. */
			'player.louder': (asked) => this.louderWhileM(asked, VOLUME_STEP),
			'player.quieter': (asked) => this.louderWhileM(asked, -VOLUME_STEP),
			'player.louderABit': (asked) => this.louderWhileM(asked, VOLUME_NUDGE),
			'player.quieterABit': (asked) => this.louderWhileM(asked, -VOLUME_NUDGE),
			'player.volumeTo': ({ value }) => {
				if (value === null) return false;
				loudness.set(value);
				p.frame()?.wake();
				return true;
			},
			/* Ctrl and an arrow is the file either side, asked before the bare arrows' five seconds. */
			'player.previous': () => this.stepTo(p.previous()),
			'player.next': () => this.stepTo(p.next()),
			'player.back': ({ key, value }) => {
				p.seekBy(-(value ?? SKIP_SECONDS));
				if (key !== null) p.echo(skipEcho(-(value ?? SKIP_SECONDS)));
				return true;
			},
			'player.forward': ({ key, value }) => {
				p.seekBy(value ?? SKIP_SECONDS);
				if (key !== null) p.echo(skipEcho(value ?? SKIP_SECONDS));
				return true;
			},
			'player.seekTo': ({ value }) => {
				const video = p.video();
				if (value === null || !video) return false;
				p.seekBy(value - video.currentTime);
				return true;
			},
			'player.mute': ({ key, value }) => {
				if (key !== null) {
					// DOWN only arms it; the mute happens on the way UP.
					if (!key.repeat) {
						this.holdingM = true;
						this.usedM = false;
					}
					return true;
				}
				const video = p.video();
				if (!video) return false;
				if (video.muted !== (value === 1)) p.toggleMute();
				return true;
			},
			'player.fill': ({ key }) => {
				// The stage owns the screen, so a player drawn without one does nothing.
				const frame = p.frame();
				if (!frame) return false;
				p.resetZoom();
				// Asked before the press: the stage's answer follows the browser a moment later.
				const filling = !frame.isFullscreen;
				frame.toggleFullscreen();
				if (key !== null) p.echo(fillEcho(filling));
				return true;
			},
			'player.repeat': ({ key, value }) => {
				// The phone names the answer it wants, by its place in the order the key cycles.
				if (key === null && value !== null) return dwell.chooseAt(value);
				p.setLoop(nextLoopMode(p.loop()));
				p.pressed('repeat');
				return true;
			},
			// S on its own; Ctrl and S saves the file, and `matches` keeps the two apart.
			'player.shuffle': ({ key, value }) => {
				if (key === null && value !== null && run.shuffle === (value === 1)) return true;
				toggleShuffle(p.watching());
				if (key !== null) p.pressed('shuffle');
				return true;
			},
			/* The drawer's presses with no key, reached by the phone by name; each answers false where
			   the drawer draws it dimmed. */
			'player.saveLoop': () => {
				if (!p.savable()) return false;
				p.saveLoop();
				return true;
			},
			'player.clip': ({ value }) => {
				if (value === null || !p.video()) return false;
				p.clipTheLast(value);
				return true;
			},
			'player.random': () => {
				if (!p.canOpen()) return false;
				p.randomize();
				return true;
			},
			'player.quality': ({ value }) => {
				const rung = value === null || !p.choosable() ? undefined : p.plan()?.qualities?.[value];
				if (!rung) return false;
				p.pickQuality(rung);
				return true;
			},
			// The A-B control's press: start, then end, then clear.
			'player.loop': () => {
				p.markPoint();
				p.pressed('loop');
				return true;
			},
			'player.corner': () => {
				p.toMini();
				return true;
			},
			/* Down to audio only; the way back up is the strip's own answer to the same key. */
			'player.audioOnly': () => {
				if (!p.audioOffered()) return false;
				p.toMini(true);
				return true;
			}
		};
	}

	/*
	 * M let go, which is where the mute happens. Disarming is asked first and more loosely than the
	 * mute: "is M still down" has to come back false however it was let go, while a held Ctrl rules
	 * the mute out, and a stuck flag would turn the arrows into volume keys for the whole sitting.
	 */
	keyup(event: KeyboardEvent): void {
		if (!shortcut('player.mute').keys.includes(event.key)) return;
		this.holdingM = false;
		if (this.usedM || !matches(event, 'player.mute')) return;
		this.parts.toggleMute();
		this.parts.echo(muteEcho(this.parts.muted()));
	}
}
