/** Zooming and panning one picture, for the player's still and the picture viewer: the point
 * under the pointer stays put and the pan, in screen pixels, never passes the edge. */

/** As far in as it goes. Past this a photograph is pixels rather than a picture. */
const MAX_ZOOM = 8;

/** Where one press takes you; a press toggles between this and fit. */
const STEP = MAX_ZOOM / 4;

/** How far a press may travel and still not be a drag. */
const A_PRESS_NOT_A_DRAG = 4;

/** A stepped zoom's animation, `--dur-slow`'s number. */
const GLIDE_MS = 320;

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

	/** Whether magnifying is available here now; it sets the cursor only. */
	canMagnify = $state(false);

	#element: HTMLElement | null = null;
	/** Where the picture was before the last press. See `undoStep`. */
	#beforeStep: { zoom: number; panX: number; panY: number } | null = null;
	#grabbed = $state<Grab | null>(null);
	/** Whether the pointer moved far enough during the last press to have been a drag. */
	#panned = false;
	#glideTimer: ReturnType<typeof setTimeout> | null = null;

	/** Whether a stepped zoom is animating; only a step animates. */
	gliding = $state(false);

	/** Whether this is magnified at all. What a caller asks to know if a gesture means something. */
	get magnified(): boolean {
		return this.zoom !== 1;
	}

	/** Whether a drag is in progress, which is what a caller draws a closed hand for. */
	get dragging(): boolean {
		return this.#grabbed !== null;
	}

	/** The cursor, so it always says what a press does. */
	get cursor(): 'zoom-in' | 'zoom-out' | 'grab' | 'grabbing' | null {
		if (this.dragging) return 'grabbing';
		if (this.canMagnify) return this.magnified ? 'zoom-out' : 'zoom-in';
		return this.magnified ? 'grab' : null;
	}

	/** What the caller writes into `scale` and `translate`, or null at fit. */
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

	/** The transition for a step, or null; reduced motion overrules it in app.css. */
	get transition(): string | null {
		return this.gliding
			? 'scale var(--dur-slow) var(--ease), translate var(--dur-slow) var(--ease)'
			: null;
	}

	/** Whether the last press moved the picture. A press that panned is not a press. */
	get panned(): boolean {
		return this.#panned;
	}

	/** One press: in a step about the point, or back to fit from any zoom. */
	step(event: { clientX: number; clientY: number }): boolean {
		if (!this.#element) return false;
		// Kept so a press that turns out to be half of a double-press can be taken back. See below.
		this.#beforeStep = { zoom: this.zoom, panX: this.panX, panY: this.panY };

		if (this.magnified) {
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

	/** Take back the last press without animating, for the first half of a double-press. */
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

	/* Animate this change, cleared first so a quick second press keeps its own. */
	#glide(): void {
		if (this.#glideTimer) clearTimeout(this.#glideTimer);
		this.gliding = true;
		this.#glideTimer = setTimeout(() => {
			this.gliding = false;
			this.#glideTimer = null;
		}, GLIDE_MS);
	}

	/** Stop animating, since a wheel or drag has begun. */
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

	/* How far it may move: half the overhang each way, clamped continuously. */
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

	/** The wheel, keeping the point under the pointer; false at either end. */
	wheel(event: WheelEvent): boolean {
		if (!this.#element) return false;
		event.preventDefault();
		const next = Math.min(MAX_ZOOM, Math.max(1, this.zoom * Math.exp(-event.deltaY / 500)));
		if (next === this.zoom) return false;
		this.#stopGliding();
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
