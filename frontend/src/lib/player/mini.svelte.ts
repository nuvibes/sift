/* The mini player: what is in it, where it sits, and how big it is.
 *
 * A panel inside the app rather than a window of its own. The browser's own way of floating a video
 * needs a secure connection, which a plain LAN install does not have, so it is simply absent on most
 * of the installs Sift runs on. This is ordinary layout: a small panel over the page, dragged and
 * resized like a chat window, and it works everywhere.
 *
 * ## Where the numbers live
 *
 * Its position and size are kept in this browser, not on the account. They are facts about the
 * screen somebody is sitting at (the corner that is out of the way on a laptop is over the grid on
 * a wide monitor), and an account setting arrives a moment after the page does, so the panel would
 * appear in one place and jump to another on every load.
 *
 * What is PLAYING is not kept anywhere. A reload is a new sitting, and a panel that reappeared
 * playing something from before would be a surprise rather than a convenience.
 */

import type { SpriteSheet } from '$lib/player/trickplay';
import type { SittingBaton, SittingPlace } from '$lib/player/sitting.svelte';
import { readStored, writeStored } from '$lib/shell/remembered.svelte';
import { thumbUrl } from '$lib/entity/art';
import type { components } from '$lib/api/schema';

const PLACE_KEY = 'sift.mini.place';

/**
 * How wide the panel is when nobody has ever moved it.
 *
 * "Small enough to keep out of the way" is half of it; the other half is big enough to be worth
 * watching, and on a wide monitor a 384-wide panel is a thumbnail whose first fate is a dragged
 * corner. About 520 wide in the corner of a 2560-wide window is the size that answers both.
 *
 * THE NUMBER LIVES IN THE STYLESHEET (`--mini-default-width`) and this is the floor under it, for
 * the reason `--window-chrome` is read the same way in `MiniPlayer`: a size the app's own scale
 * knows about belongs beside the rest of the scale, and a module that cannot see a document (a
 * test, a server render) still has to answer.
 */
const DEFAULT_WIDTH = 520;

/**
 * The window that has room for it, and the share of a narrower one.
 *
 * A fixed 520 on a 1280-wide laptop is more than a third of the screen, which is a panel that is in
 * the way rather than out of it, so under this the panel is a SHARE of the window instead, and it
 * shrinks with it. There is a step at the threshold and it is deliberate: the alternative is a
 * default that never quite reaches the size that was asked for on the screen it was asked for.
 */
const ROOM_FOR_DEFAULT = 1600;
const DEFAULT_SHARE = 0.26;

/**
 * The shape assumed before anything has said what is playing.
 *
 * Wide, because nearly everything is, and because the panel is re-shaped the moment the picture
 * reports its own size. See `shapedTo`. What this decides is the height of a panel that has never
 * been opened, which is a number that exists for one frame.
 */
const DEFAULT_SHAPE = 16 / 9;
const DEFAULT_HEIGHT = Math.round(DEFAULT_WIDTH / DEFAULT_SHAPE);

/** Under this it stops being a picture. Over the window it stops being a panel. */
const MIN_WIDTH = 240;
const MIN_HEIGHT = 135;

/**
 * The smallest panel there is, said as an AREA and a shortest side rather than as a width and a
 * height. Once the panel keeps the shape of what is in it, those two are the wrong pair of numbers.
 *
 * A width floor of 240 and a height floor of 135 describe one landscape box. Applied to a portrait
 * clip they describe a panel 240 wide and 427 tall: more than three times the area of the
 * smallest landscape one, for no reason except that such numbers are written for a wide picture,
 * so the panel could not be made as small in portrait as in landscape.
 *
 * As an area with a floor on the shorter side, the two come out the same size: 240x135 for a wide
 * clip, 135x240 for a tall one. The same panel, turned.
 */
const MIN_AREA = MIN_WIDTH * MIN_HEIGHT;
const MIN_SIDE = MIN_HEIGHT;

/** How far from the window's edge the panel rests when it has never been moved. `--space-4`, which
 *  is the distance the app puts between a thing and the edge it sits against. */
const MARGIN = 16;

/** What is playing, and where it had got to when it was sent here. */
interface MiniAsset {
	id: string;
	/**
	 * What this is: a clip, a photograph or a GIF.
	 *
	 * The panel draws a video for one and a picture for the others, and it cannot work that out from
	 * the id. Defaulted to a clip rather than made required, because a clip is what most callers
	 * hand it.
	 */
	mediaType?: string;
	/** What to put on the end of this file's picture addresses. */
	art?: string | null;
	/** How its scrub strip is cut up, when it has one. */
	sprite?: SpriteSheet | null;
	/** The still to show until the first frame arrives. */
	poster?: string;
	/** Where it had got to, in seconds. */
	at?: number;
	/** Whether it was paused when it was sent here. A clip handed over mid-pause that starts
	 *  playing is the panel deciding something nobody asked it to. */
	paused?: boolean;
	/**
	 * Whether this one is hidden, and so has nothing to draw.
	 *
	 * The server's own answer rather than anything worked out here: a concealed file comes back as
	 * the placeholder (`concealed` set, an empty media type, no art), and every byte address it
	 * has refuses. Carried so the panel can SAY that, instead of pointing a picture at a stream that
	 * answers 404 and reporting the file as unreachable, which is a different fact and reads as
	 * something being broken.
	 */
	concealed?: boolean;
	/** The panel's sitting with a picture handed over, carried on here so it stays one view. */
	sitting?: SittingBaton | null;
	/**
	 * Where the sitting it came out of was opened from, so a clip carried on in the corner still
	 * says what it was opened from (`inTheCorner`). Absent where nothing opened it over a screen.
	 */
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
		// Storage refused, or what was in it was not ours. The panel opens where it ships.
		return null;
	}
}

function write(place: Place): void {
	writeStored(PLACE_KEY, JSON.stringify(place));
}

/**
 * A place that is really on the screen.
 *
 * Applied on open, on every move, and whenever the window changes size, which is the case that
 * matters most: a panel remembered in the corner of a wide monitor is entirely off a laptop screen,
 * and a panel nobody can see is a video nobody can stop.
 */
export function fit(
	place: Place,
	/* `top` is where the room BEGINS, which is not always the top of the window: the desktop shell
	   draws a title strip above the application, and a panel placed at zero is a panel behind it:
	   in a drag region, so the gesture that would pull it back out moves the window instead. Zero
	   in a browser, where there is no strip. */
	within: { width: number; height: number; top?: number },
	/** The shape to keep while fitting, when the panel is holding something that has one. Without
	 *  it each side is clamped on its own, which is the answer for anything whose proportions are
	 *  not known yet: a wall before its cells have said what they are playing. */
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

/** Which edge or corner a resize is being dragged by. */
export type Grip = 'nw' | 'ne' | 'sw' | 'se' | 'n' | 's' | 'e' | 'w';

/**
 * Where the panel lands when an edge or a corner is dragged.
 *
 * Dragging the top or the left moves the panel as well as sizing it: the opposite edge is what
 * stays put, which is the whole of what makes that grip feel like that grip. Worked out here
 * rather than in the component so the arithmetic has a test of its own: an inverted sign is a panel
 * that runs away from the pointer, and it is the sort of thing that reads as correct in the source.
 */
export function resized(
	from: Place,
	grip: Grip,
	acrossBy: number,
	downBy: number,
	/**
	 * The shape of what is in the panel, as width over height, when it is known.
	 *
	 * Given one, the panel keeps that shape THROUGHOUT the drag rather than being squared up
	 * afterwards by a button: a button that asks for the right shape only exists because dragging
	 * gave the wrong one, with black bars down two sides for the whole drag.
	 *
	 * Absent for the moment before the browser has read a file's header, which falls through to a
	 * free resize.
	 */
	shape?: number
): Place {
	const west = grip.includes('w');
	const north = grip.includes('n');
	// An edge moves one dimension and leaves the other alone. Dragging the top of the panel must not
	// change how wide it is, and without these two it would: the pointer travels sideways as well as
	// down, and nothing else here says to ignore that.
	const sideways = west || grip.includes('e');
	const upDown = north || grip.includes('s');
	// Never past the minimum, and clamped BEFORE the edge is moved: without this, dragging inward
	// past the minimum keeps moving the panel while its size has stopped changing.
	// The floor for an UNSHAPED panel is still a width and a height; a shaped one gets the area
	// floor below instead, which is the same size in both orientations.
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
		 * One axis leads and the other follows it, and WHICH one leads is the whole of feeling
		 * connected to the pointer.
		 *
		 * An edge only moves one dimension, so that dimension is the one the hand is on. A corner
		 * moves both, and the hand is on whichever has travelled further AS A FRACTION of what the
		 * panel had, not in pixels, because a panel twice as wide as it is tall would then follow
		 * the width for almost any diagonal, and a drag mostly downward would widen it.
		 *
		 * Fitting the shape INSIDE the dragged box is wrong for an edge: drag the right edge out
		 * and the height is still the constraint, so nothing moves at all and the panel feels
		 * stuck.
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

/** Whether this drag moved the width more than the height, each measured against what it was. */
function wider(from: Place, width: number, height: number): boolean {
	return Math.abs(width - from.width) / from.width >= Math.abs(height - from.height) / from.height;
}

/**
 * The smallest panel of a given shape that is no smaller than the minimum on either side.
 *
 * Shared with `shapedTo` rather than written twice, because it is the same rule and applying one
 * clamp and not the other gets it wrong: a tall clip would come out 240x402 for a picture that is
 * 240x469: the right width, and not the right shape, from the one thing whose whole promise is
 * the shape.
 *
 * The two clamps cannot fight. The second only fires for a picture wider than the minimum panel's
 * own proportions, and for one of those the height floor gives a width over the minimum anyway.
 */
function atLeastTheMinimum(
	width: number,
	height: number,
	shape: number
): { width: number; height: number } {
	// Big enough to be worth watching, measured as area so a tall panel and a wide one of the same
	// shape-preserving size are the same size.
	const area = width * height;
	if (area < MIN_AREA) {
		const by = Math.sqrt(MIN_AREA / area);
		width *= by;
		height *= by;
	}
	// ...and never a sliver. A very wide or very tall picture reaches the area floor with one side
	// down to nothing, so the shorter side has a floor of its own and the shape follows it.
	const shorter = Math.min(width, height);
	if (shorter < MIN_SIDE) {
		const by = MIN_SIDE / shorter;
		width *= by;
		height *= by;
	}
	void shape;
	return { width: Math.round(width), height: Math.round(height) };
}

/**
 * The biggest panel of a given shape that fits inside the window, and no bigger.
 *
 * The other half of keeping the shape: `fit` clamps the width and the height INDEPENDENTLY, so a
 * shape-preserving drag that ran into the bottom of the screen would have its height cut and its
 * width left: the bars coming back at exactly the moment somebody drags the panel as large as it
 * will go.
 */
function noBiggerThanTheWindow(
	width: number,
	height: number,
	within: { width: number; height: number }
): { width: number; height: number } {
	const room = Math.min(within.width / width, within.height / height, 1);
	return { width: Math.round(width * room), height: Math.round(height * room) };
}

/**
 * The panel, made exactly the shape of what is in it.
 *
 * A panel that does not match its picture draws bars down two of its sides, and on something this
 * small the bars are a real fraction of it.
 *
 * The new size comes from the AREA the panel already has rather than from one of its edges, and
 * that is the whole of the decision here. Keeping the WIDTH is the obvious way to write it and it
 * turns a portrait clip into a panel most of the screen tall: a jump nobody asked for, from a
 * button whose whole promise is "no surprises, just the right shape". Keeping the area means the
 * panel stays about as big as it was and only its proportions change.
 *
 * Here rather than in the component for the reason `resized` is here: the arithmetic is the part
 * that can be silently wrong, and a wrong root or an inverted ratio reads as perfectly correct in
 * the source. Nothing about it needs a browser.
 *
 * A picture with no size is left alone. It is what the browser says before it has read the file's
 * header, and a shape derived from zero is a panel of no width at all.
 *
 * Given the window, the panel keeps the CORNER it sits nearest rather than its top left. A panel
 * resting bottom right that took a tall picture by its top left would grow down past the foot of
 * the window, be pushed back up flush with it and be left a wide panel's width in from the right
 * edge: over the wall, out of the corner, on every portrait GIF or clip put in it. The corner is
 * judged by the panel's middle against the window's, so a panel up in the top left still keeps
 * its top left, which is the place a press that only changes the shape must not move.
 */
export function shapedTo(
	from: Place,
	picture: { width: number; height: number },
	within?: { width: number; height: number }
): Place {
	if (picture.width <= 0 || picture.height <= 0) return { ...from };
	const shape = picture.width / picture.height;
	const across = Math.sqrt(from.width * from.height * shape);
	/* Under the smallest panel, the shape is kept and the panel GROWS. See `atLeastTheMinimum`,
	   which is shared with the resize, since a resize keeps the shape too. */
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

/**
 * How wide a panel nobody has moved is, on this window.
 *
 * The stylesheet's number where there is a document to read it from, and `DEFAULT_WIDTH` where
 * there is not. Read at the moment it is needed rather than once at import: a module read at import
 * would take whatever the stylesheet said before the theme had been applied.
 */
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

/**
 * Where a panel that has never been moved sits: out of the way, bottom right.
 *
 * The SHAPE is taken rather than assumed where the caller knows it, which is what makes the panel
 * land the right shape instead of arriving as a wide box and being squared up a frame later. The
 * corner is anchored AFTER the height is worked out, so the margin off the bottom is the same
 * distance whatever is playing: computing the height from a fixed shape and then reshaping would
 * bring the panel to rest a few pixels off the edge on anything that was not 16:9.
 */
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

/** What the panel holds of a file's record. */
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

/* A clip, as the panel reads a record: an absent type means one. See `MiniAsset.mediaType`. */
function isClip(asset: MiniAsset): boolean {
	return !asset.concealed && (asset.mediaType === undefined || asset.mediaType === 'video');
}

class MiniPlayer {
	/** What is playing in the panel, or null when there is no panel. */
	asset = $state<MiniAsset | null>(null);

	/** Where it is and how big, in pixels from the top left of the window. */
	place = $state<Place>({
		x: 0,
		y: 0,
		width: DEFAULT_WIDTH,
		height: DEFAULT_HEIGHT
	});

	/*
	 * Whether the full-size view still has to be left behind.
	 *
	 * Set when the panel was opened FROM the full-size view, and it is what tells the two cases
	 * apart. The same file open in both places at once is two soundtracks a fraction of a second
	 * apart, and which of them should go depends entirely on which arrived second: opening the panel
	 * closes the view, and opening the view closes the panel.
	 *
	 * The shell does the leaving, because leaving is navigation and the player has no business
	 * knowing what it is being drawn inside.
	 */
	handover = $state(false);

	/**
	 * Whether the panel is holding the WALL rather than one file.
	 *
	 * Theater pops out whole. A wall of several cells cut down to whichever one was in front is a
	 * different thing from the thing somebody was watching, so the panel takes the layout, and
	 * the two states are exclusive, because the panel is one box.
	 */
	wall = $state(false);

	/**
	 * The shape of what is in the panel, as width over height, once anything knows it.
	 *
	 * Held HERE rather than passed to each call, because every one of the four things that move the
	 * panel has to keep it (a drag, a let-go, the window changing size, and opening it), and a
	 * shape passed at three of the four is a panel that comes right until somebody resizes the
	 * window. Null for a Theater wall, which is several clips and has no one shape, and null until
	 * a file's header has been read.
	 */
	shape = $state<number | null>(null);

	/**
	 * Whether the panel's own chrome is up: the strip across the top, and the controls over the
	 * picture.
	 *
	 * On the store rather than inside the panel, because a Theater wall in here draws its own bar,
	 * from a different component that cannot reach into this one: held privately, the bar would
	 * sit over the picture permanently while the strip above it came and went, one surface fading
	 * in two halves.
	 *
	 * That is the shape this repository uses for a question two components have to agree on: name
	 * it once where both can read it, rather than hooking one on to the other. The panel writes it
	 * (it owns the pointer and the clock), and anything drawn inside reads it.
	 */
	chromeUp = $state(false);

	/**
	 * Whether a clip is held as the audio-only BAR rather than the panel: the third size of the
	 * player after full size and the corner. The sound carries on and the picture shrinks to a
	 * small thumbnail in the middle of a strip along the foot of the window. Only a clip: a
	 * picture has no sound to keep, and a wall is several.
	 */
	bar = $state(false);

	/** Whether anything is in it. What the layout draws on. */
	get showing(): boolean {
		return this.asset !== null || this.wall;
	}

	/** Put the whole wall in the panel. */
	openWall(within: { width: number; height: number }, shape: number | null = null): void {
		/*
		 * A wall HAS a shape, and is fitted to it exactly as a clip is.
		 *
		 * A wall is several clips in a layout, each a different shape: true of the clips and not
		 * of the wall: the rows share the height and every column is as wide as the widest thing in
		 * it, so the proportions come out of the layout and the cells' own shapes. See
		 * `wallAspect`, which is where that is worked out, beside the arithmetic the wall itself is
		 * drawn with.
		 *
		 * Null is still accepted and means each side fitted on its own, which is the right answer
		 * for the moment before anything has said what it is playing.
		 */
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

	/** Put a file in the panel, at the moment it had got to. */
	open(
		asset: MiniAsset,
		within: { width: number; height: number },
		options: { handover?: boolean; bar?: boolean } = {}
	): void {
		// Unknown until whatever is drawn in the panel has read the file's header and says so. Until
		// then the panel keeps whatever size it had.
		this.shape = null;
		/* Fitted keeping the shape it was left in. Fitted side by side instead, a panel left tall
		   by a portrait picture would be widened to the floor a wide panel has, pushed flush against
		   the right edge to make room, and the next picture's shape taken from that wider box: a
		   wide GIF after a tall one would come out bigger than the panel had been, with no margin. */
		const from = read() ?? restingPlace(within);
		this.place = fit(from, within, from.width / from.height);
		this.asset = asset;
		this.wall = false;
		this.handover = options.handover ?? false;
		this.bar = (options.bar ?? this.bar) && isClip(asset);
	}

	/** Down to the audio-only bar, from the panel. Only a clip has one. */
	toBar(): void {
		if (this.asset !== null && isClip(this.asset)) this.bar = true;
	}

	/** Back up from the bar to the panel. */
	toPanel(): void {
		this.bar = false;
	}

	/** Hidden shut on this file: its id is all that stays, at the size and in the bar it was in. */
	veil(id: string): void {
		if (this.asset?.id !== id || this.asset.concealed) return;
		this.asset = { id, concealed: true, from: this.asset.from };
	}

	/** Hidden opened on the file the panel was holding as hidden. */
	unveil(asset: MiniAsset): void {
		if (this.asset?.id !== asset.id || !this.asset.concealed) return;
		this.asset = { ...asset, from: this.asset.from };
	}

	/** The full-size view has been left. */
	left(): void {
		this.handover = false;
	}

	close(): void {
		this.asset = null;
		this.wall = false;
		this.handover = false;
		this.bar = false;
		this.shape = null;
		// Or the next thing put in the panel opens with its chrome already up, from a pointer that
		// left the last one.
		this.chromeUp = false;
	}

	/** What is in it, as a shape, or nothing. Said by whatever drew it, once the header is read. */
	takesTheShape(shape: number | null): void {
		this.shape = shape;
	}

	/** Being moved or resized. Kept inside the window, and not yet written down. */
	moveTo(place: Place, within: { width: number; height: number }): void {
		this.place = fit(place, within, this.shape ?? undefined);
	}

	/** Moved or resized, and let go of. Remembered for next time.
	 *
	 * Separate from `moveTo` because a drag is one gesture and several hundred pointer events, and
	 * storage is written synchronously: saving on each would put a write on the same thread as the
	 * thing being dragged. */
	settle(place: Place, within: { width: number; height: number }): void {
		this.moveTo(place, within);
		write(this.place);
	}

	/** The window changed size under it. Brought back inside without being written down: a panel
	 *  pushed in by a narrow window should go back where it was on a wide one. */
	reflow(within: { width: number; height: number }): void {
		this.place = fit(this.place, within, this.shape ?? undefined);
	}
}

/*
 * One fact, carried across a handover the other way.
 *
 * Going the other direction is a prop: the panel is handed what to play and can be told it was
 * paused. Coming BACK is a navigation (the full-size view is built by the shell from an address,
 * several components away from anything the panel can reach), and threading one boolean through
 * all of that would put a handover detail into three files that have no other interest in it.
 *
 * Read once and cleared, and matched on the id so a stale flag cannot pause something else.
 */
class Handover {
	private held: string | null = null;

	/** Say that this clip was paused when it was handed back. */
	wasPaused(id: string): void {
		this.held = id;
	}

	/** Whether this clip arrives held. Answering clears it: it describes one arrival. */
	take(id: string): boolean {
		if (this.held !== id) return false;
		this.held = null;
		return true;
	}

	/*
	 * The second fact: F pressed on the panel or the audio-only bar. The panel has no stage of its
	 * own to fill the screen with, so the clip goes back to full size and the full-size player
	 * fills the screen as it arrives, inside the moment the browser still counts as the press.
	 */
	private filling: string | null = null;

	/** Say that this clip should fill the screen when it arrives at full size. */
	fillsTheScreen(id: string): void {
		this.filling = id;
	}

	/** Whether this clip arrives filling the screen. Answering clears it, as `take` does. */
	takeFill(id: string): boolean {
		if (this.filling !== id) return false;
		this.filling = null;
		return true;
	}
}

export const handover = new Handover();

export const mini = new MiniPlayer();
export { MIN_HEIGHT, MIN_WIDTH };
