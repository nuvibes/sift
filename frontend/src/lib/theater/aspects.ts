/*
 * What shape one cell is held to.
 *
 * Dynamic is the default (`fit.ts`): each cell takes its file's shape and nothing is cropped, but a
 * shuffled cell then changes shape at every advance and moves its neighbours. A cell pinned to a
 * shape holds still, and its picture is FITTED inside it, letterboxed and never cropped, since a
 * wall is watched rather than worked and nobody is there to notice what was cut. No "fill the
 * frame" switch, for the same reason. A list of six, so the choice is one press in a menu.
 */

/** The shapes a cell may be held to. `dynamic` means it takes the shape of what it is playing. */
export const ASPECTS = [
	{ id: 'dynamic', label: 'Dynamic', ratio: null },
	{ id: 'wide', label: 'Widescreen (16:9)', ratio: 16 / 9 },
	{ id: 'tall', label: 'Portrait (9:16)', ratio: 9 / 16 },
	{ id: 'square', label: 'Square (1:1)', ratio: 1 },
	{ id: 'classic', label: 'Classic (4:3)', ratio: 4 / 3 },
	{ id: 'cinema', label: 'Cinema (21:9)', ratio: 21 / 9 }
] as const;

export type AspectId = (typeof ASPECTS)[number]['id'];

/** The one every cell starts on: the dynamic wall. */
export const DEFAULT_ASPECT: AspectId = 'dynamic';

/**
 * Whether a stored value is one of ours. A later version's shape, or none at all, falls back to
 * Dynamic, which is how such a wall was drawn.
 */
export function isAspect(value: unknown): value is AspectId {
	return typeof value === 'string' && ASPECTS.some((one) => one.id === value);
}

/**
 * The ratio this shape means, or null for the shape of whatever is playing: the file holds that
 * answer, and the layout already reads `null` that way (`Aspect` in `fit.ts`).
 */
export function ratioOf(id: AspectId): number | null {
	return ASPECTS.find((one) => one.id === id)?.ratio ?? null;
}

/** What this shape is called, wherever it is shown. */
export function aspectLabel(id: AspectId): string {
	return ASPECTS.find((one) => one.id === id)?.label ?? 'Dynamic';
}
