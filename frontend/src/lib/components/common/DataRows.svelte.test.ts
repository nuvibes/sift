import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, flushSync, mount, type Component, type Snippet } from 'svelte';
import { reactiveProps } from '$lib/design/testing.svelte';
import DataRows, { checkColumns, tracksOf, touchTracksOf, type Column } from './DataRows.svelte';
import ROWS from './DataRows.svelte?raw';

/* That the component, not a function it happens to call, holds the order.
 *
 * The ordering itself is proven next door, on its own. This is the other half, and it is the half
 * that would fail silently: the rule can be right and simply never asked. Nothing about a list that
 * forgot to listen for a pointer looks wrong until a queue re-sorts under someone's hand.
 */

interface Job {
	id: string;
}

const key = (job: Job) => job.id;

const row = createRawSnippet<[Job]>((job) => ({
	render: () => `<li data-id="${job().id}">${job().id}</li>`
}));

// The component is generic over its item, and mounting it from a test loses that: as a value rather
// than a tag, there is nowhere for the item type to come from and it lands on `unknown`. Named here
// with the type this test uses, so the props below are still checked rather than waved through.
const Rows = DataRows as unknown as Component<{
	items: readonly Job[];
	key: (item: Job) => string;
	row: Snippet<[Job]>;
	label: string;
}>;

let host: HTMLElement;

afterEach(() => {
	host?.remove();
});

function render(items: Job[]) {
	host = document.createElement('div');
	document.body.append(host);

	const props = reactiveProps({ items, key, row, label: 'Jobs' });
	mount(Rows, { target: host, props });

	// Mounting queues the effects that attach the event handlers. Without this the list is on screen
	// but is not listening yet, and an event dispatched here would land on nothing, which looks
	// exactly like a component that ignores it.
	flushSync();

	return {
		props,
		list: host.querySelector('ul') as HTMLElement,
		order: () => [...host.querySelectorAll('li')].map((li) => li.dataset.id)
	};
}

const jobs = (...ids: string[]) => ids.map((id) => ({ id }));

describe('with nothing pointing at it', () => {
	it('re-sorts, because that is what a live queue does', () => {
		const { props, order } = render(jobs('a', 'b', 'c'));
		expect(order()).toEqual(['a', 'b', 'c']);

		props.items = jobs('c', 'b', 'a');
		flushSync();

		expect(order()).toEqual(['c', 'b', 'a']);
	});
});

describe('while a pointer is over it', () => {
	it('holds the order, so the row being reached for stays put', () => {
		const { props, list, order } = render(jobs('a', 'b', 'c'));

		list.dispatchEvent(new PointerEvent('pointerenter', { bubbles: false }));
		props.items = jobs('c', 'b', 'a');
		flushSync();

		expect(order()).toEqual(['a', 'b', 'c']);
	});

	it('lets go when the pointer leaves, and the queue catches up', () => {
		// The freeze is a pause, not a stop. Whatever happened while someone was reading lands the
		// moment they are done, without them having to do anything to ask for it.
		const { props, list, order } = render(jobs('a', 'b', 'c'));

		list.dispatchEvent(new PointerEvent('pointerenter'));
		props.items = jobs('c', 'b', 'a');
		flushSync();
		expect(order()).toEqual(['a', 'b', 'c']);

		list.dispatchEvent(new PointerEvent('pointerleave'));
		flushSync();

		expect(order()).toEqual(['c', 'b', 'a']);
	});

	it('does not re-take the snapshot on a second pointer event', () => {
		// pointerenter can fire again while the pointer is still inside: crossing a child, mostly.
		// Re-snapshotting there would take the order as it is NOW, which is the reorder the freeze was
		// holding back, applied at exactly the wrong moment.
		const { props, list, order } = render(jobs('a', 'b', 'c'));

		list.dispatchEvent(new PointerEvent('pointerenter'));
		props.items = jobs('c', 'b', 'a');
		flushSync();

		list.dispatchEvent(new PointerEvent('pointerenter'));
		flushSync();

		expect(order()).toEqual(['a', 'b', 'c']);
	});
});

describe('while the keyboard is in it', () => {
	it('holds the order too: the same bug, a different input device', () => {
		const { props, list, order } = render(jobs('a', 'b', 'c'));

		list.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
		props.items = jobs('c', 'b', 'a');
		flushSync();

		expect(order()).toEqual(['a', 'b', 'c']);
	});

	it('keeps holding while focus moves between its own rows', () => {
		// Tabbing from one row to the next fires focusout. Letting go there would reorder the list
		// under the keyboard mid-step, which is the thing being prevented, happening during the act
		// of navigating.
		const { props, list, order } = render(jobs('a', 'b', 'c'));

		list.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
		props.items = jobs('c', 'b', 'a');
		flushSync();

		const insideRow = list.querySelector('li') as HTMLElement;
		// bubbles, because focusout does and the framework listens for it high up rather than here.
		list.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: insideRow }));
		flushSync();

		expect(order()).toEqual(['a', 'b', 'c']);
	});

	it('lets go when focus leaves the list entirely', () => {
		const { props, list, order } = render(jobs('a', 'b', 'c'));

		list.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
		props.items = jobs('c', 'b', 'a');
		flushSync();

		const elsewhere = document.createElement('button');
		document.body.append(elsewhere);
		list.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: elsewhere }));
		flushSync();

		expect(order()).toEqual(['c', 'b', 'a']);
		elsewhere.remove();
	});
});

describe('work that arrives while the list is held', () => {
	it('appears at the end rather than pushing anything down', () => {
		const { props, list, order } = render(jobs('a', 'b'));

		list.dispatchEvent(new PointerEvent('pointerenter'));
		props.items = jobs('z', 'a', 'b');
		flushSync();

		expect(order()).toEqual(['a', 'b', 'z']);
	});

	it('lands where it belongs when a press inside the list is what brought it', () => {
		// A press is the person's own act: a row opened out by its arrow puts its rows under it,
		// never after the last row, which is where a held order puts what it has not seen.
		const { props, list, order } = render(jobs('a', 'b'));

		list.dispatchEvent(new PointerEvent('pointerenter'));
		(list.querySelector('li[data-id="a"]') as HTMLElement).click();
		props.items = jobs('a', 'a1', 'b');
		flushSync();

		expect(order()).toEqual(['a', 'a1', 'b']);
	});
});

/* ------------------------------------------------------------------------------------------------
 * COLUMNS DECLARED ONCE. The list owns the tracks; a row only hands its cells over.
 * ---------------------------------------------------------------------------------------------- */

const Columned = DataRows as unknown as Component<{
	items: readonly Job[];
	key: (item: Job) => string;
	row: Snippet<[Job]>;
	label: string;
	columns?: readonly Column[];
	actions?: string;
	folds?: boolean;
}>;

function renderColumned(columns: readonly Column[], actions?: string, folds?: boolean) {
	host = document.createElement('div');
	document.body.append(host);
	mount(Columned, {
		target: host,
		props: { items: jobs('a'), key, row, label: 'Jobs', columns, actions, folds }
	});
	flushSync();
	return host.querySelector('ul') as HTMLElement;
}

const DECLARED: readonly Column[] = [
	{ id: 'name', width: 'minmax(0, 3fr)' },
	{ id: 'status', label: 'Status', width: '7rem' },
	{ id: 'count', label: 'Done', width: 'minmax(0, 1fr)', align: 'end' }
];

describe('a list that declares its columns', () => {
	it('lays the declared tracks down once, on the list, with no track for hover actions', () => {
		const list = renderColumned(DECLARED, '8rem');

		expect(list.classList.contains('columned')).toBe(true);
		expect(list.style.getPropertyValue('--data-tracks')).toBe('minmax(0, 3fr) 7rem minmax(0, 1fr)');
		// With no hover to reveal them the actions are always showing, so there they stand in a track.
		expect(list.style.getPropertyValue('--data-tracks-touch')).toBe(
			'minmax(0, 3fr) 7rem minmax(0, 1fr) 8rem'
		);
	});

	it('keeps one small track for the arrow where a row folds, the one thing seen at rest', () => {
		const list = renderColumned(DECLARED, '8rem', true);

		expect(list.style.getPropertyValue('--data-tracks')).toBe(
			'minmax(0, 3fr) 7rem minmax(0, 1fr) var(--control-height-sm)'
		);
		expect(touchTracksOf({ columns: DECLARED, actions: undefined, folds: true })).toBe(
			'minmax(0, 3fr) 7rem minmax(0, 1fr) var(--control-height-sm)'
		);
	});

	it('draws one heading row over the columns, aligned as each column says', () => {
		const list = renderColumned(DECLARED, '8rem');
		const heads = [...list.querySelectorAll('.heads .head')];

		expect(list.querySelectorAll('.heads')).toHaveLength(1);
		expect(heads.map((one) => one.textContent)).toEqual(['', 'Status', 'Done', '']);
		expect(heads[2].classList.contains('end')).toBe(true);
		// The empty heading over the actions is drawn only where they have a track.
		expect(heads[3].classList.contains('rest')).toBe(false);
		expect(ROWS.replace(/\s+/g, ' ')).toContain('.head.tail:not(.rest) { display: none; }');
	});

	it('refuses a track sized by its content, which would move between pages', () => {
		for (const width of ['auto', 'max-content', 'minmax(0, max-content)', 'fit-content(10rem)']) {
			expect(() => checkColumns([{ id: 'name', width }], undefined), width).toThrow(/content/);
		}
		expect(() => checkColumns(DECLARED, 'min-content')).toThrow(/actions track/);
	});

	it('refuses one column declared twice', () => {
		expect(() => checkColumns([DECLARED[0], DECLARED[0]], undefined)).toThrow(/twice/);
	});

	it('takes a length or a share without complaint: the known positive', () => {
		expect(() => checkColumns(DECLARED, '8rem')).not.toThrow();
		expect(tracksOf({ columns: DECLARED, actions: undefined })).toBe(
			'minmax(0, 3fr) 7rem minmax(0, 1fr)'
		);
	});

	it('is a flex list when nothing is declared', () => {
		const list = renderColumned(undefined as unknown as Column[]);

		expect(list.classList.contains('columned')).toBe(false);
		expect(list.querySelector('.heads')).toBeNull();
	});
});

describe('a list on the pane edges', () => {
	it('paints one line between two rows, and none over the line that holds a heading', () => {
		const rule = ROWS.replace(/\s+/g, ' ');
		// One mechanism: a second (a positioned line beside the painted one) would draw the line twice.
		expect(rule).not.toContain('::before');
		expect(rule).toContain(
			'.rows.edges > :global(.line + .line:has(> .row.across, > .row-trigger > .row.across)) { background-image: none; }'
		);
	});
});
