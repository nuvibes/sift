/*
 * When the label goes away, the half of a tooltip nobody can see is wrong.
 *
 * `focusout` fires for a departure and for a round trip, and the two are the same event. Pressing
 * the file name in the popout over plain http goes focusout on the control, focusin on a textarea,
 * focusout on the textarea, with `activeElement` left on `<body>`; that textarea is
 * `$lib/shell/clipboard`'s fallback, the only way to copy without a secure context, so treating the round
 * trip as a departure would dismiss the copy's own "Copied".
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, expect, it, vi } from 'vitest';

import TooltipProbe from './TooltipProbe.test.svelte';

let host: HTMLElement | undefined;
let mounted: Record<string, unknown> | undefined;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = undefined;
	host?.remove();
	host = undefined;
});

/** Mounted with the label showing, which takes a pointer MOVE rather than an enter. */
async function open(): Promise<{ wrap: HTMLElement; control: HTMLButtonElement }> {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(TooltipProbe, { target: host }) as Record<string, unknown>;
	flushSync();
	const wrap = host.querySelector<HTMLElement>('.wrap');
	const control = host.querySelector<HTMLButtonElement>('button');
	if (!wrap || !control) throw new Error('the tooltip drew nothing to point at');
	wrap.dispatchEvent(new PointerEvent('pointermove', { bubbles: true }));
	await vi.waitFor(() => {
		flushSync();
		if (!document.querySelector('[role="tooltip"]')) throw new Error('no label yet');
	});
	return { wrap, control };
}

/** Let the deferred "has focus really left?" check run. It is a task, not a microtask. */
function afterTheCheck(): Promise<void> {
	return new Promise((done) => setTimeout(done, 0));
}

it('keeps the label when focus goes and comes straight back', async () => {
	/* The round trip. Nothing has left: `activeElement` is inside the wrapper again by the time the
	   check runs, which is the only thing that can tell the two cases apart. */
	const { wrap, control } = await open();
	control.focus();

	wrap.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
	control.focus();
	await afterTheCheck();
	flushSync();

	expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
});

it('takes the label away when focus has genuinely left', async () => {
	// The other direction, so a check that simply never hides cannot pass this file.
	const { wrap, control } = await open();
	control.focus();
	const elsewhere = document.createElement('button');
	document.body.append(elsewhere);

	elsewhere.focus();
	wrap.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
	await afterTheCheck();
	flushSync();

	expect(document.querySelector('[role="tooltip"]')).toBeNull();
	elsewhere.remove();
});

it('is only allowed to be squeezed below its content when the caller asks', () => {
	/*
	 * THE FLOOR IS OPT-IN, and the reason is a fault that looks nothing like a tooltip.
	 *
	 * `min-inline-size: 0` on the wrapper is what lets a control that ellipsizes reach the width at
	 * which an ellipsis happens. Put on EVERY tooltip it would do something else entirely to the
	 * ones holding a control of a fixed size: as a grid item, a wrap around a 36px icon button
	 * would be laid out a few pixels wide and the buttons would paint on top of one another.
	 *
	 * The class is asserted rather than the computed style, because a component's scoped stylesheet
	 * is not loaded in this environment: what is under test is that the caller's answer reaches
	 * the element, which is the half that can silently stop being true.
	 */
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(TooltipProbe, { target: host }) as Record<string, unknown>;
	flushSync();

	expect(host.querySelector('.wrap')?.classList.contains('shrinks')).toBe(false);

	unmount(mounted);
	mounted = mount(TooltipProbe, { target: host, props: { shrinks: true } }) as Record<
		string,
		unknown
	>;
	flushSync();

	expect(host.querySelector('.wrap')?.classList.contains('shrinks')).toBe(true);
});

/*
 * Where the label goes when the window has a title strip of its own: the strip is drawn above this
 * layer, so the usable top of the window is the strip's foot, and a label with no room above its
 * control opens below it instead of sliding over it.
 */
function measured(el: Element, rect: { top: number; left: number; width: number; height: number }) {
	el.getBoundingClientRect = () =>
		({ ...rect, right: rect.left + rect.width, bottom: rect.top + rect.height }) as DOMRect;
}

async function openedAt(top: number): Promise<HTMLElement> {
	const { wrap } = await open();
	const bubble = document.querySelector<HTMLElement>('[role="tooltip"]');
	if (!bubble) throw new Error('no label');
	measured(wrap, { top, left: 300, width: 40, height: 28 });
	measured(bubble, { top: 0, left: 0, width: 80, height: 24 });
	window.dispatchEvent(new Event('resize'));
	flushSync();
	return bubble;
}

it('opens below a control that sits under the title strip, never on top of it', async () => {
	document.documentElement.style.setProperty('--window-chrome', '36px');
	try {
		const bubble = await openedAt(40);
		const y = parseFloat(bubble.style.top);
		expect(y, 'the label sat under the strip').toBeGreaterThanOrEqual(36);
		expect(
			y,
			'the label was clamped over its control rather than flipped below it'
		).toBeGreaterThan(40 + 28);
	} finally {
		document.documentElement.style.removeProperty('--window-chrome');
	}
});

it('keeps a label above a control that has room above it', async () => {
	const bubble = await openedAt(400);
	expect(parseFloat(bubble.style.top)).toBeLessThan(400);
});

/*
 * A key that opens the control's own list is a press. A chooser opens on the key and cancels the
 * click, so a label left up over the open list would take the first Escape for itself: Sort by
 * opened with Enter would need two Escapes to close.
 */
function key(control: HTMLElement, name: string): void {
	control.dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true }));
	flushSync();
}

it('takes the label away when a key opens the control, so the next Escape reaches the list', async () => {
	const { control } = await open();
	control.setAttribute('aria-haspopup', 'listbox');
	const heard: string[] = [];
	const listen = (event: KeyboardEvent) => heard.push(event.key);
	document.addEventListener('keydown', listen);
	try {
		key(control, 'Enter');
		expect(document.querySelector('[role="tooltip"]'), 'Enter left the label up').toBeNull();
		key(control, 'Escape');
		expect(heard, 'the label kept the Escape the list was owed').toContain('Escape');
	} finally {
		document.removeEventListener('keydown', listen);
	}
});

it('counts an arrow as a press only on a control that opens something', async () => {
	const { control } = await open();
	key(control, 'ArrowDown');
	expect(document.querySelector('[role="tooltip"]'), 'an arrow on a plain control').not.toBeNull();
	control.setAttribute('aria-haspopup', 'listbox');
	key(control, 'ArrowDown');
	expect(document.querySelector('[role="tooltip"]'), 'an arrow that opens a list').toBeNull();
});

it('shows the label while its owner holds it, with no pointer or focus, and hides it on letting go', async () => {
	host = document.createElement('div');
	document.body.append(host);
	const probe = mount(TooltipProbe, { target: host }) as { hold: (next: boolean) => void };
	mounted = probe as unknown as Record<string, unknown>;
	flushSync();
	expect(document.querySelector('[role="tooltip"]'), 'shown with nothing holding it').toBeNull();

	probe.hold(true);
	await vi.waitFor(() => {
		flushSync();
		if (!document.querySelector('[role="tooltip"]')) throw new Error('not shown while held');
	});

	probe.hold(false);
	flushSync();
	expect(document.querySelector('[role="tooltip"]'), 'left up after letting go').toBeNull();
});
