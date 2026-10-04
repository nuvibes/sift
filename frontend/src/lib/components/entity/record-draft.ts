/* SPDX-License-Identifier: AGPL-3.0-or-later */
/*
 * The part of a record form's draft that goes on the entity's OWN ROW, as against the parts that
 * are their own tables.
 *
 * A person's record form edits fifteen fields, and they do not all go to one place: the birthdate
 * and the nationality are columns on the person, while the other names, the links and the tags are
 * rows of their own with their own routes. The name and the details are on the row too but are sent
 * as named arguments rather than inside the record, and the age is computed and cannot be stored.
 *
 * Two screens save a record (the person's page, and the blank form that makes somebody), and a
 * second copy of the list would be a copy free to fall behind: a field added to the registry and
 * forgotten in one is a field
 * that saves on one screen and vanishes on the other, silently, because both screens draw it.
 */

import { fields, type RecordSubject } from '$lib/entity/records.svelte';

/**
 * Which keys are saved somewhere other than the entity's record columns, by kind.
 *
 * Named rather than derived, because the registry does not say where a field is stored: it says
 * what the field IS. A key here is a promise that something else in the save writes it; the same
 * promise for both screens, written down where both read it.
 */
const APART: Partial<Record<RecordSubject, readonly string[]>> = {
	person: ['name', 'details', 'aliases', 'links', 'tags', 'accounts', 'sources', 'age']
};

/**
 * The record columns out of a draft, every one of them, with an absent field as null.
 *
 * Null and not "left out": the form sends the whole record, so a box somebody emptied has to reach
 * the server as a cleared column rather than as silence: silence means "leave it alone".
 */
export function recordFrom(
	subject: RecordSubject,
	draft: Record<string, unknown>
): Record<string, unknown> {
	const elsewhere = APART[subject] ?? [];
	const out: Record<string, unknown> = {};
	for (const one of fields.of(subject)) {
		if (elsewhere.includes(one.key)) continue;
		out[one.key] = draft[one.key] ?? null;
	}
	return out;
}
