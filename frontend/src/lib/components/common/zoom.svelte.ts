/**
 * Zooming and panning ONE picture, written once.
 *
 * The still in the player and the picture viewer on an entity page magnify alike: the point under
 * the pointer stays put, the pan never passes the edge, a press returns to fit-to-screen. Two
 * copies would drift unnoticed. The pan is in SCREEN pixels, read off the rendered box, never the
 * file's own dimensions.
 */

/** As far in as it goes. Past this a photograph is pixels rather than a picture. */
const MAX_ZOOM = 8;

/**
 * Where one press takes you: a quarter of the way in. A press TOGGLES between this and fitting the
 * screen (a coarse control: see a face, then the picture back); the wheel is the fine one.
 */
const STEP = MAX_ZOOM / 4;

/**
 * How far the pointer may travel during a press and still be a press: a pan uses the same button,
 * and letting go of one must not also step the zoom.
 */
const A_PRESS_NOT_A_DRAG = 4;

/**
 * How long a stepped zoom is animated for, in milliseconds: `--dur-slow`'s number, spelled twice
 * since neither language can read the other, and a mismatch costs little either way.
 */
const GLIDE_MS = 320;

/** Where the pointer was when a drag began, and where the picture was. */
interface Grab {
	x: number;
	y: number;
	panX: number;
	panY: number;
}

export class Zoomable {
	/** How far in, where 1 is fit-to-screen. */
	zoom = $state(1);
	/** How far the picture has been moved from the middle, in screen pixels. */
	panX = $state(0);
	panY = $state(0);

	/**
	 * Whether magnifying is available on this surface RIGHT NOW, which only the surface knows (the
	 * player's still only while the screen is filled; never a video, whose pointer offers play).
	 * It sets the cursor and nothing else; each surface gates the gestures itself.
	 */
	canMagnify = $state(false);

	/** The element being magnified. Handed over by whoever draws it. */
	#element: HTMLElement | null = null;
	/** Where the picture was before the last press. See `undoStep`. */
	#beforeStep: { zoom: number; panX: number; panY: number } | null = null;
	#grabbed = $state<Grab | null>(null);
	/** Whether the pointer moved far enough during the last press to have been a drag. */
	#panned = false;
	/** Cleared a beat after a stepped zoom, which is the only change that animates. */
	#glideTimer: ReturnType<typeof setTimeout> | null = null;

	/**
	 * Whether the picture is in the middle of a stepped zoom, and should be animated through it. Only
	 * a STEP animates: easing a wheel's or a drag's stream would read as lag.
	 */
	gliding = $state(false);

	/** Whether this is magnified at all. What a caller asks to know if a gesture means something. */
	get magnified(): boolean {
		return this.zoom !== 1;
	}

	/** Whether a drag is in progress, which is what a caller draws a closed hand for. */
	get dragging(): boolean {
		return this.#grabbed !== null;
	}

	/**
	 * What the pointer looks like over the picture, decided here so the surfaces cannot drift.
	 *
	 * Null at fit-to-screen where the surface does not magnify, so its own cursor stands. `zoom-in`
	 * where it does, since the gesture is otherwise undiscoverable, turning to `zoom-out` once a
	 * press would go back, so the cursor always says what the press does. `grabbing` only for a
	 * drag in progress.
	 */
	get cursor(): 'zoom-in' | 'zoom-out' | 'grab' | 'grabbing' | null {
		if (this.dragging) return 'grabbing';
		if (this.canMagnify) return this.magnified ? 'zoom-out' : 'zoom-in';
		return this.magnified ? 'grab' : null;
	}

	/**
	 * What the caller writes into `scale` and `translate`, or null at fit-to-screen, so an
	 * unmagnified picture carries no transform at all. The longhands apply in the order the
	 * arithmetic here assumes.
	 */
	get scale(): string | null {
		return this.magnified ? String(this.zoom) : null;
	}

	get offset(): string | null {
		return this.magnified ? `${this.panX}px ${this.panY}px` : null;
	}

	/** Point this at the element that is drawn. Called again with null when it goes away. */
	watch(element: HTMLElement | null): void {
		this.#element = element;
	}

	/**
	 * How the picture should move between magnifications, or null when it should not: a string for
	 * the two properties this owns. Reduced motion overrules it globally in `app.css`.
	 */
	get transition(): string | null {
		return this.gliding
			? 'scale var(--dur-slow) var(--ease), translate var(--dur-slow) var(--ease)'
			: null;
	}

	/** Whether the last press moved the picture. A press that panned is not a press. */
	get panned(): boolean {
		return this.#panned;
	}

	/**
	 * One press: in to a quarter of the way, about the point pressed, or back to fitting the
	 * screen if the picture is magnified at all, from ANY magnification, so the way out never
	 * depends on where the wheel left it. Both directions animate. Answers whether it did anything.
	 */
	step(event: { clientX: number; clientY: number }): boolean {
		if (!this.#element) return false;
		// Kept so a press that turns out to be half of a double-press can be taken back. See below.
		this.#beforeStep = { zoom: this.zoom, panX: this.panX, panY: this.panY };

		if (this.magnified) {
			// All the way out is centred.
			this.zoom = 1;
			this.panX = 0;
			this.panY = 0;
			this.#glide();
			return true;
		}

		// The point pressed stays under the pointer, as with the wheel.
		this.#about(event.clientX, event.clientY, STEP / this.zoom);
		this.zoom = STEP;
		this.#clamp();
		this.#glide();
		return true;
	}

	/**
	 * Put the picture back exactly as it was before the last press, without animating: for a press
	 * that proves to be the first half of a double-press (fill or leave the screen). The press acts
	 * at once rather than wait out the double-click window, and is taken back if a second arrives;
	 * easing back would draw the accident twice. Answers whether there was anything to take back.
	 */
	undoStep(): boolean {
		if (!this.#beforeStep) return false;
		const { zoom, panX, panY } = this.#beforeStep;
		this.#beforeStep = null;
		this.zoom = zoom;
		this.panX = panX;
		this.panY = panY;
		this.#stopGliding();
		return true;
	}

	/** Keep the point under (x, y) where it is while the picture grows by `growth`. */
	#about(x: number, y: number, growth: number): void {
		if (!this.#element) return;
		const box = this.#element.getBoundingClientRect();
		const fromMiddleX = x - (box.left + box.width / 2);
		const fromMiddleY = y - (box.top + box.height / 2);
		this.panX = this.panX + fromMiddleX - fromMiddleX * growth;
		this.panY = this.panY + fromMiddleY - fromMiddleY * growth;
	}

	/* Animate this change, and take the transition off once it has landed; cleared first, so a
	   quick second press keeps its own animation. */
	#glide(): void {
		if (this.#glideTimer) clearTimeout(this.#glideTimer);
		this.gliding = true;
		this.#glideTimer = setTimeout(() => {
			this.gliding = false;
			this.#glideTimer = null;
		}, GLIDE_MS);
	}

	/**
	 * Take the animation off NOW, because a continuous gesture has begun: a wheel or drag right
	 * after a press would otherwise chase itself through the rest of the glide.
	 */
	#stopGliding(): void {
		if (this.#glideTimer) clearTimeout(this.#glideTimer);
		this.#glideTimer = null;
		this.gliding = false;
	}

	/** Back to fitting the screen, centred, not being dragged. */
	reset(): void {
		this.zoom = 1;
		this.panX = 0;
		this.panY = 0;
		this.#grabbed = null;
		this.#panned = false;
		this.#beforeStep = null;
		/* Not animated: a new picture arriving must not show the last one shrinking. */
		this.#stopGliding();
	}

	/*
	 * How far the picture may be moved, in each direction: half the overhang each way. Clamped
	 * continuously, so zooming out walks an off-centre picture back to the middle.
	 */
	#limit(): { x: number; y: number } | null {
		if (!this.#element) return null;
		const box = this.#element.getBoundingClientRect();
		// Nothing measurable yet: leave the pan alone; the next move that can measure clamps it.
		if (box.width === 0 || box.height === 0) return null;
		// The rect is the SCALED size, so the unscaled box is what it was before the transform.
		const width = box.width / this.zoom;
		const height = box.height / this.zoom;
		return { x: (width * this.zoom - width) / 2, y: (height * this.zoom - height) / 2 };
	}

	#clamp(): void {
		const bound = this.#limit();
		if (!bound) return;
		this.panX = Math.min(bound.x, Math.max(-bound.x, this.panX));
		this.panY = Math.min(bound.y, Math.max(-bound.y, this.panY));
	}

	/**
	 * The wheel, with the point under the pointer staying under the pointer, so a face being zoomed
	 * does not slide away. Answers whether it did anything (a wheel at either end does nothing).
	 */
	wheel(event: WheelEvent): boolean {
		if (!this.#element) return false;
		event.preventDefault();
		const next = Math.min(MAX_ZOOM, Math.max(1, this.zoom * Math.exp(-event.deltaY / 500)));
		if (next === this.zoom) return false;
		// A press may still be gliding. See `#stopGliding`: a wheel eased is a wheel that lags.
		this.#stopGliding();
		// The press before is no longer the last move, so there is nothing to take back.
		this.#beforeStep = null;

		const box = this.#element.getBoundingClientRect();
		// Where the pointer is on the picture, measured from its middle, at the size it is now.
		const fromMiddleX = event.clientX - (box.left + box.width / 2);
		const fromMiddleY = event.clientY - (box.top + box.height / 2);
		const growth = next / this.zoom;

		this.panX = this.panX + fromMiddleX - fromMiddleX * growth;
		this.panY = this.panY + fromMiddleY - fromMiddleY * growth;
		this.zoom = next;

		// Never further than there is picture to see; at fit-to-screen the bound is zero.
		this.#clamp();
		return true;
	}

	/** Begin a drag. Ignored where there is nothing to move, or on a button that means something else. */
	grab(event: PointerEvent): void {
		// Only the primary button: the other two open menus.
		if (!this.magnified || event.button !== 0) return;
		event.preventDefault();
		// As for the wheel: no easing behind the pointer.
		this.#stopGliding();
		this.#grabbed = { x: event.clientX, y: event.clientY, panX: this.panX, panY: this.panY };
		this.#panned = false;
		/* Captured, so a pan continues past the box's edge; asked for, since tests lack it. */
		if (event.currentTarget instanceof HTMLElement) {
			event.currentTarget.setPointerCapture?.(event.pointerId);
		}
	}

	/** Carry a drag on. One screen pixel of pointer is one screen pixel of picture. */
	drag(event: PointerEvent): void {
		if (!this.#grabbed) return;
		const travelled =
			Math.abs(event.clientX - this.#grabbed.x) + Math.abs(event.clientY - this.#grabbed.y);
		if (travelled > A_PRESS_NOT_A_DRAG) {
			this.#panned = true;
			// Same reasoning as the wheel: the drag is now what last moved the picture.
			this.#beforeStep = null;
		}
		this.panX = this.#grabbed.panX + (event.clientX - this.#grabbed.x);
		this.panY = this.#grabbed.panY + (event.clientY - this.#grabbed.y);
		this.#clamp();
	}

	/** End a drag. Answers whether one was in progress. */
	release(event: PointerEvent): boolean {
		if (!this.#grabbed) return false;
		this.#grabbed = null;
		if (event.currentTarget instanceof HTMLElement) {
			event.currentTarget.releasePointerCapture?.(event.pointerId);
		}
		return true;
	}
}
