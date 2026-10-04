// How many bare `Field`s a settings pane's source draws. See `check_settings_fields.js`.

import { withoutComments } from './tree.js';

/** The bare field's opening tag, and not `FieldRow` or any other name that starts the same. */
const BARE_FIELD = /<Field(?=[\s>/])/g;

/**
 * The bare `<Field` tags in one pane's source, comments aside: a note that names the tag it
 * replaced is not a field on the screen.
 *
 * @param {string} source
 * @returns {number}
 */
export function bareFieldsIn(source) {
	return [...withoutComments(source).matchAll(BARE_FIELD)].length;
}
