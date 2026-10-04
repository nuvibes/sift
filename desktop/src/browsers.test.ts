/* Reading the machine's browsers out of the registry, and refusing to open anything else.
 *
 * The parsing is where this goes quietly wrong, and every case below is one that produces a WORKING
 * looking result rather than an error: a path truncated at the first space, a browser listed four
 * times because it is in two hives and two registry views, or a display name taken from the wrong
 * line. None of those throws; each one just makes the menu wrong.
 *
 * The last group is not about browsers at all. `openIn` is the one place in this application that
 * hands an address to another program, and the address came from a page.
 */

import { describe, expect, it } from 'vitest';

import { defaultValue, executableFrom, installed, isWebAddress, openIn } from './browsers';

describe('the executable in a registry command', () => {
	it('takes a quoted path whole, spaces and all', () => {
		expect(executableFrom('"C:\\Program Files\\Firefox\\firefox.exe" -osint -url "%1"')).toBe(
			'C:\\Program Files\\Firefox\\firefox.exe'
		);
	});

	/* Splitting on spaces gives `C:\Program`, which is not a file, and the failure arrives later
	   as a link that silently does nothing. */
	it('does not stop at the first space in an unquoted path', () => {
		expect(executableFrom('C:\\Program Files\\Chrome\\chrome.exe --single-argument %1')).toBe(
			'C:\\Program Files\\Chrome\\chrome.exe'
		);
	});

	it('answers nothing for a command with no executable in it', () => {
		expect(executableFrom('')).toBeNull();
		expect(executableFrom('"unterminated')).toBeNull();
		expect(executableFrom('rundll32 something')).toBeNull();
	});
});

describe('the default value of a key', () => {
	it('keeps the spaces inside the value', () => {
		/* Taking the LAST field would give `Files\Firefox\firefox.exe" ...`, which is why this reads
		   everything after the type rather than splitting on whitespace. */
		const printed =
			'HKEY_LOCAL_MACHINE\\Software\\Clients\\StartMenuInternet\\FIREFOX.EXE\r\n' +
			'    (Default)    REG_SZ    Mozilla Firefox\r\n';
		expect(defaultValue(printed)).toBe('Mozilla Firefox');
	});

	it('reads an expandable string too, which is what several browsers register', () => {
		expect(defaultValue('    (Default)    REG_EXPAND_SZ    %ProgramFiles%\\x\\y.exe')).toBe(
			'%ProgramFiles%\\x\\y.exe'
		);
	});

	it('answers nothing when the key has no default value', () => {
		expect(defaultValue('    Something    REG_SZ    else')).toBeNull();
		expect(defaultValue('')).toBeNull();
	});
});

describe('the list of browsers', () => {
	/** A registry that answers the way reg.exe does, from a table of keys. */
	function registry(entries: Record<string, { name?: string; command?: string }>) {
		const root = 'HKEY_LOCAL_MACHINE\\Software\\Clients\\StartMenuInternet';
		return async (args: string[]): Promise<string> => {
			const key = args[1];
			if (key.toLowerCase().endsWith('startmenuinternet')) {
				// Only HKLM answers; HKCU is empty, which is the ordinary shape on most machines.
				if (!key.startsWith('HKLM')) return '';
				return [root, ...Object.keys(entries).map((one) => `${root}\\${one}`)].join('\r\n');
			}
			const named = Object.entries(entries).find(([one]) => key.includes(one));
			if (named === undefined) return '';
			const [, entry] = named;
			if (key.endsWith('shell\\open\\command')) {
				return entry.command ? `    (Default)    REG_SZ    ${entry.command}` : '';
			}
			return entry.name ? `    (Default)    REG_SZ    ${entry.name}` : '';
		};
	}

	it('lists what is installed, by the name a person would recognise', async () => {
		const found = await installed(
			registry({
				'FIREFOX.EXE': { name: 'Mozilla Firefox', command: '"C:\\ff\\firefox.exe" -url "%1"' },
				Chrome: { name: 'Google Chrome', command: '"C:\\gc\\chrome.exe" -- "%1"' }
			})
		);

		expect(found.map((one) => one.name)).toEqual(['Google Chrome', 'Mozilla Firefox']);
		// The real path, not the lower-cased key it was deduplicated under: this is what gets
		// started, and what somebody reads back in the settings file.
		expect(found.map((one) => one.id)).toEqual(['C:\\gc\\chrome.exe', 'C:\\ff\\firefox.exe']);
	});

	/* Both hives and both registry views are asked, so one browser can be found up to four times.
	   Keyed on the executable, it is one entry. */
	it('counts a browser found in several places once', async () => {
		const found = await installed(
			registry({ Chrome: { name: 'Google Chrome', command: '"C:\\gc\\chrome.exe"' } })
		);

		expect(found).toHaveLength(1);
	});

	/* Several browsers leave the display name unset and are known only by their key. A menu row
	   with no words in it is worse than one saying `firefox.exe`. */
	it('falls back to the key name when a browser sets no display name', async () => {
		const found = await installed(
			registry({ 'FIREFOX.EXE': { command: '"C:\\ff\\firefox.exe"' } })
		);

		expect(found[0].name).toBe('FIREFOX.EXE');
	});

	it('skips a key with no command, rather than offering something it cannot start', async () => {
		const found = await installed(registry({ Broken: { name: 'Half installed' } }));

		expect(found).toEqual([]);
	});

	/* The container key is in its own listing. Without the guard it becomes a browser called
	   `StartMenuInternet` that cannot be started. */
	it('does not offer the container key itself as a browser', async () => {
		const found = await installed(
			registry({ Chrome: { name: 'Google Chrome', command: '"C:\\gc\\chrome.exe"' } })
		);

		expect(found.every((one) => !one.name.includes('StartMenuInternet'))).toBe(true);
	});
});

describe('what may be opened at all', () => {
	/* `shell.openExternal` hands an address to Windows, which opens it with whatever is
	   registered for its scheme. The address comes from a page, so without this refusal anything
	   that got code onto that page could open a local file or any protocol handler installed on
	   the machine. */
	it('accepts a web address', () => {
		expect(isWebAddress('https://example.com/a?b=c')).toBe(true);
		expect(isWebAddress('http://192.168.1.20:5171/')).toBe(true);
	});

	it('refuses everything else, including the ones that look harmless', () => {
		for (const address of [
			'file:///C:/Windows/System32/calc.exe',
			'ms-settings:privacy',
			'mailto:someone@example.com',
			'javascript:alert(1)',
			'not an address at all',
			''
		]) {
			expect(isWebAddress(address), address).toBe(false);
		}
	});

	it('will not start a browser for an address it would refuse', () => {
		/* Checked before the executable is even looked for, so a refused scheme cannot reach a
		   process launch by way of a browser that happens to exist. */
		expect(openIn('C:\\gc\\chrome.exe', 'file:///C:/Windows/System32/calc.exe')).toBe(false);
	});

	it('says so rather than claiming success when the chosen browser is gone', () => {
		/* `spawn` reports a missing executable asynchronously, long after the caller has been told
		   the link opened. Somebody who uninstalled the browser they chose would click a link, be
		   told nothing, and watch nothing happen. */
		expect(openIn('C:\\this\\was\\uninstalled.exe', 'https://example.com')).toBe(false);
	});
});
