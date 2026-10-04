/*
 * A finger's reach at a phone's width, held at each primitive's one declaration.
 *
 * Every target on a phone is a finger's size (`--touch-target`, 44px). A box or a press in a row
 * is DRAWN that size: the row control height is raised in the phone block, and every field,
 * chooser and row press is built from it. A small press inside a card, a strip or a dense row
 * keeps its glyph and grows its REACH instead: an invisible ring of the element's own, drawn by
 * `::after`, reaching only as far as the element falls short. Every menu, chooser and panel that
 * floats beside its trigger on a desktop opens as a sheet from the foot of a phone's screen.
 *
 * Most of this is CSS, which jsdom does not lay out, so most of these read the rule out of the
 * file that owns it; the two that are behaviour (the pager's readout while loading, a drawer
 * beside the page) are mounted.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

import { readFileSync } from 'node:fs';
import pressable from './Pressable.svelte?raw';
import button from './Button.svelte?raw';
import chip from './Chip.svelte?raw';
import sharingMark from './SharingMark.svelte?raw';
import toaster from './Toaster.svelte?raw';
import heart from './Heart.svelte?raw';
import ratingChip from './RatingChip.svelte?raw';
import sectionHeading from './SectionHeading.svelte?raw';
import tabs from './Tabs.svelte?raw';
import pager from './Pager.svelte?raw';
import select from './Select.svelte?raw';
import popover from './Popover.svelte?raw';
import numberInput from './NumberInput.svelte?raw';
import contextMenuItem from './ContextMenuItem.svelte?raw';
import pickMenu from './PickMenu.svelte?raw';
import shell from '$lib/settings-ui/SettingsShell.svelte?raw';
import modal from '$lib/components/SettingsModal.svelte?raw';
import dataRow from './DataRow.svelte?raw';
import backButton from './BackButton.svelte?raw';
import narrowBox from './NarrowBox.svelte?raw';
import passwordInput from './PasswordInput.svelte?raw';
import slider from './Slider.svelte?raw';
import pickDialog from './PickDialog.svelte?raw';
import ratingChoices from './RatingChoices.svelte?raw';
import formCard from './FormCard.svelte?raw';
import tooltip from './Tooltip.svelte?raw';
import settingLink from './SettingLink.svelte?raw';
import pathSteps from '$lib/components/insights/PathSteps.svelte?raw';
import Pager from './Pager.svelte';
import DrawerProbe from './DrawerProbe.test.svelte';
import { phoneWidth } from './phone-width.svelte';

/* Read off the disk: a stylesheet imported `?raw` comes back empty under the test runner. */
const appCss = readFileSync('src/app.css', 'utf8');
const PHONE = '@media (max-width: 767px)';
const RING = 'min(0px, calc((100% - var(--touch-target)) / 2))';

/** What follows each phone block's opening, up to the next media query: the rules a phone adds. */
function phoneRules(source: string): string {
	const at = source.indexOf('<style>');
	const style = at === -1 ? source : source.slice(at);
	return style
		.split(PHONE)
		.slice(1)
		.map((block) => block.split('@media')[0])
		.join('\n');
}

let drawn: Record<string, unknown> | null = null;

afterEach(() => {
	if (drawn) unmount(drawn);
	drawn = null;
	phoneWidth.yes = false;
	document.body.innerHTML = '';
});

describe('a box and a row press at a phone width', () => {
	it('raise the row control height to a finger, and leave the small one to its reach', () => {
		const phone = phoneRules(appCss);
		expect(phone).toMatch(/--control-height: var\(--touch-target\);/);
		expect(phone).not.toMatch(/--control-height-sm: var\(--touch-target\)/);
	});

	it('draw every field at the control height, not a number of its own', () => {
		const at = appCss.indexOf(
			"input:not([type='range'], [type='checkbox'], [type='radio'], [type='file'], [type='hidden']),\ntextarea,\nselect {"
		);
		expect(at).toBeGreaterThan(-1);
		const rule = appCss.slice(at, appCss.indexOf('}', at));
		expect(rule).toMatch(/block-size: var\(--control-height\);/);
	});

	it('set every field at the phone size, whatever component dresses it', () => {
		const phone = phoneRules(appCss);
		expect(phone).toMatch(
			/input:where\(\s*:not\(\[type='range'\][^)]*\)\s*\),\s*textarea,\s*select \{\s*font-size: var\(--field-text-phone\) !important;/
		);
	});

	it('measure a number box by its words at the size the box draws them', () => {
		expect(phoneRules(numberInput)).toMatch(/\.sizer \{\s*font-size: var\(--field-text-phone\);/);
	});
});

describe("a row's press and a box, drawn a finger's size at a phone width", () => {
	it.each([
		['BackButton', backButton, '.back {', 'min-block-size: var(--touch-target);'],
		['NarrowBox', narrowBox, '.narrow {', 'block-size: var(--control-height);'],
		['PasswordInput', passwordInput, '.reveal {', 'inline-size: var(--touch-target);'],
		['Slider', slider, '.slider:not(.vertical) {', 'block-size: var(--touch-target);'],
		['PickDialog', pickDialog, '.rows button {', 'min-block-size: var(--touch-target);'],
		['RatingChoices', ratingChoices, '.choice {', 'min-block-size: var(--touch-target);'],
		['Select', select, ':global(.ui-select-item) {', 'min-block-size: var(--touch-target);'],
		['SettingsModal', modal, ':global(.close) {', 'inline-size: var(--touch-target);'],
		['DataRow', dataRow, '.subject {', 'flex-basis: 8rem;'],
		['FormCard', formCard, '.card {', '--row-pack: flex-start;'],
		['PathSteps', pathSteps, 'a {', 'padding-block: calc((var(--touch-target) - 1lh) / 2);']
	])('%s', (_name, source, selector, declaration) => {
		const phone = phoneRules(source);
		const at = phone.indexOf(selector);
		expect(at).toBeGreaterThan(-1);
		expect(phone.slice(at, phone.indexOf('}', at))).toContain(declaration);
	});

	it('the Settings panel and its shell agree on what a phone is', () => {
		expect(modal).toContain(PHONE);
		expect(modal).not.toContain('@media (max-width: 700px)');
	});

	it('a tooltip never shows for a finger', () => {
		expect(tooltip).toMatch(/if \(event\.pointerType !== 'touch'\) show\(180\);/);
		expect(tooltip).toMatch(/function onFocus\(event: FocusEvent\) \{\s*if \(fingerLast\) return;/);
	});
});

describe("a small press's reach at a phone width", () => {
	const OWNERS: Array<[string, string, string]> = [
		['Pressable', pressable, '.pressable::after'],
		['Button', button, '.btn.small::after'],
		['Chip', chip, 'button.body::after'],
		['SharingMark', sharingMark, 'button.mark::after'],
		['Toaster', toaster, '.close::after'],
		['Heart', heart, '.heart::after'],
		['RatingChip', ratingChip, '.mark::after'],
		['SectionHeading', sectionHeading, '.heading-link::after'],
		['Tabs', tabs, '.tab::after'],
		['Pager', pager, '.where::after'],
		['SettingLink', settingLink, '.setting-link::after']
	];

	it.each(OWNERS)('is a ring of its own on %s', (_name, source, selector) => {
		const phone = phoneRules(source);
		const at = phone.indexOf(selector);
		expect(at).toBeGreaterThan(-1);
		const rule = phone.slice(at, phone.indexOf('}', at));
		// A tab's ring is measured from its border box, its rule under the tab included, so its
		// block reach is its own pair of insets; the other rings share the one shape.
		if (selector === '.tab::after') {
			expect(rule).toContain('inset-block-start: var(--tab-short)');
			expect(rule).toContain('inset-block-end: calc(var(--tab-short) - var(--tab-rule))');
		} else {
			expect(rule).toContain(`inset-block: ${RING}`);
		}
		expect(rule).toContain(`inset-inline: ${RING}`);
	});

	it('keeps the pager presses a finger apart, centre to centre', () => {
		expect(phoneRules(pager)).toMatch(
			/\.steps \{\s*gap: calc\(var\(--touch-target\) - var\(--control-height-sm\)\);/
		);
	});
});

describe('a menu, a chooser and a panel at a phone width', () => {
	it.each([
		['Select', select],
		['Popover', popover],
		['NumberInput', numberInput],
		['ContextMenuItem', contextMenuItem],
		['PickMenu', pickMenu]
	])('%s opens as the sheet from the foot', (_name, source) => {
		const branch = source.indexOf('{#if phoneWidth.yes}');
		expect(branch).toBeGreaterThan(-1);
		const sheet = source.slice(branch, source.indexOf('{:else}', branch));
		/* The class itself, not `menu-sheet-head`, which the sheet's head wears. */
		expect(sheet).toMatch(/class="(?:[^"]*\s)?menu-sheet(?:\s[^"]*)?"/);
		expect(sheet).toContain('class="menu-sheet-head"');
	});
});

describe('a settings section on a phone', () => {
	it('scrolls itself, under a strip holding the way back', () => {
		const phone = phoneRules(shell);
		expect(phone).toMatch(/\.settings \{\s*block-size: 100%;/);
		expect(phone).toMatch(/\.pane-slot > :global\(\.scroll-root\) \{\s*flex: 1 1 0;/);
		const strip = shell.indexOf('<div class="back-slot">');
		const scroller = shell.indexOf('<Scroller onviewport');
		expect(strip).toBeGreaterThan(-1);
		expect(strip).toBeLessThan(scroller);
	});
});

describe('the pager while the first page loads', () => {
	it('says nothing rather than that there is nothing', () => {
		const host = document.createElement('div');
		document.body.append(host);
		const props = {
			offset: 0,
			shown: 0,
			total: 0,
			noun: 'files',
			onfirst: vi.fn(),
			onprevious: vi.fn(),
			onnext: vi.fn(),
			onlast: vi.fn(),
			onjump: vi.fn()
		};
		drawn = mount(Pager, { target: host, props: { ...props, loading: true } });
		flushSync();
		expect(host.querySelector('.where')?.textContent?.trim()).toBe('');
		unmount(drawn);
		drawn = mount(Pager, { target: host, props });
		flushSync();
		expect(host.querySelector('.where')?.textContent?.trim()).toBe('No files');
	});
});

describe('a drawer beside the page on a phone', () => {
	it('comes from the bottom whatever edge was asked for', () => {
		phoneWidth.yes = true;
		const host = document.createElement('div');
		document.body.append(host);
		drawn = mount(DrawerProbe, {
			target: host,
			props: { open: true, beside: true, side: 'right' }
		});
		flushSync();
		expect(document.querySelector('aside.drawer')?.getAttribute('data-side')).toBe('bottom');
	});

	it('keeps its edge on a wide window', () => {
		const host = document.createElement('div');
		document.body.append(host);
		drawn = mount(DrawerProbe, {
			target: host,
			props: { open: true, beside: true, side: 'right' }
		});
		flushSync();
		expect(document.querySelector('aside.drawer')?.getAttribute('data-side')).toBe('right');
	});
});
