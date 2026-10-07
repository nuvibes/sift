/* The sentences the facts panel is made of.
 *
 * Worth their own file because two surfaces draw them: the player and a Theater cell share one
 * panel, so a wrong answer here is wrong in two places at the same time. Every one of these takes
 * the shape the library hands over (half of it null on a file Sift has not finished reading), and
 * has to answer without inventing anything.
 */

import { describe, expect, it } from 'vitest';

import {
	bitrate,
	bufferedAhead,
	clock,
	codecs,
	depth,
	dimensions,
	noticeLabel,
	noticeWords,
	REPAIRED_WORDS,
	playingAs,
	rate,
	ratio,
	size
} from './facts';
import { fileFacts } from '$lib/design/testing.svelte';

describe('what the file is', () => {
	it('names both codecs the way the encoders are named', () => {
		expect(codecs(fileFacts({ vcodec: 'h264', acodec: 'aac' }))).toBe('H.264 / AAC');
	});

	it('passes an unknown codec through rather than hiding it', () => {
		expect(codecs(fileFacts({ vcodec: 'ffv1', acodec: null }))).toBe('ffv1');
	});

	it('says so when neither is known', () => {
		expect(codecs(null)).toBe('Unknown');
	});

	it('reduces a shape to the ratio people say out loud', () => {
		expect(ratio(fileFacts({ width: 1920, height: 1080 }))).toBe('16:9');
		expect(ratio(fileFacts({ width: 1440, height: 1080 }))).toBe('4:3');
	});

	it('falls back to a decimal for a shape nobody names', () => {
		expect(ratio(fileFacts({ width: 683, height: 384 }))).toBe('1.78:1');
	});

	it('treats a depth of zero as no answer, because that is what it means', () => {
		expect(depth(fileFacts({ bit_depth: 0 }))).toBe('Unknown');
		expect(depth(fileFacts({ bit_depth: 10 }))).toBe('10-bit');
	});

	it('drops a trailing zero from a whole frame rate and keeps a fractional one', () => {
		expect(rate(fileFacts({ fps: 60 }))).toBe('60 fps');
		expect(rate(fileFacts({ fps: 29.97 }))).toBe('29.97 fps');
	});

	it('has no answer for a size it does not have both halves of', () => {
		expect(dimensions(1920, null)).toBe('Unknown');
		expect(dimensions(1920, 1080)).toBe('1920 x 1080');
	});
});

describe('how big it is', () => {
	/* One formatter, and two points it holds. A thousand and twenty-four bytes is a KiB; `GB`
	   means a thousand million, and dividing by 1024 while writing `GB` would make the same file
	   read 5.6 GB in one panel and 6.0 GB on its own record two inches below. And zero is a
	   size: a file that is there and empty is worth being told about, and `Unknown` would hide
	   it. */
	it('picks the unit that keeps the number short', () => {
		expect(size(512)).toBe('512 B');
		expect(size(3_500_000)).toBe('3.5 MB');
		expect(size(42_000_000_000)).toBe('42 GB');
	});

	it('says nothing about a size it was not given, and zero is not that', () => {
		expect(size(null)).toBe('Unknown');
		expect(size(0)).toBe('0 B');
	});

	it('divides the size by the length, counting everything in the file', () => {
		// 12 MB over 60 seconds is 1.6 Mbps, which is the number that decides whether it streams.
		expect(bitrate(fileFacts({ size_bytes: 12_000_000 }), 60)).toBe('1.6 Mbps');
	});

	it('drops to kbps for something small', () => {
		expect(bitrate(fileFacts({ size_bytes: 600_000 }), 60)).toBe('80 kbps');
	});

	it('has no bitrate for a still, which has no length to divide by', () => {
		expect(bitrate(fileFacts({ size_bytes: 12_000_000 }), 0)).toBe('Unknown');
	});
});

describe('what is being done to it', () => {
	it('names what is happening now, in words nothing else has to explain', () => {
		expect(playingAs('direct')).toBe('Direct play');
		expect(playingAs('remux')).toBe('Remuxing');
		expect(playingAs('transcode')).toBe('Transcoding');
		expect(playingAs('unread')).toBe('Not read yet');
	});

	/* The copy's reason rides in brackets rather than becoming a fourth answer: the server decides
	   between three routes, and `copy_kind` says which of two problems a remux was written for. */
	it('keeps the two reasons for a copy apart without inventing a fourth route', () => {
		expect(playingAs('remux', 'repaired')).toBe('Remuxing (repaired)');
		expect(playingAs('remux', 'repackaged')).toBe('Remuxing');
	});

	it('says "Not yet" before the server has answered', () => {
		expect(playingAs(null)).toBe('Not yet');
	});
});

describe('the mark in the corner of a picture', () => {
	const converted = {
		route: 'transcode',
		reason: 'Your browser cannot play VP9, so it is being converted.'
	};

	it("says the plan, in the server's words, for any path that is not direct play", () => {
		expect(noticeWords(converted, null)).toBe(converted.reason);
		expect(noticeWords({ route: 'remux', reason: 'a copy' }, 'repaired')).toBe('a copy');
		expect(noticeLabel(converted, null)).toBe('Why this file is being converted');
		expect(noticeLabel({ route: 'remux' }, null)).toBe('Why this file is playing from a copy');
	});

	it('says nothing for a stall, which the panel over the picture already says', () => {
		const stall = { ...converted, streamable: false };
		expect(noticeWords(stall, null)).toBe('');
		expect(noticeWords({ ...converted, streamable: true }, null)).toBe(converted.reason);
	});

	it('falls back to the repair for a file that plays as it is', () => {
		expect(noticeWords({ route: 'direct', reason: 'as it is' }, 'off')).toContain('switched off');
		expect(noticeWords({ route: 'direct', reason: 'as it is' }, null)).toBe('');
		expect(noticeWords(null, 'pending')).toContain('finishes a repackaged copy');
		// The player's own sentence, word for word, and never the packet-order cause it dropped.
		expect(noticeWords(null, 'repaired')).toBe(REPAIRED_WORDS);
		for (const state of ['repaired', 'pending', 'off']) {
			expect(noticeWords(null, state)).not.toContain('far apart');
		}
		expect(noticeLabel({ route: 'direct' }, 'off')).toBe('Why skipping through this file is jerky');
	});

	it('says what the copy IS in all three states: a repackaged copy, the same picture and sound', () => {
		// "A smoother copy" would name no act and draw the question "is it transcoding? remuxing?". It
		// is a remux (nothing re-encoded), and each of the three sentences says so in the same words.
		for (const state of ['repaired', 'pending', 'off']) {
			const said = noticeWords(null, state);
			expect(said).toContain('repackaged copy');
			expect(said).toContain('the same picture and sound');
			expect(said).not.toContain('smoother copy');
		}
	});

	it('says nothing for a file Sift has not read, which the stage says itself', () => {
		expect(noticeWords({ route: 'unread', reason: 'not read' }, null)).toBe('');
	});
});

describe('what the browser is holding', () => {
	/** A `buffered` list, which is what a media element hands over rather than an array. */
	function ranges(list: [number, number][]): HTMLVideoElement {
		return {
			buffered: {
				length: list.length,
				start: (index: number) => list[index][0],
				end: (index: number) => list[index][1]
			}
		} as unknown as HTMLVideoElement;
	}

	it('measures ahead of the playhead, in the range the playhead is in', () => {
		expect(bufferedAhead(ranges([[0, 30]]), 10)).toBe(20);
	});

	it('ignores a range the playhead is not in: a seek leaves several', () => {
		expect(
			bufferedAhead(
				ranges([
					[0, 5],
					[60, 90]
				]),
				70
			)
		).toBe(20);
	});

	it('holds nothing when there is no element and when nothing is buffered', () => {
		expect(bufferedAhead(null, 10)).toBe(0);
		expect(bufferedAhead(ranges([]), 10)).toBe(0);
	});
});

describe('the clock', () => {
	it('pads the seconds', () => {
		expect(clock(65)).toBe('1:05');
		expect(clock(0)).toBe('0:00');
	});

	it('says the hours of a film, as its record does, rather than ninety minutes', () => {
		expect(clock(90 * 60)).toBe('1:30:00');
	});

	it('answers for a length the element has not said yet', () => {
		expect(clock(NaN)).toBe('0:00');
		expect(clock(-1)).toBe('0:00');
	});
});
