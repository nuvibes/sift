/*
 * How tall a press is where it stands, decided by what holds it rather than by each call site.
 *
 * A press inside a settings row or a pane's form is the small control; one standing beside a field
 * in the same control column takes the field's height, so the two stand level; a form's submit is
 * the default size wherever it is. A row primitive says which of the first two applies to what it
 * holds, and `Button` reads it when its caller named no size. A surface of its own (a dialog, a
 * popover) starts again, so a row that opens one does not shrink the dialog's presses.
 */
import { getContext, setContext } from 'svelte';

import type { ButtonSize } from './Button.svelte';

const PRESS_SIZE = Symbol('press size');

/** Give every press drawn inside the calling component this size, unless a press names its own. */
export function givePressSize(size: ButtonSize | undefined): void {
	setContext(PRESS_SIZE, size);
}

/** The size the nearest holder gave, or undefined where nothing did. */
export function pressSize(): ButtonSize | undefined {
	return getContext<ButtonSize | undefined>(PRESS_SIZE);
}

/**
 * The size a press is drawn at: its own where the caller named one, the default for a form's
 * submit, the holder's where one gave a size, and the default otherwise.
 */
export function pressSizeFor(
	named: ButtonSize | undefined,
	type: string | null | undefined,
	given: ButtonSize | undefined
): ButtonSize {
	if (named !== undefined) return named;
	if (type === 'submit') return 'medium';
	return given ?? 'medium';
}
