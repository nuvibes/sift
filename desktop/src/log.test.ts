/* The shell's log, and above all the two things that would each be a real fault and look like
 * nothing at all: a record that carries somebody's name into a bug report, and a logger that
 * throws and takes the thing it was logging with it. */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { paths, resetElectronStub } from '../test/electron-stub';
import {
	isDetailed,
	log,
	logTo,
	record,
	REDACTED,
	scrub,
	setDetail,
	setHidePersonal,
	tail
} from './log';

let home: string;

beforeEach(() => {
	resetElectronStub();
	home = fs.mkdtempSync(path.join(os.tmpdir(), 'sift-shell-log-'));
	paths.userData = home;
	logTo(null);
});

afterEach(() => {
	logTo(null);
	fs.rmSync(home, { recursive: true, force: true });
});

/** Every record the log holds now, parsed. */
function written(): Record<string, unknown>[] {
	return tail(1000).lines.map((one) => JSON.parse(one) as Record<string, unknown>);
}

describe('where it goes', () => {
	it('sits beside the shell settings rather than in the library', () => {
		// The library is another computer in client mode, and the shell has to be able to write a
		// log on the machine somebody is actually sitting at.
		expect(log.where()).toBe(path.join(home, 'shell.log'));
	});

	it('reports itself absent before anything is written, rather than empty', () => {
		// Two different answers: a log that exists and is quiet, and no log at all.
		const found = tail(10);
		expect(found.present).toBe(false);
		expect(found.lines).toEqual([]);
	});

	it('makes the folder if it is not there', () => {
		fs.rmSync(home, { recursive: true, force: true });
		log.info('drag.started', { how: 'local' });
		expect(written()).toHaveLength(1);
	});
});

describe('the record', () => {
	it('is one JSON object per line, in the shape the server writes', () => {
		// `slices/logs` parses `timestamp`, `level` and `event` off each line and the screen draws
		// them.
		const one = JSON.parse(record('warning', 'drag.refused', { why: 'no-origin' }));
		expect(one.event).toBe('drag.refused');
		expect(one.level).toBe('warning');
		expect(typeof one.timestamp).toBe('string');
		expect(one.timestamp).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
		expect(one.why).toBe('no-origin');
	});

	it('keeps numbers and booleans as themselves', () => {
		// `waited_ms=1500` has to be a number, or the one field that answers "did the share time
		// out" arrives as a string and every comparison on it is a string comparison.
		const one = JSON.parse(
			record('info', 'drag.share_not_used', { waited_ms: 1501, timed_out: true })
		);
		expect(one.waited_ms).toBe(1501);
		expect(one.timed_out).toBe(true);
	});

	it('leaves out a field nobody passed rather than writing it null', () => {
		const one = JSON.parse(record('info', 'x', { given: 'yes', missing: undefined }));
		expect(one.given).toBe('yes');
		expect('missing' in one).toBe(false);
	});

	it('writes each call as its own line', () => {
		log.info('one');
		log.warning('two');
		log.error('three');
		expect(written().map((one) => one.event)).toEqual(['one', 'two', 'three']);
		expect(written().map((one) => one.level)).toEqual(['info', 'warning', 'error']);
	});
});

describe('the detail, at the level the setting says', () => {
	afterEach(() => {
		setDetail(false);
	});

	it('writes what happened always, and the detail only while the Detail setting is on', () => {
		setDetail(false);
		log.debug('drag.path', { how: 'share' });
		log.info('drag.started', { how: 'share' });
		setDetail(true);
		log.debug('drag.path', { how: 'fetched' });
		expect(isDetailed()).toBe(true);

		expect(written().map((one) => [one.level, one.event, one.how])).toEqual([
			['info', 'drag.started', 'share'],
			['debug', 'drag.path', 'fetched']
		]);
	});
});

describe('redaction, which happens where the record is written', () => {
	/* The server's rule, from kernel/log.py: a path is REDUCED, not erased. */
	it('takes the account name out of a Windows path and leaves the rest', () => {
		expect(scrub('C:\\Users\\someone\\AppData\\Local\\Sift\\data\\sift.log')).toBe(
			`C:\\Users\\${REDACTED}\\AppData\\Local\\Sift\\data\\sift.log`
		);
	});

	it('does the same for a path that has already been through JSON once', () => {
		// The doubled form is how a path usually reaches a log at all, and the single-slash pattern
		// does not match it, and the account name would be left standing.
		expect(scrub('C:\\\\Users\\\\someone\\\\AppData')).toBe(
			`C:\\\\Users\\\\${REDACTED}\\\\AppData`
		);
	});

	it('writes the name whole while Hide personal details in the log is off, and the password never', () => {
		setHidePersonal(false);
		try {
			log.info('library.opened', {
				path: 'C:\\Users\\someone\\Sift',
				server: 'https://u:pw@box:5171'
			});
			const [one] = written();
			expect(one.path).toBe('C:\\Users\\someone\\Sift');
			expect(one.server).toBe(`https://u:${REDACTED}@box:5171`);
		} finally {
			setHidePersonal(true);
		}
	});

	it('takes it out of a POSIX path too', () => {
		expect(scrub('/home/kate/Videos/holiday.mp4')).toBe(`/home/${REDACTED}/Videos/holiday.mp4`);
		expect(scrub('/Users/kate/Movies/clip.mp4')).toBe(`/Users/${REDACTED}/Movies/clip.mp4`);
	});

	it('leaves a route alone', () => {
		// `/health` looks like a home directory to a careless regex.
		expect(scrub('/health')).toBe('/health');
		expect(scrub('/homes/shared/clip.mp4')).toBe('/homes/shared/clip.mp4');
	});

	it('leaves a share path alone, because nobody is named in it', () => {
		expect(scrub('\\\\nas\\Stash\\Sift Downloads\\clip.mp4')).toBe(
			'\\\\nas\\Stash\\Sift Downloads\\clip.mp4'
		);
	});

	it('takes a password out of a server address and keeps the address', () => {
		expect(scrub('https://someone:a-secret@server:5171/api')).toBe(
			`https://someone:${REDACTED}@server:5171/api`
		);
	});

	it('never writes the value of a field whose name says it is a credential', () => {
		const one = JSON.parse(record('info', 'x', { authToken: 'abc123', session_cookie: 'zzz' }));
		expect(one.authToken).toBe(REDACTED);
		expect(one.session_cookie).toBe(REDACTED);
	});

	it('reduces a path inside a field as well as one passed as a path', () => {
		log.info('drag.started', { how: 'local', path: 'C:\\Users\\someone\\Videos\\clip.mp4' });
		expect(written()[0].path).toBe(`C:\\Users\\${REDACTED}\\Videos\\clip.mp4`);
	});

	it('does not redact the things that say what happened', () => {
		// Redaction that removes the answer with the identity is redaction that gets switched off.
		const one = JSON.parse(
			record('info', 'drag.share_not_used', { waited_ms: 1500, shared_path: '\\\\nas\\Stash' })
		);
		expect(one.waited_ms).toBe(1500);
		expect(one.shared_path).toBe('\\\\nas\\Stash');
	});
});

describe('it cannot be the reason something failed', () => {
	it('says nothing and throws nothing when the file cannot be written', () => {
		// A folder where the log file should be: every write fails, for ever.
		fs.mkdirSync(path.join(home, 'shell.log'), { recursive: true });
		expect(() => log.info('drag.started', { how: 'local' })).not.toThrow();
	});

	it('answers absent rather than throwing when the log cannot be read', () => {
		fs.mkdirSync(path.join(home, 'shell.log'), { recursive: true });
		expect(() => tail(10)).not.toThrow();
		expect(tail(10).present).toBe(false);
	});
});

describe('the cost of a record is linear in its size', () => {
	/* Writing is synchronous and on the main thread, so a pathological scrub is a frozen window,
	   and redaction regexes are the kind that go quadratic unnoticed. */
	it('scrubs a very long field in no time at all', () => {
		const long = 'x'.repeat(64 * 1024);
		const began = performance.now();
		scrub(long);
		// The bounded pattern is immeasurable here and an unbounded one takes over a second.
		expect(performance.now() - began).toBeLessThan(250);
	});

	it('still finds a password in a long line', () => {
		// The known positive. A bound that no longer matches anything is a fast rule that protects
		// nothing, and it would pass the timing test above perfectly.
		const long = `${'x'.repeat(40_000)} https://someone:a-secret@server:5171/api`;
		expect(scrub(long)).toContain(`someone:${REDACTED}@server`);
		expect(scrub(long)).not.toContain('a-secret');
	});

	/* The same two questions of the home-path rules. */
	it('reduces a home path in a long line of things that nearly are one, in no time at all', () => {
		const long = `${'/homex/ C:\\Userss\\ '.repeat(10_000)} /home/someone/pictures`;
		const began = performance.now();
		scrub(long);
		expect(performance.now() - began).toBeLessThan(250);
	});

	it('still takes the name out of that line', () => {
		// The known positive again, and for the same reason: a home rule that stopped matching
		// would be the fastest one in the file.
		const long = `${'/homex/ C:\\Userss\\ '.repeat(10_000)} /home/someone/pictures`;
		const scrubbed = scrub(long);
		expect(scrubbed).toContain(`/home/${REDACTED}/pictures`);
		expect(scrubbed).not.toContain('someone');
		// And the near misses are left exactly as they were, which is the half a bound gets wrong.
		expect(scrubbed).toContain('/homex/');
	});
});

describe('it does not grow for ever', () => {
	it('rolls over and keeps one generation', () => {
		// Two megabytes of records, written as a few big ones rather than a hundred thousand small
		// ones so the test is about the rollover and not about speed.
		const big = 'x'.repeat(64 * 1024);
		for (let i = 0; i < 40; i += 1) log.info('filler', { padding: big });
		expect(fs.existsSync(path.join(home, 'shell.log.1'))).toBe(true);
		expect(fs.statSync(path.join(home, 'shell.log')).size).toBeLessThan(2 * 1024 * 1024);
	});

	it('goes on writing after a rollover rather than falling silent', () => {
		const big = 'x'.repeat(64 * 1024);
		for (let i = 0; i < 40; i += 1) log.info('filler', { padding: big });
		log.info('after.the.roll');
		expect(written().some((one) => one.event === 'after.the.roll')).toBe(true);
	});

	it('picks up the size of a file a previous run left, rather than adding to it', () => {
		// Without the first stat, a restart starts counting from zero and the file grows by another
		// whole cap before it rolls, which is how a "2 MB" log becomes 4 MB and nobody notices.
		fs.writeFileSync(path.join(home, 'shell.log'), 'x'.repeat(2 * 1024 * 1024));
		log.info('first.after.restart');
		expect(fs.existsSync(path.join(home, 'shell.log.1'))).toBe(true);
	});
});

describe('reading it back', () => {
	it('answers the last N, oldest first', () => {
		for (let i = 0; i < 10; i += 1) log.info(`event.${i}`);
		const found = tail(3);
		expect(found.lines).toHaveLength(3);
		expect(found.lines.map((one) => (JSON.parse(one) as { event: string }).event)).toEqual([
			'event.7',
			'event.8',
			'event.9'
		]);
	});

	it('reports where it is and how big, so a screen can say so', () => {
		log.info('one');
		const found = tail(10);
		expect(found.present).toBe(true);
		expect(found.path).toBe(path.join(home, 'shell.log'));
		expect(found.size).toBeGreaterThan(0);
	});
});
