/*
 * The mini player: a panel inside the app, since the browser's floating video needs a secure
 * connection. Its place and size are kept in this browser; what is playing is kept nowhere.
 */

import type { SpriteSheet } from '$lib/player/trickplay';
import type { SittingBaton, SittingPlace } from '$lib/player/sitting.svelte';
import { readStored, writeStored } from '$lib/shell/remembered.svelte';
import { thumbUrl } from '$lib/entity/art';
import type { components } from '$lib/api/schema';

const PLACE_KEY = 'sift.mini.place';

/** The floor under `--mini-default-width`, for a module that cannot read a document. */
const DEFAULT_WIDTH = 520;

/** Under this window width the panel is a share of it. */
const ROOM_FOR_DEFAULT = 1600;
const DEFAULT_SHARE = 0.26;

/** Before anything has said what is playing; reshaped when the picture reports its size. */
const DEFAULT_SHAPE = 16 / 9;
const DEFAULT_HEIGHT = Math.round(DEFAULT_WIDTH / DEFAULT_SHAPE);

const MIN_WIDTH = 240;
const MIN_HEIGHT = 135;

/**
 * The smallest panel as an area and a shortest side, so a portrait panel can be as small as a
 * landscape one.
 */
const MIN_AREA = MIN_WIDTH * MIN_HEIGHT;
const MIN_SIDE = MIN_HEIGHT;

/** `--space-4`. */
const MARGIN = 16;

interface MiniAsset {
	id: string;
	/** A clip, a photograph or a GIF; a clip by default. */
	mediaType?: string;
	art?: string | null;
	sprite?: SpriteSheet | null;
	poster?: string;
	at?: number;
	/** A clip handed over mid-pause must not start playing. */
	paused?: boolean;
	/** The server's placeholder for a concealed file, so the panel can say so instead of a 404. */
	concealed?: boolean;
	sitting?: SittingBaton | null;
	/** Where the sitting it came out of was opened from (`inTheCorner`). */
	from?: SittingPlace | null;
}

interface Place {
	x: number;
	y: number;
	width: number;
	height: number;
}

function read(): Place | null {
	try {
		const raw = readStored(PLACE_KEY);
		if (!raw) return null;
		const stored = JSON.parse(raw) as Partial<Place>;
		const place = {
			x: Number(stored.x),
			y: Number(stored.y),
			width: Number(stored.width),
			height: Number(stored.height)
		};
		return Object.values(place).every((value) => Number.isFinite(value)) ? place : null;
	} catch {
		// Storage refused, or not ours: the panel opens where it ships.
		return null;
	}
}

function write(place: Place): void {
	writeStored(PLACE_KEY, JSON.stringify(place));
}

/** A place really on the screen, applied whenever the window changes size. */
export function fit(
	place: Place,
	/* Where the room BEGINS: below the desktop shell's title strip. */
	within: { width: number; height: number; top?: number },
	/** Without a shape each side is clamped on its own. */
	shape?: number
): Place {
	let width: number;
	let height: number;
	if (shape !== undefined && shape > 0) {
		const smallest = atLeastTheMinimum(place.width, place.height, shape);
		({ width, height } = noBiggerThanTheWindow(smallest.width, smallest.height, within));
	} else {
		width = Math.min(Math.max(place.width, MIN_WIDTH), Math.max(within.width, MIN_WIDTH));
		height = Math.min(Math.max(place.height, MIN_HEIGHT), Math.max(within.height, MIN_HEIGHT));
	}
	return {
		width,
		height,
		x: Math.min(Math.max(place.x, 0), Math.max(within.width - width, 0)),
		y: Math.min(
			Math.max(place.y, within.top ?? 0),
			Math.max(within.height - height, within.top ?? 0)
		)
	};
}

export type Grip = 'nw' | 'ne' | 'sw' | 'se' | 'n' | 's' | 'e' | 'w';

/** Dragging the top or left moves the panel too; here so the signs have a test of their own. */
export function resized(
	from: Place,
	grip: Grip,
	acrossBy: number,
	downBy: number,
	/** Kept THROUGHOUT the drag; absent until the browser reads the header. */
	shape?: number
): Place {
	const west = grip.includes('w');
	const north = grip.includes('n');
	// An edge moves one dimension only, whatever the pointer does sideways.
	const sideways = west || grip.includes('e');
	const upDown = north || grip.includes('s');
	// Clamped BEFORE the edge moves, or the panel keeps moving after its size stops.
	const floorWidth = shape !== undefined && shape > 0 ? 1 : MIN_WIDTH;
	const floorHeight = shape !== undefined && shape > 0 ? 1 : MIN_HEIGHT;
	let width = sideways
		? Math.max(west ? from.width - acrossBy : from.width + acrossBy, floorWidth)
		: from.width;
	let height = upDown
		? Math.max(north ? from.height - downBy : from.height + downBy, floorHeight)
		: from.height;

	if (shape !== undefined && shape > 0) {
		/*
		 * The axis the hand is on leads: an edge's own, or for a corner whichever travelled further
		 * AS A FRACTION of what the panel had.
		 */
		const leadWidth = sideways && (!upDown || wider(from, width, height));
		if (leadWidth) height = width / shape;
		else width = height * shape;
		({ width, height } = atLeastTheMinimum(width, height, shape));
	}

	return {
		width,
		height,
		x: west ? from.x + from.width - width : from.x,
		y: north ? from.y + from.height - height : from.y
	};
}

function wider(from: Place, width: number, height: number): boolean {
	return Math.abs(width - from.width) / from.width >= Math.abs(height - from.height) / from.height;
}

/** The smallest panel of a shape that meets both floors; shared with `shapedTo`. */
function atLeastTheMinimum(
	width: number,
	height: number,
	shape: number
): { width: number; height: number } {
	const area = width * height;
	if (area < MIN_AREA) {
		const by = Math.sqrt(MIN_AREA / area);
		width *= by;
		height *= by;
	}
	// And never a sliver: the shorter side has a floor of its own.
	const shorter = Math.min(width, height);
	if (shorter < MIN_SIDE) {
		const by = MIN_SIDE / shorter;
		width *= by;
		height *= by;
	}
	void shape;
	return { width: Math.round(width), height: Math.round(height) };
}

/** The biggest panel of a shape inside the window: `fit` clamps the sides independently. */
function noBiggerThanTheWindow(
	width: number,
	height: number,
	within: { width: number; height: number }
): { width: number; height: number } {
	const room = Math.min(within.width / width, within.height / height, 1);
	return { width: Math.round(width * room), height: Math.round(height * room) };
}

/**
 * The panel made exactly the shape of what is in it, keeping its AREA (keeping the width would make
 * a portrait clip most of the screen tall) and the corner it sits nearest. A picture with no size
 * yet is left alone.
 */
export function shapedTo(
	from: Place,
	picture: { width: number; height: number },
	within?: { width: number; height: number }
): Place {
	if (picture.width <= 0 || picture.height <= 0) return { ...from };
	const shape = picture.width / picture.height;
	const across = Math.sqrt(from.width * from.height * shape);
	const { width, height } = atLeastTheMinimum(across, across / shape, shape);
	if (within === undefined) return { x: from.x, y: from.y, width, height };
	const keepsRight = from.x + from.width / 2 > within.width / 2;
	const keepsFoot = from.y + from.height / 2 > within.height / 2;
	return {
		x: keepsRight ? from.x + from.width - width : from.x,
		y: keepsFoot ? from.y + from.height - height : from.y,
		width,
		height
	};
}

/** Read when needed, not at import, which would be before the theme applied. */
function widthForDefault(within: { width: number }): number {
	let wanted = DEFAULT_WIDTH;
	if (typeof document !== 'undefined') {
		const said = Number.parseFloat(
			getComputedStyle(document.documentElement).getPropertyValue('--mini-default-width')
		);
		if (Number.isFinite(said) && said > 0) wanted = said;
	}
	return within.width >= ROOM_FOR_DEFAULT
		? Math.round(wanted)
		: Math.round(within.width * DEFAULT_SHARE);
}

/** Bottom right, with the shape known up front so the margin is the same whatever is playing. */
export function restingPlace(within: { width: number; height: number }, shape?: number): Place {
	const width = widthForDefault(within);
	const height = Math.round(width / (shape !== undefined && shape > 0 ? shape : DEFAULT_SHAPE));
	return fit(
		{
			x: within.width - width - MARGIN,
			y: within.height - height - MARGIN,
			width,
			height
		},
		within,
		shape
	);
}

export function heldOf(file: components['schemas']['AssetDetail']): MiniAsset {
	return {
		id: file.id,
		mediaType: file.media_type,
		art: file.art,
		sprite: file.sprite,
		poster: thumbUrl(file),
		concealed: file.concealed
	};
}

function isClip(asset: MiniAsset): boolean {
	return !asset.concealed && (asset.mediaType === undefined || asset.mediaType === 'video');
}

class MiniPlayer {
	asset = $state<MiniAsset | null>(null);

	place = $state<Place>({
		x: 0,
		y: 0,
		width: DEFAULT_WIDTH,
		height: DEFAULT_HEIGHT
	});

	/*
	 * Opened FROM the full-size view, so the shell leaves it: one file in both places is two
	 * soundtracks.
	 */
	handover = $state(false);

	/** Theater pops out whole, so the panel holds the WALL rather than one file. */
	wall = $state(false);

	/** Held here, so every one of the four things that move the panel keeps it; null for a wall. */
	shape = $state<number | null>(null);

	/** On the store, so a Theater wall's own bar fades with the panel's strip. */
	chromeUp = $state(false);

	/** The audio-only BAR: a clip's third size, after full size and the corner. */
	bar = $state(false);

	get showing(): boolean {
		return this.asset !== null || this.wall;
	}

	openWall(within: { width: number; height: number }, shape: number | null = null): void {
		/* A wall's shape comes from its layout (`wallAspect`); null fits each side on its own. */
		this.shape = shape;
		this.place = fit(
			read() ?? restingPlace(within, shape ?? undefined),
			within,
			shape ?? undefined
		);
		this.asset = null;
		this.wall = true;
		this.handover = false;
		this.bar = false;
	}

	open(
		asset: MiniAsset,
		within: { width: number; height: number },
		options: { handover?: boolean; bar?: boolean } = {}
	): void {
		// Unknown until the header is read; the panel keeps its size.
		this.shape = null;
		/*
		 * Fitted keeping the shape it was left in, or a tall panel would widen and lose its margin.
		 */
		const from = read() ?? restingPlace(within);
		this.place = fit(from, within, from.width / from.height);
		this.asset = asset;
		this.wall = false;
		this.handover = options.handover ?? false;
		this.bar = (options.bar ?? this.bar) && isClip(asset);
	}

	toBar(): void {
		if (this.asset !== null && isClip(this.asset)) this.bar = true;
	}

	toPanel(): void {
		this.bar = false;
	}

	veil(id: string): void {
		if (this.asset?.id !== id || this.asset.concealed) return;
		this.asset = { id, concealed: true, from: this.asset.from };
	}

	unveil(asset: MiniAsset): void {
		if (this.asset?.id !== asset.id || !this.asset.concealed) return;
		this.asset = { ...asset, from: this.asset.from };
	}

	left(): void {
		this.handover = false;
	}

	close(): void {
		this.asset = null;
		this.wall = false;
		this.handover = false;
		this.bar = false;
		this.shape = null;
		this.chromeUp = false;
	}

	takesTheShape(shape: number | null): void {
		this.shape = shape;
	}

	moveTo(place: Place, within: { width: number; height: number }): void {
		this.place = fit(place, within, this.shape ?? undefined);
	}

	/** Remembered on let-go only: storage is written synchronously. */
	settle(place: Place, within: { width: number; height: number }): void {
		this.moveTo(place, within);
		write(this.place);
	}

	/** Not written down: a panel pushed in by a narrow window goes back on a wide one. */
	reflow(within: { width: number; height: number }): void {
		this.place = fit(this.place, within, this.shape ?? undefined);
	}
}

/* Paused, carried back across a handover that is a navigation; read once, matched on the id. */
class Handover {
	private held: string | null = null;

	wasPaused(id: string): void {
		this.held = id;
	}

	take(id: string): boolean {
		if (this.held !== id) return false;
		this.held = null;
		return true;
	}

	/* F pressed on the panel or bar: the clip fills the screen as it arrives at full size. */
	private filling: string | null = null;

	fillsTheScreen(id: string): void {
		this.filling = id;
	}

	takeFill(id: string): boolean {
		if (this.filling !== id) return false;
		this.filling = null;
		return true;
	}
}

export const handover = new Handover();

export const mini = new MiniPlayer();
export { MIN_HEIGHT, MIN_WIDTH };
