import { describe, expect, it, vi } from 'vitest';
import { ASSIGN_TYPE, carriesAssign, readAssign, startAssign } from './drag-assign.svelte';

/* The drag that files something without moving it.
 *
 * The payload carries ids and nothing else. That is the property worth guarding: there is no path
 * in it, so the gesture has no way to move a file even if somebody later wired it to something
 * that could.
 *
 * The private media type is the other half. `text/plain` is readable by any page and writable by
 * any page, so a drag out of a text editor would otherwise arrive looking like a valid asset id.
 */

/** A DataTransfer good enough to carry a payload. jsdom does not implement one. */
function transfer(initial: Record<string, string> = {}) {
	const store = new Map(Object.entries(initial));
	return {
		effectAllowed: 'none',
		dropEffect: 'none',
		get types() {
			return Array.from(store.keys());
		},
		setData: (type: string, value: string) => void store.set(type, value),
		getData: (type: string) => store.get(type) ?? ''
	};
}

function dragEvent(dataTransfer: ReturnType<typeof transfer> | null): DragEvent {
	return { dataTransfer, preventDefault: vi.fn() } as unknown as DragEvent;
}

describe('what a drag carries', () => {
	it('round-trips the ids it was given', () => {
		const data = transfer();
		startAssign(dragEvent(data), { kind: 'asset', ids: ['a1', 'a2'] });

		expect(readAssign(dragEvent(data))).toEqual({ kind: 'asset', ids: ['a1', 'a2'] });
	});

	it('carries no path, so the gesture cannot move a file', () => {
		const data = transfer();
		startAssign(dragEvent(data), { kind: 'asset', ids: ['a1'] });

		const raw = data.getData(ASSIGN_TYPE);
		expect(raw).not.toContain('/');
		expect(JSON.parse(raw)).toEqual({ kind: 'asset', ids: ['a1'] });
	});

	it('says it is linking, not moving or copying', () => {
		const data = transfer();
		startAssign(dragEvent(data), { kind: 'asset', ids: ['a1'] });

		expect(data.effectAllowed).toBe('link');
	});

	it('does nothing when the browser gave no transfer to write into', () => {
		expect(() => startAssign(dragEvent(null), { kind: 'asset', ids: ['a1'] })).not.toThrow();
	});
});

describe('what it refuses to read', () => {
	it('ignores a drag from somewhere else entirely', () => {
		// The whole reason for the private type. Text dragged out of another application arrives
		// as text/plain, and reading that would let any page hand this one an asset id.
		const data = transfer({ 'text/plain': JSON.stringify({ kind: 'asset', ids: ['a1'] }) });

		expect(readAssign(dragEvent(data))).toBeNull();
		expect(carriesAssign(dragEvent(data), 'asset')).toBe(false);
	});

	it('ignores a payload that is not JSON', () => {
		const data = transfer({ [ASSIGN_TYPE]: 'not json at all' });

		expect(readAssign(dragEvent(data))).toBeNull();
	});

	it.each([
		['a bare string', '"a1"'],
		['no ids', JSON.stringify({ kind: 'asset' })],
		['ids that are not strings', JSON.stringify({ kind: 'asset', ids: [1, 2] })],
		['an empty id', JSON.stringify({ kind: 'asset', ids: [''] })],
		['no kind', JSON.stringify({ ids: ['a1'] })],
		['null', 'null']
	])('ignores a payload with %s', (_name, raw) => {
		expect(readAssign(dragEvent(transfer({ [ASSIGN_TYPE]: raw })))).toBeNull();
	});
});

describe('deciding whether to accept a drop', () => {
	it('accepts one of ours', () => {
		const data = transfer({ [ASSIGN_TYPE]: JSON.stringify({ kind: 'asset', ids: ['a1'] }) });

		expect(carriesAssign(dragEvent(data), 'asset')).toBe(true);
	});

	it('reads the type list rather than the payload', () => {
		// During dragover the browser exposes only the types, never the data. A target that
		// decided by reading the payload would refuse every drop it was actually offered.
		const data = transfer({ [ASSIGN_TYPE]: '' });

		expect(carriesAssign(dragEvent(data), 'asset')).toBe(true);
	});

	it('refuses when there is no transfer at all', () => {
		expect(carriesAssign(dragEvent(null), 'asset')).toBe(false);
	});
});
