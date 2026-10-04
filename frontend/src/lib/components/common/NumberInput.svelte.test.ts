import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import NumberInput from './NumberInput.svelte';
import source from './NumberInput.svelte?raw';
import { applyStyles, removeStyles } from '$lib/design/testing-styles';

/* The box that replaced `type="number"`, and the reasons it is not one.
 *
 * A native number field draws a pair of stepper arrows in the operating system's own style, in a
 * place the page has no say over. So this is a text field that behaves like a number field, which
 * means every behaviour a number field gave for free is now code, and code with nothing asserting
 * it is code that quietly stops working.
 *
 * The one that matters most is the empty box. Clearing a field and clicking away is somebody
 * changing their mind, not a request to save zero, and "zero" is a real, accepted value for
 * several of the settings this draws, so saving it would look exactly like it worked.
 */

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function render(props: {
	value: number;
	min?: number;
	max?: number;
	step?: number;
	unit?: string;
	disabled?: boolean;
	autofocus?: boolean;
}) {
	host = document.createElement('div');
	document.body.append(host);

	const onchange = vi.fn();
	const all = reactiveProps({ ...props, onchange });
	mount(NumberInput, { target: host, props: all });
	flushSync();

	const input = host.querySelector('input') as HTMLInputElement;

	function type(text: string) {
		input.value = text;
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
	}

	function leave() {
		input.dispatchEvent(new Event('blur', { bubbles: true }));
		flushSync();
	}

	function press(key: string) {
		input.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
		flushSync();
	}

	return { input, onchange, props: all, type, leave, press };
}

describe('the box', () => {
	it('is not a native number field, so the site draws no arrows on it', () => {
		/* The whole reason it exists. `inputmode` is what still asks a phone for the number pad:
		 * without it this would be a text box on a touch keyboard, which is worse than the arrows. */
		const { input } = render({ value: 5 });

		expect(input.type).toBe('text');
		expect(input.inputMode).toBe('numeric');
	});

	it('tells assistive technology it is a number, and what its range is', () => {
		// A text field pretending to be a number field has to say so, or it is announced as free text
		// and the arrow keys are a secret.
		const { input } = render({ value: 5, min: 1, max: 10 });

		expect(input.getAttribute('role')).toBe('spinbutton');
		expect(input.getAttribute('aria-valuenow')).toBe('5');
		expect(input.getAttribute('aria-valuemin')).toBe('1');
		expect(input.getAttribute('aria-valuemax')).toBe('10');
	});
});

describe('committing', () => {
	it('saves what was typed when the box is left', () => {
		const { onchange, type, leave } = render({ value: 5 });

		type('12');
		expect(onchange, 'nothing is saved per keystroke').not.toHaveBeenCalled();

		leave();

		expect(onchange).toHaveBeenCalledExactlyOnceWith(12);
	});

	it('saves on Enter without waiting to be left', () => {
		const { onchange, type, press } = render({ value: 5 });

		type('12');
		press('Enter');

		expect(onchange).toHaveBeenCalledExactlyOnceWith(12);
	});

	it('says nothing when the value was not actually changed', () => {
		// Clicking into a field and out of it again is not an edit, and a write per visit would put a
		// row in the job feed for every settings pane somebody looked at.
		const { onchange, type, leave } = render({ value: 5 });

		type('5');
		leave();

		expect(onchange).not.toHaveBeenCalled();
	});

	it('puts the old value back when the box is cleared, and saves nothing', () => {
		/* The one that matters most. Zero is a value several of these settings accept, so a cleared
		 * box saved as zero would look like it had worked, and the number it wrote would be the one
		 * that switches a feature off. */
		const { input, onchange, type, leave } = render({ value: 60 });

		type('');
		leave();

		expect(onchange).not.toHaveBeenCalled();
		expect(input.value).toBe('60');
	});

	it('puts the old value back when the box holds something that is not a number', () => {
		const { input, onchange, type, leave } = render({ value: 60 });

		type('later');
		leave();

		expect(onchange).not.toHaveBeenCalled();
		expect(input.value).toBe('60');
	});

	it('keeps a value inside the range the setting declared', () => {
		const { onchange, type, leave } = render({ value: 5, min: 1, max: 10 });

		type('9999');
		leave();

		expect(onchange).toHaveBeenCalledExactlyOnceWith(10);
	});

	it('drops anything after the decimal point rather than refusing it', () => {
		// Every setting this draws is a whole number of something. Refusing would mean a refusal to
		// explain; rounding down is what the native field did.
		const { onchange, type, leave } = render({ value: 5 });

		type('7.9');
		leave();

		expect(onchange).toHaveBeenCalledExactlyOnceWith(7);
	});
});

describe('the arrow keys', () => {
	it('still step, because that is what the native arrows were for', () => {
		const { onchange, press } = render({ value: 5, step: 1 });

		press('ArrowUp');
		expect(onchange).toHaveBeenLastCalledWith(6);
	});

	it('step by the amount the setting declared', () => {
		const { onchange, press } = render({ value: 100, step: 50 });

		press('ArrowDown');
		expect(onchange).toHaveBeenLastCalledWith(50);
	});

	it('stop at the ends of the range rather than walking past them', () => {
		const { onchange, press } = render({ value: 1, min: 1, max: 10 });

		press('ArrowDown');

		expect(onchange).not.toHaveBeenCalled();
	});

	it('step from what is in the box, not from what was last saved', () => {
		// Somebody who typed a number and then reached for the arrow means to move the number they
		// typed. Stepping from the stored value would silently throw their typing away.
		const { onchange, type, press } = render({ value: 5, step: 1 });

		type('20');
		press('ArrowUp');

		expect(onchange).toHaveBeenLastCalledWith(21);
	});
});

describe('a value changed somewhere else', () => {
	it('shows up in the box', () => {
		/*
		 * Read through a derived rather than copied into state on the first render: state
		 * initialised from a prop never hears about a change, so a value saved on another screen,
		 * or rolled back after the server refused it, would leave this box showing a number that is
		 * not true.
		 */
		const { input, props } = render({ value: 5 });

		props.value = 42;
		flushSync();

		expect(input.value).toBe('42');
	});
});

describe('a box that was asked for', () => {
	it('selects its number on focus, so the first key starts a new one', () => {
		// The pager's jump box opens on the page it replaces. Typing 5 on page 2 goes to page 5,
		// not 25: the old number is selected, so the keystroke replaces it.
		const { input } = render({ value: 2, min: 1, max: 40, autofocus: true });
		input.setSelectionRange(1, 1);
		input.dispatchEvent(new FocusEvent('focus'));

		expect([input.selectionStart, input.selectionEnd]).toEqual([0, 1]);
	});

	it('leaves the caret where it was in a box nobody asked for', () => {
		const { input } = render({ value: 2, min: 1, max: 40 });
		input.setSelectionRange(1, 1);
		input.dispatchEvent(new FocusEvent('focus'));

		expect([input.selectionStart, input.selectionEnd]).toEqual([1, 1]);
	});
});

describe('the unit menu', () => {
	/*
	 * The door opens the app's one menu surface, and its rows are the shared menu row: bare library
	 * items with no class would have no inset, no corner and no ground under the pointer while
	 * every other list in the app has all three. `ContextMenuItem` is the same library component in
	 * a dropdown as in a right-click menu.
	 */
	const LADDER = [
		{ unit: 'KB/s', per: 1, places: 0 },
		{ unit: 'MB/s', per: 1000, places: 2 }
	];

	function open() {
		host = document.createElement('div');
		document.body.append(host);
		mount(NumberInput, {
			target: host,
			props: reactiveProps({ value: 5000, units: LADDER, onchange: vi.fn() })
		});
		flushSync();

		const door = host.querySelector('button.unit-door') as HTMLButtonElement;
		door.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0 }));
		flushSync();
		return door;
	}

	afterEach(() => {
		// The menu is portalled to the end of the document, so removing the host leaves it behind.
		document.body.innerHTML = '';
	});

	/* One chooser, one glyph: the unit door's chevron is the size every select draws. */
	it('opens from the same chevron every chooser wears', async () => {
		const { CHOOSER_CHEVRON } = await import('./Select.svelte');
		const door = open();
		const glyph = door.querySelector('.icon') as HTMLElement;
		expect(glyph.classList.contains(`size-${CHOOSER_CHEVRON}`)).toBe(true);
		const number = host.querySelector('.number') as HTMLElement;
		expect(number.style.getPropertyValue('--chevron')).toBe(`${CHOOSER_CHEVRON}px`);
	});

	/* The chevron wears the chooser's own ink, as every chooser beside it does. */
	it("draws the chevron in a chooser's ink, and only the unit's word quiet", () => {
		host = document.createElement('div');
		document.body.append(host);
		mount(NumberInput, {
			target: host,
			props: reactiveProps({ value: 5000, units: LADDER, onchange: vi.fn() })
		});
		flushSync();
		applyStyles(source, host.querySelector('.number'));
		const door = host.querySelector('button.unit-door') as HTMLButtonElement;
		const glyph = door.querySelector('.icon') as HTMLElement;
		const word = door.querySelector('.unit-word') as HTMLElement;

		expect(getComputedStyle(door).color).toBe('var(--foreground)');
		// The glyph sets no ink of its own, so it wears the door's.
		expect(glyph.style.color).toBe('');
		expect(LADDER.map((one) => one.unit)).toContain(word.textContent);
		expect(getComputedStyle(word).color).toBe('var(--sift-ink-3)');
		removeStyles();
	});

	it('draws its rows as the app-s menu row', () => {
		open();

		const rows = [...document.querySelectorAll('.ui-menu .item')].map((one) =>
			one.textContent?.trim()
		);
		expect(rows).toEqual(['KB/s', 'MB/s']);
	});

	it('scrolls in the shared region, like every other menu', () => {
		open();

		expect(document.querySelector('.ui-menu .scroll-root')).toBeTruthy();
	});
});

describe('the width of the box', () => {
	/*
	 * A box is as wide as what it can hold, never one width for every number on a pane. What it
	 * can hold is drawn unseen in its own cell, so the browser measures the words; these read what
	 * is drawn there and the rules that make the widest of them the box's width.
	 */
	const LADDER = [
		{ unit: 'MB', per: 1, places: 0 },
		{ unit: 'GB', per: 1000, places: 2 }
	];

	function sized(props: Record<string, unknown>) {
		host = document.createElement('div');
		document.body.append(host);
		mount(NumberInput, {
			target: host,
			props: reactiveProps({ value: 0, onchange: vi.fn(), ...props })
		});
		flushSync();
		return [...host.querySelectorAll<HTMLElement>('.sizer')].map((one) => one.dataset.words);
	}

	afterEach(removeStyles);

	it('has room for the digits of its maximum and one more, never a fixed width', () => {
		expect(sized({ max: 60 })).toEqual(['000']);
		expect(sized({ max: 1_000_000, unit: 'KB/s' })).toEqual(['00000000']);
		expect(host.querySelector<HTMLElement>('.number')!.style.inlineSize).toBe('');
	});

	it('reads the maximum on every unit it offers, so choosing one does not move its edge', () => {
		expect(sized({ max: 100_000, units: LADDER })).toEqual(['0000000']);
	});

	it('has room for the word it shows for zero', () => {
		expect(sized({ max: 100_000, automatic: 'No minimum' })).toEqual(['0000000', 'No minimum']);
	});

	it('takes the width a caller names instead', () => {
		expect(sized({ max: 3600, width: 5 })).toEqual(['00000']);
	});

	it('stacks the sizers in the input-s own cell, unseen', () => {
		sized({ max: 60, unit: 'days' });
		const box = host.querySelector<HTMLElement>('.number')!;
		applyStyles(source, box);
		expect(getComputedStyle(box).display).toBe('inline-grid');
		const sizer = getComputedStyle(host.querySelector('.sizer')!);
		const input = getComputedStyle(host.querySelector('input')!);
		expect(sizer.visibility).toBe('hidden');
		expect(sizer.gridArea.startsWith('1 / 1')).toBe(true);
		expect(input.gridArea.startsWith('1 / 1')).toBe(true);
		expect(host.querySelector('input')!.getAttribute('size')).toBe('1');
		/* The trailing cell is sized by its own unit, not by the longest unit any box has. */
		expect(box.style.getPropertyValue('--unit-chars')).toBe('4');
	});
});

describe('the unit beside the word for zero', () => {
	it('says nothing while the word shows, and keeps its cell', () => {
		const host = document.createElement('div');
		document.body.append(host);
		const instance = mount(NumberInput, {
			target: host,
			props: {
				value: 0,
				automatic: 'No minimum',
				unit: 'MB',
				label: 'Skip files smaller than',
				onchange: () => {}
			}
		});
		flushSync();
		const unit = host.querySelector('.unit');
		expect(unit?.classList.contains('silent')).toBe(true);
		unmount(instance);
		host.remove();
	});
});

describe('the word for zero in a box with a unit menu', () => {
	it('starts where a chooser answer starts, and the door drops its divider', () => {
		const host = document.createElement('div');
		document.body.append(host);
		const props = reactiveProps({
			value: 0,
			automatic: 'Never',
			units: [
				{ unit: 'days', per: 1, places: 0 },
				{ unit: 'weeks', per: 7, places: 1 }
			],
			label: 'Delete quarantined files after',
			onchange: () => {}
		});
		const instance = mount(NumberInput, { target: host, props });
		flushSync();
		const box = host.querySelector<HTMLElement>('.number')!;
		applyStyles(source, box);
		const input = host.querySelector('input')!;
		const door = host.querySelector<HTMLElement>('button.unit-door')!;

		expect(box.classList.contains('reads-automatic')).toBe(true);
		expect(getComputedStyle(input).textAlign).toBe('start');
		expect(door.closest('.reads-automatic')).toBe(box);
		// The door is the library's own element, so the rule reaching it is read from the source.
		expect(source.replace(/\s+/g, ' ')).toContain(
			'.number.reads-automatic :global(button.unit-door) { border-inline-start-color: transparent; }'
		);

		// A number is a number again: set at the end, with the door's divider back.
		props.value = 30;
		flushSync();
		expect(box.classList.contains('reads-automatic')).toBe(false);
		expect(getComputedStyle(input).textAlign).toBe('end');

		unmount(instance);
		host.remove();
		removeStyles();
	});
});
