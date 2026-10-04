import { describe, expect, it } from 'vitest';

import { isRemoteDrive } from './drives';

describe('isRemoteDrive', () => {
	it('knows a UNC path without asking', async () => {
		let asked = 0;
		const away = await isRemoteDrive('\\\\nas\\Stash\\library', async () => {
			asked += 1;
			return 'Fixed';
		});
		expect(away).toBe(true);
		expect(asked).toBe(0);
	});

	it('asks Windows what a lettered drive is', async () => {
		expect(await isRemoteDrive('Z:\\library', async () => 'Network\r\n')).toBe(true);
		expect(await isRemoteDrive('C:\\Users\\me\\Sift', async () => 'Fixed')).toBe(false);
	});

	it('asks for the letter in upper case, which is how Windows names drives', async () => {
		const letters: string[] = [];
		await isRemoteDrive('z:\\library', async (letter) => {
			letters.push(letter);
			return 'Fixed';
		});
		expect(letters).toEqual(['Z']);
	});

	it('does not refuse a folder it could not ask about', async () => {
		expect(await isRemoteDrive('D:\\library', async () => null)).toBe(false);
	});

	it('has nothing to say about a path with no drive', async () => {
		expect(await isRemoteDrive('library', async () => 'Network')).toBe(false);
	});
});
