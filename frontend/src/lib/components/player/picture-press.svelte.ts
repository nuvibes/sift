/**
 * Pressing and magnifying the moving picture. A press plays or pauses, unless it was the end of a
 * pan; a double press fills the screen or empties it; and while the screen is filled the wheel and
 * a drag magnify and move the picture, by the same `Zoomable` a photograph has. Not in a window,
 * where the wheel scrolls the page and a drag takes the file out to another application.
 */

import { Zoomable } from '$lib/components/common';
import type { getStage } from './stage.svelte';

/** The stage a player is drawn in. */
type Stage = NonNullable<ReturnType<typeof getStage>>;

/** What the presses read and drive in the player. */
interface PressParts {
	video(): HTMLVideoElement | null;
	/** The file being watched: a new one starts again at fit-to-screen. */
	watching(): string;
	frame(): Stage | null;
	/** Play or pause. */
	toggle(): void;
}

export class PicturePress {
	readonly view = new Zoomable();
	/* Whether the pointer moved between down and up: the end of a pan is not a press. */
	private panned = false;
	/* Whether the last press was a finger: a tap is play and pause, and two are not a fill. */
	private byFinger = false;
	private parts: PressParts;

	/** Constructed while the player initialises, because it keeps effects of its own. */
	constructor(parts: PressParts) {
		this.parts = parts;
		$effect(() => {
			this.view.watch(parts.video());
		});
		/* A new file, or the end of fullscreen, starts again at fit-to-screen. */
		$effect(() => {
			void parts.watching();
			void parts.frame()?.isFullscreen;
			this.view.reset();
		});
	}

	wheel(event: WheelEvent): void {
		const frame = this.parts.frame();
		if (!frame?.isFullscreen) return;
		// Only a wheel that did something wakes the controls.
		if (this.view.wheel(event)) frame.wake();
	}

	grab(event: PointerEvent): void {
		this.panned = false;
		this.byFinger = event.pointerType === 'touch';
		if (!this.parts.frame()?.isFullscreen) return;
		this.view.grab(event);
	}

	drag(event: PointerEvent): void {
		if (this.view.dragging) this.panned = true;
		this.view.drag(event);
	}

	press(): void {
		if (this.panned) {
			this.panned = false;
			return;
		}
		this.parts.toggle();
	}

	/* The zoom goes back on the way: magnifying is offered only while the screen is filled. */
	pressTwice(): void {
		if (this.byFinger) return;
		this.view.reset();
		this.parts.frame()?.toggleFullscreen();
	}
}
