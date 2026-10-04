/** What the button-glyphs gate refuses, driven with markup written for the purpose.
 *
 * `scripts/check_button_glyphs.js` holds every button to the rule at the head of `Button.svelte`:
 * an act wears its glyph and its words, a navigation or an answer its words, and a glyph alone sits
 * inside a `Tooltip` naming it.
 */

import { describe, expect, it } from 'vitest';

import { buttonFaultsIn, buttonsIn, UNLABELLED, ACTS } from '../../../scripts/lib/button-glyphs.js';
import { ACTS as ROW_ACTS, actGlyph } from '$lib/design/button-glyphs';

const faults = (code: string) =>
	buttonFaultsIn(code).faults.map((one: { what: string }) => one.what);
const counted = (code: string) =>
	buttonFaultsIn(code).glyphedNavigations.map((one: { what: string }) => one.what);

describe('a glyph alone', () => {
	it('is refused with no tooltip around it', () => {
		expect(faults('<Button tone="ghost" icon="close" aria-label="Close" />')).toEqual([
			expect.stringContaining('icon-only Button ("Close") with no Tooltip')
		]);
	});

	it('is refused when the glyph is handed in as a child', () => {
		expect(
			faults(
				'<Button tone="ghost" aria-label="Delete it">\n\t<Icon name="delete" size={16} />\n</Button>'
			)
		).toEqual([expect.stringContaining('with no Tooltip')]);
	});

	it('passes inside a tooltip, however deep, and not after one has closed', () => {
		const inside =
			'<Tooltip label="Close">\n\t{#if open}<Button icon="close" aria-label="Close" />{/if}\n</Tooltip>';
		expect(faults(inside)).toEqual([]);
		expect(faults(`${inside}\n<Button icon="add" aria-label="Add" />`)).toEqual([
			expect.stringContaining('("Add") with no Tooltip')
		]);
	});

	it('reads an arrow function in an attribute without ending the tag early', () => {
		const code = '<Button icon="close" onclick={() => (open = x > 1)} aria-label="Close" />';
		expect(buttonsIn(code)[0]).toMatchObject({ icon: '"close"', text: '', named: '"Close"' });
	});

	it('is excused only by name, and the excuse is reported as met', () => {
		const [key] = [...UNLABELLED.keys()];
		const [file, name] = key.split('|');
		const met = new Set<string>();
		const code = `<Button icon="info" aria-label=${name} />`;
		expect(buttonFaultsIn(code, file, met).faults).toEqual([]);
		expect(met).toEqual(new Set([key]));
		expect(buttonFaultsIn(code, 'lib/Elsewhere.svelte').faults).toHaveLength(1);
	});
});

describe('an act', () => {
	it('is refused without its glyph', () => {
		expect(faults('<Button tone="primary" type="submit">Save</Button>')).toEqual([
			'"Save" is an act and wears no glyph (save)'
		]);
	});

	it("is refused wearing another act's glyph", () => {
		expect(faults('<Button icon="check" tone="primary">Save</Button>')).toEqual([
			'"Save" wears check, and Save wears save'
		]);
	});

	it('passes with its own glyph, and a worked-out glyph is not second-guessed', () => {
		expect(faults('<Button icon="save">Save</Button>')).toEqual([]);
		expect(faults("<Button icon={busy ? 'sync' : 'add'}>Add folder</Button>")).toEqual([]);
	});

	it('is judged only from words written out, never from an expression', () => {
		expect(faults('<Button>{COPY.save}</Button>')).toEqual([]);
	});

	it('in a word tone wears no glyph, and a mark with a figure keeps its glyph', () => {
		expect(faults('<Button tone="quiet">Clear all</Button>')).toEqual([]);
		expect(faults('<Button tone="quiet" icon="close">Clear all</Button>')).toEqual([
			'"Clear all" is a word that acts and wears a glyph'
		]);
		expect(faults('<Button tone="quiet" icon="water_drop">{counted.count}</Button>')).toEqual([]);
	});
});

describe('an answer', () => {
	it('is refused wearing a glyph', () => {
		expect(faults('<Button icon="close" onclick={cancel}>Cancel</Button>')).toEqual([
			'"Cancel" is an answer and wears a glyph'
		]);
	});

	it('may wear an arrow on the side it goes', () => {
		expect(faults('<Button icon="chevron_left">Previous</Button>')).toEqual([]);
		expect(faults('<Button trailing="chevron_right">Next</Button>')).toEqual([]);
	});
});

describe('a navigation', () => {
	it('wearing a glyph is counted, not refused', () => {
		const code =
			'<Button icon="folder" onclick={() => goto(\'/browse\')}>Browse the folders</Button>';
		expect(faults(code)).toEqual([]);
		expect(counted(code)).toEqual(['"Browse the folders" goes somewhere and wears a glyph']);
	});

	it('is not counted for words alone or for an arrow', () => {
		expect(counted("<Button onclick={() => goto('/browse')}>Open the folders</Button>")).toEqual(
			[]
		);
		expect(counted('<Button icon="arrow_upward">Open the new ones</Button>')).toEqual([]);
	});
});

describe('a verb handed to a row as a prop', () => {
	it('is read from the same table the gate holds markup to', () => {
		expect([...ACTS.entries()]).toEqual([...ROW_ACTS.entries()]);
	});

	it('wears its act glyph, whatever the caller offered, and a non-act keeps the offer', () => {
		expect(actGlyph('Delete results')).toBe('delete');
		expect(actGlyph('Edit')).toBe('edit');
		expect(actGlyph('Create', 'search')).toBe('add');
		expect(actGlyph('Add a person', 'person_add')).toBe('person_add');
		expect(actGlyph('Open')).toBeUndefined();
		expect(actGlyph('Count now', 'search')).toBe('search');
		expect(actGlyph('Downloading\u2026')).toBeUndefined();
	});
});
