/* Dragging something onto something else to say they belong together. A drop writes an
 * association and moves no file; the payload rides under a private type, never text/plain. */

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
	// `link`: nothing is moved or duplicated, and the cursor says so.
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
		// Another drag using the type name, or a truncated payload: it does not apply here.
		return null;
	}
}

/** Whether a drag in progress is one this target would accept. */
export function carriesAssign(event: DragEvent, kind: string): boolean {
	// During dragover only the type list is readable.
	const types = event.dataTransfer?.types;
	return Boolean(types && Array.from(types).includes(ASSIGN_TYPE)) && kind.length > 0;
}

/** Whether a drag in flight is a link from outside; see `drag-origin` for where it began. */
export function carriesALink(event: DragEvent): boolean {
	if (dragBeganInside()) return false;
	const types = event.dataTransfer?.types;
	if (!types) return false;
	if (types.includes(ASSIGN_TYPE)) return false;
	return types.includes('text/uri-list') || types.includes('text/plain');
}

/** Whether the pointer is over a zone that takes this drag itself, so the window-wide offers
 * stand down rather than starting a second download. */
export function overADropZone(event: DragEvent): boolean {
	const target = event.target;
	if (!(target instanceof Element)) return false;
	const zone = target.closest('[data-drop-zone]');
	if (!zone) return false;
	const takes = zone.getAttribute('data-drop-zone');
	if (!takes) return true;
	/* Asked with the zone's own function: a browser's drag carries a link and a file together. */
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

export interface DropTarget {
	/** What the target accepts. A drop of another kind is ignored rather than refused loudly. */
	kind: string;
	/** Called with the dragged ids and this target's id; absent, an assign drag is refused. */
	onassign?: (sourceIds: string[], targetId: string) => void;
	targetId: string;
	/** A link from outside dropped here, fetched and filed here; absent, the window takes it. */
	onlink?: (url: string, targetId: string) => void;
}

/** One drop target's state and handlers; `over` counts, since a child's leave-enter flickers. */
export function dropTarget(target: DropTarget) {
	let depth = $state(0);

	/* What this target takes, asked in one place so the handlers agree. */
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
		/** The `data-drop-zone` mark: a card takes a link, never a file. */
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
				/* A link from outside. stopPropagation too, or the window overlay starts a second download;
				   the overlays listen in the capture phase, so their counters still see it. */
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
