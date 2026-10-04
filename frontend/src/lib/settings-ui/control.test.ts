import { describe, expect, it } from 'vitest';
import { choiceFor, controlFor, optionsFor, PERCENT } from './control';
import type { SettingEntry } from '$lib/settings-ui/settings';

/* The rule this file guards is the reason there is one settings control chooser.
 *
 * A pane that picks its own control can pick "switch" for everything registered into its section,
 * whatever the setting holds: four sizes in megabytes and six recognition settings on screen as
 * toggles, reading as off because a number is not `true`, and sending a boolean the server
 * refuses when flipped. Dead controls, with every test green unless something asserts that a
 * number gets a number field.
 *
 * This is that assertion, and the rest of the settings surface rests on it.
 */

function entry(over: Partial<SettingEntry> = {}): SettingEntry {
	return { key: 'test.setting', value: undefined, ...over };
}

describe('the control a setting gets', () => {
	it('gives a number a number field, and never a toggle', () => {
		expect(controlFor(entry({ default: 100, minimum: 1, maximum: 100000 }))).toBe('number');
		expect(controlFor(entry({ default: 0 }))).toBe('number');
	});

	it('gives a percentage a slider, because it is a proportion tuned by feel', () => {
		expect(controlFor(entry({ default: 50, minimum: 10, maximum: 100, unit: PERCENT }))).toBe(
			'slider'
		);
	});

	it('gives a boolean a toggle', () => {
		expect(controlFor(entry({ default: true }))).toBe('toggle');
		expect(controlFor(entry({ default: false }))).toBe('toggle');
	});

	it('gives a fixed set of options a menu, whatever type the options are', () => {
		expect(controlFor(entry({ default: 'cpu', choices: ['cpu', 'nvidia'] }))).toBe('menu');
		// A number with choices is still a menu: a fixed set is a fixed set.
		expect(controlFor(entry({ default: 1080, choices: [480, 720, 1080] }))).toBe('menu');
	});

	it('gives text a text field', () => {
		expect(controlFor(entry({ default: '' }))).toBe('text');
	});

	it('gives a clock time a clock, read off the default and not off a list of keys', () => {
		/* The quiet hours. As text boxes they would take `11pm` and `2300` with only a sentence
		   underneath saying otherwise. A list of keys here would be a copy of something the
		   server owns and would go stale the first time a setting was added; the SHAPE of the
		   default cannot. */
		expect(controlFor(entry({ default: '23:00' }))).toBe('time');
		expect(controlFor(entry({ default: '07:00' }))).toBe('time');
		expect(controlFor(entry({ default: '00:00' }))).toBe('time');
	});

	it('is not fooled by a string that merely has a colon in it', () => {
		// Both halves matter: the hour has to be an hour and the minute a minute, or a template, a
		// ratio and a network address would all arrive at a clock.
		expect(controlFor(entry({ default: '24:00' }))).toBe('text');
		expect(controlFor(entry({ default: '12:60' }))).toBe('text');
		expect(controlFor(entry({ default: '16:9' }))).toBe('text');
		expect(controlFor(entry({ default: '10.0.0.4:5171' }))).toBe('text');
	});

	it('says so rather than guessing when it does not know', () => {
		// The important half of the rule. Falling through to the nearest thing is exactly how a
		// number would come to be drawn as a switch.
		expect(controlFor(entry({ default: null }))).toBe('unknown');
		expect(controlFor(entry({ default: [1, 2] }))).toBe('unknown');
	});

	it('reads the declared default, not the stored value', () => {
		/* A stored value can be absent, and after a version change it can be a leftover of the wrong
		 * type. The default is declared beside the validator that accepts it, so it is the one field
		 * guaranteed to be an example of what the setting holds: deciding from `value` would make a
		 * number with nothing stored yet a toggle. */
		expect(controlFor(entry({ default: 60, value: undefined }))).toBe('number');
		expect(controlFor(entry({ default: 60, value: true }))).toBe('number');
	});
});

describe('the slider', () => {
	it('is what a percentage gets, and only a percentage', () => {
		/* A range input fires `input` once per pixel of travel. The rule that decides this is worth
		 * pinning on its own: every extra slider is a control that writes on every frame of a drag
		 * unless the row handles it, and the row only handles it for the shape it knows about. */
		expect(controlFor(entry({ default: 50, minimum: 0, maximum: 100, unit: PERCENT }))).toBe(
			'slider'
		);
		expect(controlFor(entry({ default: 50, minimum: 0, maximum: 100, unit: 'seconds' }))).toBe(
			'number'
		);
		expect(controlFor(entry({ default: 50, minimum: 0, maximum: 100 }))).toBe('number');
	});
});

describe('what a menu shows', () => {
	it('shows the names the setting declared, not the values it stores', () => {
		const device = entry({
			default: 'cpu',
			choices: ['cpu', 'nvidia'],
			choice_labels: ['CPU', 'GPU']
		});
		expect(optionsFor(device)).toEqual([
			{ value: 'cpu', label: 'CPU' },
			{ value: 'nvidia', label: 'GPU' }
		]);
	});

	it('falls back to the stored word when a name is missing, visibly', () => {
		// Only reachable from a server older than this client. Showing `fully_gone` says plainly
		// that something is out of step, where a prettified guess would hide it.
		const older = entry({ default: 'fully_gone', choices: ['fully_gone', 'placeholder'] });
		expect(optionsFor(older)[0].label).toBe('fully_gone');
	});
});

describe('the way back from an option to the choice', () => {
	/* A menu's option is a string; the server checks the declared choice, typed, and refuses a
	   number menu that sends "720". See `choiceFor`. */
	it('returns the declared choice in its declared type', () => {
		const height = entry({ default: 1080, choices: [480, 720, 1080] });
		expect(choiceFor(height, '720')).toBe(720);
		expect(choiceFor(entry({ default: 'cpu', choices: ['cpu', 'nvidia'] }), 'nvidia')).toBe(
			'nvidia'
		);
	});

	it('returns nothing for a string no choice answers to', () => {
		expect(choiceFor(entry({ default: 1080, choices: [480, 720, 1080] }), '721')).toBeUndefined();
	});
});
