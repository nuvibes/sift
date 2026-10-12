/* Opening the port, which is the one thing Sift does that needs rights it otherwise never has. */

import { describe, expect, it, vi } from 'vitest';

import {
	firewallState,
	openFirewall,
	RULE_NAME,
	scripts,
	TUNNEL_RULE_NAME,
	tunnelProgram,
	type Runner
} from './firewall';

/** What the read script prints on a machine whose only network Windows files as private. */
const PRIVATE_OPEN = 'networks: Private\r\nopen Private\r\n';

const PORT = 5171;

/** Where an installed Sift keeps its tunnel program, under a folder with an apostrophe in it. */
const PROGRAM = "D:\\Apps\\Rock 'n' Roll\\Sift\\resources\\vendor\\bin\\wireproxy.exe";

/** What a script would look like on the far side of -EncodedCommand. */
function decode(base64: string): string {
	return Buffer.from(base64, 'base64').toString('utf16le');
}

describe('what it asks Windows', () => {
	it('asks about its own rule and its own port', () => {
		const read = scripts.read(PORT);

		expect(read).toContain(`-DisplayName '${RULE_NAME}'`);
		expect(read).toContain(`-contains '${PORT}'`);
	});

	/* Windows translates `netsh`'s output and does not translate object fields. */
	it('reads the rule as an object rather than parsing printed text', () => {
		expect(scripts.read(PORT)).toContain('Get-NetFirewallRule');
		expect(scripts.read(PORT)).not.toContain('netsh');
	});

	/* A rule that is disabled, outbound or blocking has the right name and lets nothing through. */
	it('counts only a rule that is enabled, inbound and allowing', () => {
		const read = scripts.read(PORT);

		expect(read).toContain("$rule.Enabled -ne 'True'");
		expect(read).toContain("$rule.Direction -ne 'Inbound'");
		expect(read).toContain("$rule.Action -ne 'Allow'");
	});

	/* The narrowest rule that works. A wider one is somebody's home network opened further than
	 * they agreed to, and they agreed to this on the strength of a sentence on a screen. */
	it('creates one inbound TCP rule, on private networks only', () => {
		const create = scripts.create(PORT);

		expect(create).toContain(`New-NetFirewallRule -DisplayName '${RULE_NAME}'`);
		expect(create).toContain('-Direction Inbound');
		expect(create).toContain(`-LocalPort ${PORT}`);
		expect(create).toContain('-Protocol TCP');
		expect(create).toContain('-Action Allow');
		expect(create).toContain('-Profile Private');
	});

	/* Windows files a new network as Public unless told otherwise, and a rule on private
	 * networks only opens nothing there. */
	it('reads which networks the machine is on, and the profile the rule reaches', () => {
		const read = scripts.read(PORT);
		expect(read).toContain('Get-NetConnectionProfile');
		expect(read).toContain('$rule.Profile');
	});

	/* The profile says which KIND of network; it says nothing about where a connection comes
	 * from. */
	it('lets in only addresses on the same local network, on every profile', () => {
		expect(scripts.create(PORT)).toContain('-RemoteAddress LocalSubnet');
		expect(scripts.create(PORT, 'any')).toContain('-RemoteAddress LocalSubnet');
	});

	it('opens on every network only when asked to', () => {
		expect(scripts.create(PORT, 'any')).toContain('-Profile Any -RemoteAddress LocalSubnet');
		expect(scripts.create(PORT)).toContain('-Profile Private -RemoteAddress LocalSubnet');
		expect(scripts.create(PORT)).not.toContain('-Profile Any -RemoteAddress');
	});

	/* An older version's rule, on a port Sift no longer uses, would otherwise sit beside the new
	 * one for ever, and the rule that is found first decides what the screen says. */
	it('replaces its own earlier rule instead of adding a second', () => {
		expect(scripts.create(PORT)).toContain(`Remove-NetFirewallRule -DisplayName '${RULE_NAME}'`);
	});

	it('refuses anything that is not a port', async () => {
		const never: Runner = () => Promise.reject(new Error('should not have run'));

		await expect(firewallState(0, never)).rejects.toThrow('not a port');
		await expect(firewallState(70_000, never)).rejects.toThrow('not a port');
		await expect(firewallState(5171.5, never)).rejects.toThrow('not a port');
		await expect(openFirewall(Number.NaN, never)).rejects.toThrow('not a port');
	});
});

describe('the elevated half', () => {
	it('raises the operating system own prompt and waits for it', () => {
		const elevate = scripts.elevate(scripts.create(PORT));

		expect(elevate).toContain('-Verb RunAs');
		expect(elevate).toContain('-Wait');
	});

	/* The inner script travels as an argument of the outer one, through two layers of quoting. */
	it('carries the rule across intact', () => {
		const create = scripts.create(PORT);
		const elevate = scripts.elevate(create);
		const carried = /'-EncodedCommand','([A-Za-z0-9+/=]+)'/.exec(elevate);

		expect(carried).not.toBeNull();
		expect(decode((carried as RegExpExecArray)[1] as string)).toBe(create);
	});

	/* A cancelled prompt is an ordinary answer, not a crash. */
	it('survives a prompt that was refused', () => {
		expect(scripts.elevate('whatever')).toContain('catch');
	});
});

describe('what it answers', () => {
	it('says open only when Windows says so', async () => {
		expect(await firewallState(PORT, async () => PRIVATE_OPEN)).toEqual({
			state: 'open',
			networks: ['Private'],
			scope: 'private',
			tunnel: 'unknown'
		});
		expect(await firewallState(PORT, async () => 'networks: Public\r\nclosed\r\n')).toEqual({
			state: 'closed',
			networks: ['Public'],
			scope: null,
			tunnel: 'unknown'
		});
	});

	/* The rule is there and reads as open, and the network the machine is on is one the rule
	 * does not reach. */
	it('says which networks the rule reaches, so a public network can be told from a private one', async () => {
		const onPublic = await firewallState(
			PORT,
			async () => 'networks: Public,Private\r\nopen Private\r\n'
		);
		expect(onPublic.networks).toEqual(['Public', 'Private']);
		expect(onPublic.scope).toBe('private');

		const everywhere = await firewallState(PORT, async () => 'networks: Public\r\nopen Any\r\n');
		expect(everywhere.scope).toBe('any');
		const domain = await firewallState(
			PORT,
			async () => 'networks: DomainAuthenticated\r\nopen Private\r\n'
		);
		expect(domain.networks).toEqual(['Domain']);
	});

	/* Three ways of not knowing, and all of them must read as not knowing rather than as closed:
	 * the screen offers to open the port when it is closed, and offering that to somebody whose
	 * PowerShell could not be asked is an administrator prompt raised for no reason. */
	it('does not guess when it could not ask', async () => {
		const unknown = { state: 'unknown', networks: null, scope: null, tunnel: 'unknown' };
		expect(await firewallState(PORT, async () => null)).toEqual(unknown);
		expect(await firewallState(PORT, async () => '')).toEqual(unknown);
		expect(await firewallState(PORT, async () => 'Get-NetFirewallRule is not recognized')).toEqual(
			unknown
		);
	});

	/* THE ANSWER IS READ, NOT CLAIMED. */
	it('answers with the state afterwards, whatever the elevated half said', async () => {
		const run = vi.fn<Runner>();
		run
			.mockResolvedValueOnce('') // the elevated half, saying nothing useful
			.mockResolvedValueOnce(PRIVATE_OPEN); // the read that follows it

		expect((await openFirewall(PORT, run)).state).toBe('open');
		expect(run).toHaveBeenCalledTimes(2);
	});

	it('says closed when the prompt was refused and nothing changed', async () => {
		const run = vi.fn<Runner>();
		run.mockResolvedValueOnce(null).mockResolvedValueOnce('networks: Private\r\nclosed\r\n');

		expect((await openFirewall(PORT, run)).state).toBe('closed');
	});

	/* The elevated call is given minutes because a person has to answer a dialog; the read is
	 * given seconds because nobody is waiting on it. */
	it('gives the prompt longer than the read', async () => {
		const waits: number[] = [];
		const run: Runner = async (_script, timeout) => {
			waits.push(timeout);
			return 'closed';
		};

		await openFirewall(PORT, run);
		expect(waits[0]).toBeGreaterThan(waits[1] as number);
	});
});

/* The tunnel program's WireGuard socket listens on every address, so Windows asks the person about
 * it at the first swap hosted. The rule goes in with Sift's own, under the same one prompt. */
describe('the tunnel program', () => {
	it('is let in by its own rule, inbound, on every network, in the same script as the port', () => {
		const create = scripts.create(PORT, 'private', PROGRAM);
		const quoted = `'${PROGRAM.replaceAll("'", "''")}'`;

		expect(create).toContain(`New-NetFirewallRule -DisplayName '${TUNNEL_RULE_NAME}'`);
		expect(create).toContain(`-Program ${quoted}`);
		expect(create).toMatch(
			/'Sift tunnel' -Direction Inbound -Program .* -Action Allow -Profile Any/
		);
		expect(create).toContain(`Remove-NetFirewallRule -DisplayName '${TUNNEL_RULE_NAME}'`);
		expect(create.indexOf(`'${RULE_NAME}' -Direction`)).toBeLessThan(
			create.indexOf(`'${TUNNEL_RULE_NAME}' -Direction`)
		);
		expect(create.indexOf(`'${TUNNEL_RULE_NAME}' -Direction`)).toBeLessThan(
			create.indexOf('exit 0')
		);
	});

	it('travels in the one elevated prompt', () => {
		const create = scripts.create(PORT, 'any', PROGRAM);
		const carried = /'-EncodedCommand','([A-Za-z0-9+/=]+)'/.exec(scripts.elevate(create));

		expect(decode((carried as RegExpExecArray)[1] as string)).toContain(TUNNEL_RULE_NAME);
	});

	it('is read back as the port is: an enabled inbound allow for that program', () => {
		const read = scripts.read(PORT, PROGRAM);

		const quoted = `'${PROGRAM.replaceAll("'", "''")}'`;
		expect(read).toContain(`Get-NetFirewallApplicationFilter -Program ${quoted}`);
		expect(read).toContain("$rule.Enabled -ne 'True'");
		expect(read).toContain("$rule.Direction -ne 'Inbound'");
		expect(read).toContain("$rule.Action -eq 'Allow'");
		expect(read.indexOf('tunnel: ')).toBeLessThan(read.indexOf("Write-Output 'closed'"));
	});

	/* The path is the one other value the scripts are built from, so it is checked like the port. */
	it('refuses a path that is not the shipped program', async () => {
		const never: Runner = () => Promise.reject(new Error('should not have run'));

		expect(() => scripts.create(PORT, 'private', 'wireproxy.exe')).toThrow(
			'not the tunnel program'
		);
		expect(() => scripts.create(PORT, 'private', 'C:\\Windows\\notepad.exe')).toThrow(
			'not the tunnel program'
		);
		expect(() => scripts.read(PORT, "C:\\x\\wireproxy.exe'\n; Remove-Item C:\\")).toThrow(
			'not the tunnel program'
		);
		await expect(openFirewall(PORT, never, 'private', 'C:\\x\\other.exe')).rejects.toThrow(
			'not the tunnel program'
		);
	});

	it('is the shipped program in the vendored tools folder', () => {
		expect(tunnelProgram()).toMatch(/vendor[\\/]bin[\\/]wireproxy\.exe$/);
	});

	it('reports what Windows said about it, and not knowing when it said nothing', async () => {
		const open = await firewallState(
			PORT,
			async () => 'networks: Private\r\ntunnel: open\r\nopen Private\r\n',
			PROGRAM
		);
		expect(open.tunnel).toBe('open');
		const shut = await firewallState(
			PORT,
			async () => 'networks: Private\r\ntunnel: closed\r\nclosed\r\n',
			PROGRAM
		);
		expect(shut).toEqual({
			state: 'closed',
			networks: ['Private'],
			scope: null,
			tunnel: 'closed'
		});
		const mumbled = await firewallState(
			PORT,
			async () => 'networks: Private\r\ntunnel: maybe\r\nclosed\r\n',
			PROGRAM
		);
		expect(mumbled.tunnel).toBe('unknown');
	});

	it('is opened and read back with the same program', async () => {
		const asked: string[] = [];
		const run: Runner = async (script) => {
			asked.push(script);
			return 'networks: Private\r\ntunnel: open\r\nopen Private\r\n';
		};

		expect((await openFirewall(PORT, run, 'private', PROGRAM)).tunnel).toBe('open');
		const [elevated, read] = asked as [string, string];
		const carried = /'-EncodedCommand','([A-Za-z0-9+/=]+)'/.exec(elevated);
		expect(decode((carried as RegExpExecArray)[1] as string)).toContain("Rock ''n'' Roll");
		expect(read).toContain("Rock ''n'' Roll");
	});
});
