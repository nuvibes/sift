/*
 * A stash-box's constant, said as the word it stands for: `BLONDE` is "Blonde", `NON_BINARY` is
 * "Non binary".
 *
 * A box answers a choice (hair color, breast type, ethnicity) with its own upper-case constant,
 * and a record that drew it raw would read `BLONDE` beside a History line saying "Blonde" about the
 * same value. The server says it in words by one rule (`records.value_said`, for a `word` field);
 * this is the same rule for the record's own cells, and `tests/gates/test_word_values_agree.py`
 * holds the two patterns equal.
 *
 * Only a `word` field's value, and only one spelled wholly as a constant: a word somebody typed in
 * ordinary case is left exactly as typed.
 */

/** The shape of a box's constant: a capital letter, then capitals, digits and underscores. */
export const CONSTANT = /^[A-Z][A-Z0-9_]*$/;

/** A box's constant that is not a word at all, as what it stands for: `NA` is a breast type that
 *  does not apply, which the rule below would say "Na". Held equal to the server's `SPELLED`. */
export const SPELLED: Readonly<Record<string, string>> = { NA: 'Not applicable' };

/** A `word` field's value as the word it stands for, or the value as it is. */
export function constantSaid(value: unknown): string {
	const text = String(value);
	if (Object.hasOwn(SPELLED, text)) return SPELLED[text];
	if (!CONSTANT.test(text)) return text;
	const spaced = text.replace(/_/g, ' ');
	return spaced.charAt(0) + spaced.slice(1).toLowerCase();
}
