/*
 * How far a step through a clip goes.
 *
 * ## Why this is a module and not a number in five files
 *
 * The full-size player, the corner panel, a cell's bar, Theater's key handler and the timeline
 * itself all step. A constant repeated five times is not a constant; it is five that happen to
 * agree today.
 *
 * The one that makes it matter is the TIMELINE. A range input moves by its own `step` when it has
 * the keyboard, and the scrubber's step is a tenth of a second because that is the resolution
 * somebody dragging it wants. So clicking the bar and then pressing an arrow would move the
 * playhead a tenth of a second (while the same arrow, with the focus anywhere else, moves it five),
 * and the app's own shortcut would be suppressed the whole time, because a shortcut does not
 * fire into a focused input: arrows stuttering until you click the picture.
 *
 * The timeline handles its own arrows, and it moves by THIS, so the two answers cannot differ,
 * whatever has the keyboard.
 *
 * The glyphs are tied to it as well: `replay_5` and `forward_5` have the number drawn into them, so
 * a step that stopped being five would need those changed in the same breath.
 */
export const SKIP_SECONDS = 5;
