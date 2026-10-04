/** What the settings-fields gate counts, driven with markup written for the purpose.
 *
 * `scripts/check_settings_fields.js` holds every settings pane to `FieldRow`, the field form of
 * the settings row, and refuses a bare `Field` (recorded at zero, may only fall).
 */

import { expect, it } from 'vitest';

import { bareFieldsIn } from '../../../scripts/lib/settings-fields.js';

it('counts a planted bare Field in a pane', () => {
	const pane = [
		'<FormCard onsubmit={save}>',
		'\t<Field label="Key">{#snippet control({ id })}<TextInput {id} />{/snippet}</Field>',
		'</FormCard>'
	].join('\n');
	expect(bareFieldsIn(pane)).toBe(1);
});

it('counts none for the field form of the row, and none named only in a comment', () => {
	/* A pane is a component: its markup is read as markup, where `<!-- -->` is a comment. */
	const pane = [
		'<script lang="ts">\n</script>',
		'<!-- Once a <Field label="Key">, now a row. -->',
		'<FieldRow label="Key">{#snippet control({ id })}<TextInput {id} />{/snippet}</FieldRow>'
	].join('\n');
	expect(bareFieldsIn(pane)).toBe(0);
});
