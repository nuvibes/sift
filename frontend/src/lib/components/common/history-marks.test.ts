/*
 * The seven ledger acts that need a mark of their own (a pause, a cancel and a finished task are
 * not "settled at the workbench", which is where `_EVENT_KINDS` on the server would otherwise send
 * them). Each has its own mark here; its words are the server's (`sentences.MEANS`, held by
 * `test_history_false_lines.py`).
 */
import { describe, expect, it } from 'vitest';

import { markOf, markWords } from './history';

const SEVEN = [
	'paused',
	'resumed',
	'cookies_saved',
	'cookies_replaced',
	'cookies_forgotten',
	'canceled',
	'ran',
	'song_named'
];

describe('the acts that wore the workbench mark', () => {
	it.each(SEVEN)('%s wears a mark of its own, not the tray and not the unknown one', (kind) => {
		expect(markOf(kind)).not.toBe(markOf('decided'));
		expect(markOf(kind)).not.toBe(markOf('a kind nobody has heard of'));
	});

	it("says the server's words for the act, never a word of its own", () => {
		expect(markWords({ via: null, means: 'A download was paused', actor: 'sift' })).toBe(
			'A download was paused'
		);
	});
});
