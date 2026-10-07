/*
 * A chip that carries a picture.
 *
 * The point of the picture being the chip's rather than the caller's is that a chip with a face and
 * a chip without are the same object and measure the same. jsdom lays nothing out, so the height
 * cannot be read here: what can be is the SHAPE. A pill of a screen's own, with a face sized by
 * hand and a cross floated over its corner, is wrong in two ways that are testable. The picture is
 * inside the chip's own
 * body, at no size of its own; the cross is a sibling of the link and never inside it.
 *
 * The height itself belongs to the stylesheet and to the gallery specimen, which is where a person
 * can see the row standing level.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRawSnippet, flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

/* The account's own answer to "stop asking before a cross removes something" lives on the server,
   and the chip reads it through the shared interface state. Stood in for here rather than mocked at
   the request: what is under test is what the chip does with each answer, and a component test that
   reached the network would be testing the route. */
const remembered = vi.hoisted(() => ({ skipped: false, stopped: 0 }));

vi.mock('$lib/shell/interface-state.svelte', () => ({
	recallInterfaceState: async () => {},
	chipRemoveSkipped: () => remembered.skipped,
	skipChipRemoveConfirm: () => {
		remembered.stopped += 1;
	}
}));

import Chip from './Chip.svelte';

/** An invented name. Nobody real goes in a fixture. */
const WHO = 'Marla Quist';

const label = createRawSnippet(() => ({ render: () => `<span>${WHO}</span>` }));

let host: HTMLElement | undefined;
let mounted: Record<string, unknown> | undefined;

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = undefined;
	host?.remove();
	host = undefined;
});

function render(props: Record<string, unknown>) {
	host = document.createElement('div');
	document.body.append(host);
	mounted = mount(Chip, {
		target: host,
		props: { children: label, ...props }
	}) as Record<string, unknown>;
	return host.querySelector('.chip') as HTMLElement;
}

describe('in a place narrower than its words', () => {
	it('stays on one line, and only the label gives way', async () => {
		const { applyStyles, removeStyles } = await import('$lib/design/testing-styles');
		const { default: source } = await import('./Chip.svelte?raw');
		const lead = createRawSnippet(() => ({ render: () => '<span class="field">filetype:</span>' }));
		const chip = render({ lead });
		applyStyles(source, chip);
		try {
			const body = chip.querySelector<HTMLElement>('.body')!;
			expect(getComputedStyle(body).whiteSpace).toBe('nowrap');
			expect(getComputedStyle(body.querySelector('.field')!).flexShrink).toBe('0');
			const words = getComputedStyle(body.querySelector('.label')!);
			expect(words.flexShrink).toBe('1');
			expect(words.minInlineSize).toBe('0px');
			expect(words.textOverflow).toBe('ellipsis');
		} finally {
			removeStyles();
		}
	});
});

describe('a chip with a picture', () => {
	it('draws it inside the chip body, so the chip is what decides how tall it is', () => {
		const chip = render({ picture: { src: '/api/people/p-1/cover', name: WHO } });

		const shot = chip.querySelector('.shot');
		expect(shot).not.toBeNull();
		// Inside the body and not beside it: the body is what carries the chip's own height, so a
		// picture outside it would be free to be any size, and the chip taller than the chips next
		// to it.
		expect(shot?.closest('.body')).not.toBeNull();
		// And it names no size of its own. A number here would be the same fault written once more.
		expect(shot?.getAttribute('style')).toBeNull();
	});

	it('is the same chip whether or not it has one', () => {
		// The size class is what the height comes from, and a picture must not change it.
		const bare = render({});
		expect(bare.classList.contains('md')).toBe(true);
		unmount(mounted as Record<string, unknown>);
		mounted = undefined;
		host?.remove();

		const withFace = render({ picture: { src: '/api/people/p-1/cover', name: WHO } });
		expect(withFace.classList.contains('md')).toBe(true);
		expect(withFace.querySelector('.shot')).not.toBeNull();
	});

	it('keeps the cross OUTSIDE the link, where a browser will leave it alone', () => {
		// A button written inside an anchor is torn back out by the browser and the press lands on
		// the wrong one of the two. The component avoids it by structure rather than by floating a
		// button over the corner of the face.
		const chip = render({
			picture: { src: '/api/people/p-1/cover', name: WHO },
			href: '/people/p-1',
			onremove: () => {},
			removeLabel: `Remove ${WHO} from this file`
		});

		const cross = chip.querySelector(`[aria-label="Remove ${WHO} from this file"]`);
		expect(cross).not.toBeNull();
		expect(cross?.closest('a')).toBeNull();
		expect(chip.querySelector('a.body .shot')).not.toBeNull();
	});
});

describe('a cross that asks first', () => {
	/*
	 * The cross is a small target a few pixels from the half that opens the thing, and what it does
	 * (take a person off a file, a file out of a collection) is cheap to undo but hard to notice:
	 * the chip is simply gone from a row of look-alikes.
	 */
	function cross(chip: HTMLElement): HTMLButtonElement {
		const found = chip.querySelector<HTMLButtonElement>('.remove');
		if (!found) throw new Error('the chip has no cross');
		return found;
	}

	function press(button: HTMLElement) {
		button.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();
	}

	it('removes immediately when nothing was handed in, which is what a filter token does', () => {
		remembered.skipped = false;
		const removed = vi.fn();
		const chip = render({ onremove: removed, removeLabel: 'Remove this filter' });

		press(cross(chip));

		expect(removed).toHaveBeenCalledTimes(1);
		expect(document.querySelector('[role="alertdialog"]')).toBeNull();
	});

	it('asks before it removes, and has not removed anything while it is asking', () => {
		remembered.skipped = false;
		const removed = vi.fn();
		const chip = render({
			onremove: removed,
			removeLabel: `Remove ${WHO} from this file`,
			confirm: { what: WHO }
		});

		press(cross(chip));

		expect(removed, 'it removed and asked afterwards').not.toHaveBeenCalled();
		expect(document.body.textContent).toContain(`Remove ${WHO} from this file?`);
	});

	it('says what the cross itself promises, rather than a second sentence written here', () => {
		/* Two sentences for one act is two things free to disagree, and the one on the button is the
		   one a screen reader already reads out. */
		remembered.skipped = false;
		const chip = render({
			onremove: () => {},
			removeLabel: 'Remove from the collection Keepers',
			confirm: { what: 'Keepers' }
		});

		press(cross(chip));

		expect(document.body.textContent).toContain('Remove from the collection Keepers');
	});

	it('puts the thing itself on the question, as a link to its own page', () => {
		/*
		 * The chips behind the sheet are under the veil, so the thing is drawn on the sheet as the
		 * same chip that was pressed, and it opens: somebody unsure which Ada, or what a collection
		 * holds, can check before answering.
		 */
		remembered.skipped = false;
		const chip = render({
			onremove: () => {},
			removeLabel: `Remove ${WHO} from this file`,
			confirm: { what: WHO, where: '/people/p-1' }
		});

		press(cross(chip));

		const sheet = document.querySelector('[role="alertdialog"]');
		const opens = sheet?.querySelector<HTMLAnchorElement>('a[href="/people/p-1"]');
		expect(opens, 'the thing on the question does not open').not.toBeNull();
		expect(opens?.textContent).toContain(WHO);
		// ...and it is not a second way to remove: the sheet's copy of the chip has no cross.
		expect(sheet?.querySelector('.remove')).toBeNull();
	});

	it('leaves the question in words for a thing with no page to open', () => {
		/* A filing whose site was deleted, which is the ordinary absence rather than a gap: there is
		   nothing behind it to open and the only thing left to do with it is take it off. */
		remembered.skipped = false;
		const chip = render({
			onremove: () => {},
			removeLabel: 'Remove it from this file',
			confirm: { what: 'A site that is gone' }
		});

		press(cross(chip));

		const sheet = document.querySelector('[role="alertdialog"]');
		expect(sheet?.textContent).toContain('A site that is gone');
		expect(sheet?.querySelector('a')).toBeNull();
	});

	it('shuts the question when the link out of it is followed', async () => {
		/* Answering is what somebody came to do, so going to look at the thing ABANDONS the question
		   rather than leaving a sheet floating over whatever page opens. */
		remembered.skipped = false;
		const chip = render({
			onremove: () => {},
			removeLabel: `Remove ${WHO} from this file`,
			confirm: { what: WHO, where: '/people/p-1' }
		});
		press(cross(chip));

		const opens = document.querySelector<HTMLAnchorElement>(
			'[role="alertdialog"] a[href="/people/p-1"]'
		);
		opens?.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
		flushSync();

		/* Waited for rather than read immediately: the sheet has an exit transition, so it is still in
		   the document for a frame after it has been told to go. */
		await vi.waitFor(() => {
			flushSync();
			expect(document.querySelector('[role="alertdialog"]')).toBeNull();
		});
	});

	it('holds no room for the cross at rest, so the name gets the whole chip', () => {
		/*
		 * The cross holds no room at rest, so a name is not ellipsized early to keep a gap for an
		 * absent mark. The width comes back on hover and on focus, and `:focus-within` makes it
		 * reachable by keyboard, which is why this is a width of nought and never `display: none`.
		 */
		const chip = render({ onremove: () => {}, removeLabel: 'Remove it' });
		expect(cross(chip), 'the chip has no cross to hold room for').not.toBeNull();

		/* Read off the SOURCE, because the styles are not in the document here: this runner compiles
		   the component without the stylesheet vite would otherwise inject, so nothing rendered can
		   say what the cross measures. The same arrangement `LooksLikeThis.svelte.test.ts` uses to
		   hold two copies of one number equal. What is pinned is the shape rather than the spelling:
		   a width of nought, and never `display: none`, which would put a control that a keyboard
		   has to reach out of the layout entirely. */
		const source = readFileSync(resolve('src/lib/components/common/Chip.svelte'), 'utf8');
		const dressing = source
			.slice(source.indexOf('\t.remove {', source.indexOf('</script>')))
			// The comments say `display: none` in order to say why it is not used. Only the rules.
			.replace(/\/\*[\s\S]*?\*\//g, '');
		expect(dressing).toContain('inline-size: 0;');
		expect(dressing).not.toContain('display: none');
		// ...and it comes back on a pointer AND on a keyboard reaching anything inside the chip.
		expect(dressing).toContain('.chip:focus-within .remove');
	});

	it('removes immediately for an account that has said to stop asking', () => {
		remembered.skipped = true;
		const removed = vi.fn();
		const chip = render({
			onremove: removed,
			removeLabel: `Remove ${WHO} from this file`,
			confirm: { what: WHO }
		});

		press(cross(chip));

		expect(removed).toHaveBeenCalledTimes(1);
		remembered.skipped = false;
	});
});

/*
 * THE CHIP AS A DOOR, and the tone that says it is a place rather than a thing.
 *
 * The Add-a-tag control on an entity page is a chip at the end of the row of tags it adds to, so
 * it has to be the same object as the tags beside it and must not read as one of them. Two
 * properties carry that and both are testable here: the press is the MENU's, spread onto the chip's
 * own pressable body, and the line round it is dashed.
 */
describe('a chip that opens a menu', () => {
	it('spreads the menu wiring onto its own button rather than a span', () => {
		const opened = vi.fn();
		const chip = render({
			tone: 'outline',
			icon: 'add',
			trigger: { onclick: opened, 'aria-expanded': 'false', 'aria-haspopup': 'menu' }
		});

		const body = chip.querySelector('.body') as HTMLButtonElement;
		expect(body.tagName).toBe('BUTTON');
		expect(body.getAttribute('aria-haspopup')).toBe('menu');
		expect(body.getAttribute('aria-expanded')).toBe('false');

		body.click();
		flushSync();
		expect(opened).toHaveBeenCalledTimes(1);
	});

	it('reads as pressable, which is what a door is', () => {
		const chip = render({ tone: 'outline', trigger: { onclick: () => {} } });

		expect(chip.classList.contains('pressable')).toBe(true);
		expect(chip.classList.contains('outline')).toBe(true);
	});

	it('draws the outline tone with a DASHED line, which is what says it is a slot', () => {
		/* The distinction that earns a tone rather than a class: `quiet` is a solid outline and means
		   the chip IS a thing, dashed means it is the place a thing goes. Read off the stylesheet,
		   because jsdom does not apply a scoped rule to an element. */
		const css = readFileSync(resolve('src/lib/components/common/Chip.svelte'), 'utf8');
		const rule = css.slice(css.indexOf('.chip.outline {'));
		expect(rule.slice(0, rule.indexOf('}'))).toContain('border-style: dashed');
	});
});
