/* Dragging something onto something else to say they belong together.
 *
 * One handler, not three. Tags, People and collections all want the same gesture (take a clip,
 * drop it on a chip, they are now associated) and three implementations of it would drift into
 * three slightly different sets of rules about what counts as a drop.
 *
 * **The gesture moves nothing.** A drop writes an association and leaves every file exactly where
 * it was. That is the whole promise of organising logically, and it is why this carries ids rather
 * than paths: there is nothing here that could move a file even by mistake.
 *
 * The payload rides in `dataTransfer` under a private type. A browser hands `text/plain` to any
 * page and takes it from any page, so a drag from a text editor into the library would otherwise
 * arrive looking like a perfectly good asset id.
 */

import { dragBeganInside } from './drag-origin.svelte';

/** The one media type this app's own drags carry. Not text/plain: see above. */
export const ASSIGN_TYPE = 'application/x-sift-assign';

/** What a drag is carrying: one or more source ids, and what kind of thing they are. */
export interface AssignPayload {
	kind: string;
	ids: string[];
}

/** Put a payload on a drag that is starting. */
export function startAssign(event: DragEvent, payload: AssignPayload): void {
	if (!event.dataTransfer) return;
	event.dataTransfer.effectAllowed = 'link';
	// `link` rather than `move` or `copy`, and the cursor says so: nothing is moved and nothing is
	// duplicated. The browser draws the association cursor, which is what is actually happening.
	event.dataTransfer.setData(ASSIGN_TYPE, JSON.stringify(payload));
}

/** Read a payload back, or null when the drag is not one of ours. */
export function readAssign(event: DragEvent): AssignPayload | null {
	const raw = event.dataTransfer?.getData(ASSIGN_TYPE);
	if (!raw) return null;
	try {
		const parsed: unknown = JSON.parse(raw);
		if (!parsed || typeof parsed !== 'object') return null;
		const { kind, ids } = parsed as Partial<AssignPayload>;
		if (typeof kind !== 'string' || !Array.isArray(ids)) return null;
		if (!ids.every((id) => typeof id === 'string' && id.length > 0)) return null;
		return { kind, ids };
	} catch {
		// Something else's drag that happens to use the same type name, or a truncated payload.
		// Not an error worth showing anybody: the drop simply does not apply here.
		return null;
	}
}

/** Whether a drag in progress is one this target would accept. */
export function carriesAssign(event: DragEvent, kind: string): boolean {
	// During dragover the data itself is unreadable (the browser only exposes the type list until
	// the drop) so the decision to accept has to be made from the type alone.
	const types = event.dataTransfer?.types;
	return Boolean(types && Array.from(types).includes(ASSIGN_TYPE)) && kind.length > 0;
}

/**
 * Whether a drag in flight is a LINK arriving from outside this application.
 *
 * Two facts, and both are needed. A browser fills any drag begun on a link or a picture with
 * `text/uri-list` of its own accord, so the payload alone would make every card in the app look
 * like an incoming link. See `drag-origin`, which owns the question of where a drag began and
 * says in full why that is the only reliable half of the answer.
 *
 * `text/plain` counts too: a link dragged out of some applications arrives as nothing else.
 */
export function carriesALink(event: DragEvent): boolean {
	if (dragBeganInside()) return false;
	const types = event.dataTransfer?.types;
	if (!types) return false;
	if (types.includes(ASSIGN_TYPE)) return false;
	return types.includes('text/uri-list') || types.includes('text/plain');
}

/**
 * Whether the pointer is over something that is going to take this drag ITSELF.
 *
 * The shared ANSWER to one question ("has a smaller target already claimed this?") asked by
 * both of the window-wide offers: the import overlay and an entity page's own. Both have to stand
 * down over a card that is going to file the drop under one person, or a single gesture draws two
 * offers and, on a drop, starts two downloads.
 *
 * It reads the mark rather than relying on each zone stopping the event, for the reason the marks
 * exist at all: a stopped event never reaches the window, and the counters up there would then sit
 * armed with no drag in flight.
 *
 * WHAT the zone takes, not merely that it is one. No value means it takes whatever the window
 * would have taken, which is files; `link` means links and nothing else: a card takes a link and
 * deliberately refuses a file, because a file dropped on a person is imported the ordinary way.
 *
 * A drag can be both at once, and that is the ordinary case rather than the strange one: dragging
 * an image out of a browser hands over a link and a file together. `carriesALink` decides it, so
 * the answer here is the same answer the zone itself will give. See below.
 */
export function overADropZone(event: DragEvent): boolean {
	const target = event.target;
	if (!(target instanceof Element)) return false;
	const zone = target.closest('[data-drop-zone]');
	if (!zone) return false;
	const takes = zone.getAttribute('data-drop-zone');
	if (!takes) return true;
	/*
	 * The zone's own question, asked with the zone's own function: `dropTarget` asks
	 * `carriesALink`. A drag out of a browser carries `text/uri-list` and `Files` at once, so
	 * asking "is it carrying a file" instead would light the card and keep the window overlay
	 * armed, drawing two offers and starting two downloads on a drop. Whether a zone takes this
	 * drag has one answer, and it belongs to the zone.
	 */
	return takes === 'link' ? carriesALink(event) : true;
}

/** The address a dropped link carries, or null when it carries none worth having. */
export function readLink(event: DragEvent): string | null {
	const data = event.dataTransfer;
	if (!data) return null;
	const url = (data.getData('text/uri-list') || data.getData('text/plain')).trim();
	// The first line: `text/uri-list` is a LIST, and its later lines can be comments beginning `#`.
	const first = url.split(/\r?\n/)[0]?.trim() ?? '';
	return first.length > 0 && !first.startsWith('#') ? first : null;
}

/** Options for one drop target. */
export interface DropTarget {
	/** What the target accepts. A drop of another kind is ignored rather than refused loudly. */
	kind: string;
	/**
	 * Called with the dragged ids and this target's id. Nothing on disk moves.
	 *
	 * OPTIONAL, for a surface that takes a link from outside and nothing from inside. An entity's own
	 * PAGE is that: dropping a link on somebody's page means the same thing as dropping it on their
	 * card, while dragging files onto the page they are already listed on means nothing. Absent, the
	 * target refuses an assign drag outright rather than lighting up and then doing nothing.
	 */
	onassign?: (sourceIds: string[], targetId: string) => void;
	targetId: string;
	/**
	 * A LINK from outside, dropped on this same target: fetch it and file it here.
	 *
	 * On the same target rather than a second one beside it, because to the person dragging it is
	 * one gesture ("put this here") and what they are holding decides which half runs. Two
	 * targets on one element would be two sets of enter/leave counters over one box, which flicker
	 * against each other.
	 *
	 * Absent means the target takes nothing from outside, and the drop falls through to the window
	 * overlay, which imports it the ordinary way. That is the honest default: a target that armed
	 * for links without being able to file them would swallow the drop and do nothing.
	 */
	onlink?: (url: string, targetId: string) => void;
}

/** The reactive state of one drop target, and the handlers to put on it.
 *
 * `over` is a counter rather than a flag: dragging across a chip's own children fires leave-then-
 * enter, and a boolean flickers off in the gap. The same trick the window-level file drop uses.
 */
export function dropTarget(target: DropTarget) {
	let depth = $state(0);

	/* What this target will take: something of its own kind from inside, or, where it was given
	   somewhere to put one, a link from outside. Asked in one place so the three handlers cannot
	   come to disagree about what is being offered, which is how a target lights up and then
	   refuses the drop. */
	function takes(event: DragEvent): boolean {
		if (target.onassign && carriesAssign(event, target.kind)) return true;
		return Boolean(target.onlink) && carriesALink(event);
	}

	return {
		get over() {
			return depth > 0;
		},
		reset() {
			depth = 0;
		},
		/**
		 * What to put on the element as `data-drop-zone`, or nothing where this target takes
		 * nothing from outside the application.
		 *
		 * The window-wide import overlay stands down over a zone that is going to take the drop
		 * itself, and it has to know WHICH drop. A card takes a LINK and deliberately does not take
		 * a file (a file dropped on a person is imported the ordinary way) so marking the card
		 * as a drop zone outright would have swallowed that file and done nothing with it.
		 *
		 * Derived from the same field that decides whether a link is accepted at all, rather than
		 * declared again beside every target: two spellings of one fact is how a card comes to be
		 * marked as taking something it refuses.
		 */
		get zone(): 'link' | undefined {
			return target.onlink ? 'link' : undefined;
		},
		handlers: {
			ondragenter(event: DragEvent) {
				if (!takes(event)) return;
				event.preventDefault();
				depth += 1;
			},
			ondragover(event: DragEvent) {
				if (!takes(event)) return;
				// Without this the browser refuses the drop, silently, and the gesture just fails.
				event.preventDefault();
				if (event.dataTransfer) event.dataTransfer.dropEffect = 'link';
			},
			ondragleave() {
				depth = Math.max(0, depth - 1);
			},
			ondrop(event: DragEvent) {
				depth = 0;
				const payload = readAssign(event);
				if (payload) {
					if (!target.onassign || payload.kind !== target.kind) return;
					event.preventDefault();
					target.onassign(payload.ids, target.targetId);
					return;
				}
				/*
				 * A link from outside. `stopPropagation` as well as `preventDefault`: the window
				 * overlay listens above this and would otherwise also take the drop, starting two
				 * downloads, one filed here and one filed nowhere.
				 *
				 * `stopPropagation` stops a bubbling listener on the window from running, so the
				 * overlays listen in the capture phase, which runs before this and cannot be
				 * stopped from here; that is how their counters still see the drop (see
				 * `DropOverlay` and `EntityDropZone`).
				 */
				if (!target.onlink) return;
				const url = readLink(event);
				if (!url) return;
				event.preventDefault();
				event.stopPropagation();
				target.onlink(url, target.targetId);
			}
		}
	};
}
