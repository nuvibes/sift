/* What a smoke run has to do, and the ways it could look like it worked without working.
 *
 * The check in scripts/release.py reads the marker out of this module's source, so the string is
 * declared once. What a test can hold is the behaviour: the flag is answered only when it is asked
 * for, and the line is written before the caller is told to stop.
 */

import { describe, expect, it } from 'vitest';

import { answerSmokeRun, SMOKE_MARKER } from './smoke';

describe('a smoke run', () => {
	it('writes the marker, stops the process, and tells the caller it is over', () => {
		const said: string[] = [];
		const stopped: number[] = [];

		const answered = answerSmokeRun(
			['Sift.exe', '--smoke'],
			(line) => said.push(line),
			(code) => stopped.push(code)
		);

		expect(answered).toBe(true);
		expect(said).toEqual([`${SMOKE_MARKER}\n`]);
		expect(stopped).toEqual([0]);
	});

	it('says it started BEFORE it stops, which is the whole order', () => {
		/* A process stopped first writes nothing, and the release would read that as a shell that
		 * exited cleanly and never said it had started: a true refusal of a good build. */
		const order: string[] = [];

		answerSmokeRun(
			['--smoke'],
			() => order.push('said'),
			() => order.push('stopped')
		);

		expect(order).toEqual(['said', 'stopped']);
	});

	it('does nothing at all to an ordinary launch', () => {
		/* The whole application is behind the `else` of this call. A flag misread as present would
		 * quit Sift on somebody's desktop the moment they opened it. */
		const written: string[] = [];

		const stopped: number[] = [];
		const answered = answerSmokeRun(
			['Sift.exe'],
			(line) => written.push(line),
			(code) => stopped.push(code)
		);

		expect(answered).toBe(false);
		expect(written).toEqual([]);
		expect(stopped).toEqual([]);
	});

	it('is not answered by an argument that merely contains the word', () => {
		/* `includes` on the argument LIST, not on the joined command line: a saved server address or
		 * a file path carrying the word would otherwise stop the application from starting. */
		const written: string[] = [];

		const answered = answerSmokeRun(
			['Sift.exe', '--smoke-test', 'C:/--smoke/x.mp4'],
			(line) => written.push(line),
			() => written.push('stopped')
		);

		expect(answered).toBe(false);
		expect(written).toEqual([]);
	});

	it('ends the marker with a newline, which is what makes it findable in a stream', () => {
		/* The release reads standard output as text and looks for this line. Electron writes a
		 * newline of its own before anything the application prints, so the check searches rather
		 * than compares, and an unterminated marker would be indistinguishable from a truncated
		 * one.
		 */
		const written: string[] = [];

		answerSmokeRun(
			['--smoke'],
			(line) => written.push(line),
			() => undefined
		);

		expect(written[0]?.endsWith('\n')).toBe(true);
		expect(written[0]?.trim()).toBe(SMOKE_MARKER);
	});
});
