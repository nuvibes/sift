/*
 * The words on the button that finishes a pick sheet.
 *
 * `PickDialog` asks the caller because only the caller knows the verb; the sentences live here so
 * each is written once rather than per verb and wall.
 *
 * Each answers the empty case too. The button is unpressable with nothing ticked but its words are
 * still on screen, and "Add these 0 tags" is a number nobody chose and a plural nothing is.
 *
 * The second number. The sheet draws what the files are already on and a full tick can be cleared,
 * so finishing can remove as well as add. A button saying "Add these 2 tags" while about to
 * remove one would lie about what it does. So it says both halves when there are both, as the
 * shortest true thing ("Add 2, remove 1") rather than a fluent sentence that must be read to the
 * end before pressing.
 *
 * One verb per act. Taking a file off a tag is a membership ending (both things stay): Remove.
 * Joining is Add, matching the menu that opens the sheet ("Add to > Tag"). The other places are
 * picked from their lists on Add to, which finish on the pick itself and carry no button.
 */
import { counted } from '$lib/entity/entity-counts';

/** "Add tags" / "Add this tag" / "Add these 4 tags" / "Remove tag" / "Add 2, remove 1". */
export function tagConfirmLabel(on: number, off = 0): string {
	if (on === 0 && off === 0) return 'Add tags';
	if (off === 0) return on === 1 ? 'Add this tag' : `Add these ${counted(on)} tags`;
	if (on === 0) return off === 1 ? 'Remove tag' : `Remove these ${counted(off)} tags`;
	return `Add ${on}, remove ${off}`;
}

/*
 * What a half tick means, written once for the flyout and the sheet, which both draw a bar for a
 * row only part of the selection is on. It says what pressing means for the thing being applied,
 * not merely what the mark depicts.
 */
export const PARTLY_APPLIED = 'Already applied to some of the selected files';
