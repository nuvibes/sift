/*
 * The first read of a library carries its one recommendation: download the face models and turn
 * faces on before the scan, so the scan finds the people as it reads and no second pass is needed.
 *
 * Read from the source, because mounting Browse is mounting the whole wall. What is held is that
 * the sentence sits with the Scan now press, links to the switch itself, and is said only while
 * faces are off.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';

const source = readFileSync('src/routes/browse/+page.svelte', 'utf8');
const scanNow = source.slice(
	source.indexOf('{#snippet scanNow()}'),
	source.indexOf('{/snippet}', source.indexOf('{#snippet scanNow()}'))
);

describe('the first read', () => {
	it('recommends the face models beside the Scan now press, linking to the switch', () => {
		expect(scanNow).toContain("scanEverything('now')");
		/* No link to the models' download: the Faces pane draws that row only once the switch is
		   on, so a link to it from here, where faces are off, would land on nothing. */
		expect(scanNow).not.toContain('faces.download');
		expect(source).toMatch(/const FACES_KEY = 'faces\.enabled';/);
	});

	it('links the switch where it is turned on, not the Faces pane that only points there', () => {
		/* The Faces pane draws a pointer row for the switch ("Change in Identify settings"); a link to
		   that row would leave the reader one press short. Import tasks claims the key and opens
		   Identify's page. */
		expect(scanNow).toMatch(
			/<SettingLink section="tasks" setting=\{FACES_KEY\}\s*>Settings > Tasks and Activity > Import tasks<\/SettingLink/
		);
		expect(scanNow).not.toMatch(/<SettingLink section="faces" setting=\{FACES_KEY\}/);
	});

	it('links quiet hours where they are set', () => {
		expect(scanNow).toMatch(
			/<SettingLink section="tasks" setting="tasks\.quiet-hours"\s*>Settings > Tasks and Activity<\/SettingLink/
		);
	});

	it('reads the switch again when a setting moves, so the sentence goes once faces are on', () => {
		expect(source).toMatch(
			/whenChanged\(settingChanges, \(\) => \{\s*if \(session\.isAdmin\) void readFaces\(\);\s*\}\);/
		);
	});

	it('says it only while faces are off', () => {
		const at = scanNow.indexOf('<SettingLink section="tasks" setting={FACES_KEY}');
		const guard = scanNow.lastIndexOf('{#if', at);
		expect(scanNow.slice(guard, at)).toMatch(/^\{#if facesOn === false\}/);
	});
});
