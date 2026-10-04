/*
 * A split button is one control with two actions, and every property that makes that true is
 * invisible when it breaks. What it adds over two plain buttons is a rule about which half
 * something reaches:
 *
 * - the caller's own props and handlers go to the main half, so a main half that opens something on
 *   hover is not opened by a pointer on its way to the other one;
 * - the trailing half reports its own hover separately, for that reason;
 * - `trailingDisabled` takes the trailing half alone, `disabled` takes the pair;
 * - handed `menu`, the trailing half is a door and does not fire `ontrailing` at all.
 *
 * Each is a one-word edit from its opposite, and none changes how the control looks.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount } from 'svelte';
import { createRawSnippet } from 'svelte';

import SplitButton from './SplitButton.svelte';
// The component's own text, for the checks below that have to read the stylesheet: jsdom lays
// nothing out, so a rule about what happens when there is not enough room cannot be measured here.
// `?raw` is the bundler's way of asking for a file as a string.
import splitSource from './SplitButton.svelte?raw';

let host: HTMLElement;

afterEach(() => {
	host?.remove();
	document.body.innerHTML = '';
});

/** The words snippet, written the way a `.test.ts` has to write one. */
const words = (text: string) => createRawSnippet(() => ({ render: () => `<span>${text}</span>` }));

function draw(props: Record<string, unknown> = {}) {
	host = document.createElement('div');
	document.body.append(host);
	mount(SplitButton, {
		target: host,
		props: {
			trailingLabel: 'More ways to do this',
			children: words('Generate now'),
			...props
		}
	});
	flushSync();
}

/** The main half's button, found through this component's own hook rather than by position:
 *  the trailing half is wrapped by a tooltip or a menu door, so `:last-child` is not it. */
function lead(): HTMLButtonElement {
	const found = host.querySelector<HTMLButtonElement>('.half.lead button');
	if (!found) throw new Error('there is no main half');
	return found;
}

function trail(): HTMLButtonElement {
	const found = host.querySelector<HTMLButtonElement>('.half.trail button');
	if (!found) throw new Error('there is no trailing half');
	return found;
}

function press(button: HTMLElement) {
	button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	flushSync();
}

describe('one control, two actions', () => {
	it('is two real buttons, so each is separately focusable and separately named', () => {
		/* Splitting one button by where the pointer landed would be none of that, and would be
		   unreachable from a keyboard entirely. */
		draw();

		expect(host.querySelectorAll('button')).toHaveLength(2);
		expect(lead().textContent).toContain('Generate now');
		expect(trail().getAttribute('aria-label')).toBe('More ways to do this');
	});

	it('announces the pair as one thing', () => {
		draw();
		expect(host.querySelector('.split')?.getAttribute('role')).toBe('group');
	});
});

describe('which half the caller reaches', () => {
	it('sends the caller-s own press to the MAIN half and nowhere else', () => {
		const onclick = vi.fn();
		const ontrailing = vi.fn();
		draw({ onclick, ontrailing });

		press(lead());
		expect(onclick).toHaveBeenCalledTimes(1);
		expect(ontrailing).not.toHaveBeenCalled();

		press(trail());
		expect(ontrailing).toHaveBeenCalledTimes(1);
		expect(onclick, 'the caller-s press reached the trailing half too').toHaveBeenCalledTimes(1);
	});

	it('reports the trailing half-s hover separately from the main half-s', () => {
		/* The case this exists for: a main half that opens a preview on hover, and a pointer
		   travelling across it to reach the chevron. If `...rest` reached both, arriving at the
		   chevron would open the thing the caller only wanted opened by the main half. */
		const onmouseenter = vi.fn();
		const ontrailingenter = vi.fn();
		draw({ onmouseenter, ontrailingenter });

		trail().dispatchEvent(new MouseEvent('mouseenter'));
		flushSync();

		expect(ontrailingenter).toHaveBeenCalledTimes(1);
		expect(
			onmouseenter,
			'the main half-s hover fired from the trailing one'
		).not.toHaveBeenCalled();
	});
});

describe('what is unavailable', () => {
	it('takes the trailing half alone when only that action cannot be done', () => {
		draw({ trailingDisabled: true });

		expect(trail().disabled).toBe(true);
		expect(lead().disabled, 'the main half went with it').toBe(false);
	});

	it('takes the pair when the whole control is unavailable', () => {
		draw({ disabled: true });

		expect(lead().disabled).toBe(true);
		expect(trail().disabled, 'the trailing half stayed pressable').toBe(true);
	});
});

describe('the trailing half as a door', () => {
	it('opens a menu instead of firing its own action', () => {
		/*
		 * Handed `menu`, the half is the app's own `MenuButton` trigger, and `ontrailing` is not
		 * the press: firing both would make one press act and open.
		 */
		const ontrailing = vi.fn();
		draw({ menu: words('a row'), ontrailing });

		expect(trail().getAttribute('aria-haspopup')).toBe('menu');
		press(trail());

		expect(ontrailing).not.toHaveBeenCalled();
	});

	it('names the door, for anybody who cannot see the chevron', () => {
		draw({ menu: words('a row') });
		expect(trail().getAttribute('aria-label')).toBe('More ways to do this');
	});

	it('keeps the caller-s own props off the door half as well', () => {
		/* The same rule as the plain trailing half, and it has to be asserted separately: the door
		   draws its own Button from the library-s trigger props, so a spread added there is a
		   different edit in a different branch, which the plain half's test cannot see. */
		const onclick = vi.fn();
		const onmouseenter = vi.fn();
		draw({ menu: words('a row'), onclick, onmouseenter });

		press(trail());
		trail().dispatchEvent(new MouseEvent('mouseenter'));
		flushSync();

		expect(onclick, 'the caller-s press reached the door').not.toHaveBeenCalled();
		expect(onmouseenter, 'the caller-s hover reached the door').not.toHaveBeenCalled();
	});
});

describe('a door on each half', () => {
	it('opens a menu from the MAIN half instead of firing the caller-s press', () => {
		/* A file-s own screen wears this: the main half opens the places the file can be put, the
		   trailing half everything else. A half that both acted and opened would be one press doing
		   two things, which is the rule the trailing half already follows. */
		const onclick = vi.fn();
		draw({ leadMenu: words('a place'), leadMenuLabel: 'Add this file to', onclick });

		expect(lead().getAttribute('aria-haspopup')).toBe('menu');
		press(lead());

		expect(onclick).not.toHaveBeenCalled();
	});

	it('keeps the words on the main half when it becomes a door', () => {
		/* The whole point of the shape is that the loud half says what it is for. A door that lost
		   its label would be a second anonymous chevron beside the first. */
		draw({ leadMenu: words('a place'), leadMenuLabel: 'Add this file to' });

		expect(lead().textContent).toContain('Generate now');
	});

	it('is still two separately named buttons, one door each', () => {
		draw({
			leadMenu: words('a place'),
			leadMenuLabel: 'Add this file to',
			menu: words('a row')
		});

		expect(host.querySelectorAll('button')).toHaveLength(2);
		expect(lead().getAttribute('aria-haspopup')).toBe('menu');
		expect(trail().getAttribute('aria-haspopup')).toBe('menu');
		expect(trail().getAttribute('aria-label')).toBe('More ways to do this');
	});

	it('takes the pair when the whole control is unavailable, doors included', () => {
		/* The door draws its own Button from the library-s trigger props, so `disabled` reaching it
		   is a different edit in a different branch from the plain half-s, and a door nobody can
		   open that still looks pressable is worse than one that is plainly off. */
		draw({
			leadMenu: words('a place'),
			leadMenuLabel: 'Add this file to',
			menu: words('a row'),
			disabled: true
		});

		expect(lead().disabled).toBe(true);
		expect(trail().disabled).toBe(true);
	});
});

/*
 * It can never be wider than what it is in. Both halves are the shared Button, `white-space:
 * nowrap` and `inline-size: fit-content`, and a flex item's floor is its content, so without these
 * rules the pair would draw past the edge of any card narrower than its words.
 *
 * Asserted against the stylesheet rather than a box, because jsdom has no layout: a rendered
 * assertion would pass with every one of these rules deleted. The box itself is measured in a real
 * engine.
 */
describe('it cannot grow past what it is in', () => {
	/** The stylesheet alone, so a phrase in a comment cannot stand in for a rule. */
	const styles = splitSource.slice(splitSource.lastIndexOf('<style>'));

	it('caps the pair at its container', () => {
		expect(styles).toContain('max-inline-size: 100%');
	});

	it('lets the half with the words give way, and wrap rather than be cut', () => {
		// `min-inline-size: 0` is the whole of what lets a flex item go under its content width.
		const lead = styles.slice(styles.indexOf('\t.lead {'), styles.indexOf('\t.trail {'));
		expect(lead).toContain('flex: 0 1 auto');
		expect(lead).toContain('min-inline-size: 0');
		expect(lead).toContain('white-space: normal');
	});

	it('never squeezes the chevron, which is the only way to the rest of the acts', () => {
		const trail = styles.slice(styles.indexOf('\t.trail {'));
		expect(trail).toContain('flex: 0 0 auto');
		expect(trail).not.toContain('min-inline-size: 0');
	});
});
