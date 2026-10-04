/* Opening the port, which is the one thing Sift does that needs rights it otherwise never has.
 *
 * What can be tested here is what is ASKED and what is BELIEVED: the exact rule the script would
 * create, that the elevated half really is elevated, and that the answer comes from reading the
 * state afterwards rather than from whether the command claimed to work. Whether Windows then
 * creates the rule is a hand check on a real machine: there is no firewall in a test.
 */

import { describe, expect, it, vi } from 'vitest';

import { firewallState, openFirewall, RULE_NAME, scripts, type Runner } from './firewall';

/** What the read script prints on a machine whose only network Windows files as private. */
const PRIVATE_OPEN = 'networks: Private\r\nopen Private\r\n';

const PORT = 5171;

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

	/* Windows translates `netsh`'s output and does not translate object fields. A check that read
	 * words would answer "closed" on a machine whose language nobody here chose, with the rule
	 * sitting right there. */
	it('reads the rule as an object rather than parsing printed text', () => {
		expect(scripts.read(PORT)).toContain('Get-NetFirewallRule');
		expect(scripts.read(PORT)).not.toContain('netsh');
	});

	/* A rule that is disabled, outbound or blocking has the right name and lets nothing through.
	 * Counting it would report a working setup to somebody whose other computer cannot connect. */
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

	/* Windows files a new network as Public unless told otherwise, and a rule on private networks
	 * only opens nothing there. So the machine's networks are read beside the rule, and opening on
	 * public networks as well is a choice the screen offers rather than a default it takes. */
	it('reads which networks the machine is on, and the profile the rule reaches', () => {
		const read = scripts.read(PORT);
		expect(read).toContain('Get-NetConnectionProfile');
		expect(read).toContain('$rule.Profile');
	});

	/* The profile says which KIND of network; it says nothing about where a connection comes from.
	 * Without the address scope the rule answers anything that can route to this machine, so the
	 * scope is on the rule whichever profile was asked for, and nowhere is it left off. */
	it('lets in only addresses on the same local network, on every profile', () => {
		expect(scripts.create(PORT)).toContain('-RemoteAddress LocalSubnet');
		expect(scripts.create(PORT, 'any')).toContain('-RemoteAddress LocalSubnet');
	});

	it('opens on every network only when asked to', () => {
		expect(scripts.create(PORT, 'any')).toContain('-Profile Any');
		expect(scripts.create(PORT)).not.toContain('-Profile Any');
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

	/* The inner script travels as an argument of the outer one, through two layers of quoting. It
	 * is carried encoded so there is nothing to escape, and this is what proves the thing that
	 * arrives on the far side is the rule above and not a mangled version of it. */
	it('carries the rule across intact', () => {
		const create = scripts.create(PORT);
		const elevate = scripts.elevate(create);
		const carried = /'-EncodedCommand','([A-Za-z0-9+/=]+)'/.exec(elevate);

		expect(carried).not.toBeNull();
		expect(decode((carried as RegExpExecArray)[1] as string)).toBe(create);
	});

	/* A cancelled prompt is an ordinary answer, not a crash. Windows reports it as a failure to
	 * start the process, which would otherwise be read as a missing exit code. */
	it('survives a prompt that was refused', () => {
		expect(scripts.elevate('whatever')).toContain('catch');
	});
});

describe('what it answers', () => {
	it('says open only when Windows says so', async () => {
		expect(await firewallState(PORT, async () => PRIVATE_OPEN)).toEqual({
			state: 'open',
			networks: ['Private'],
			scope: 'private'
		});
		expect(await firewallState(PORT, async () => 'networks: Public\r\nclosed\r\n')).toEqual({
			state: 'closed',
			networks: ['Public'],
			scope: null
		});
	});

	/* The rule is there and reads as open, and the network the machine is on is one the rule does
	 * not reach. Both facts travel, and the screen decides.
	 */
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
		const unknown = { state: 'unknown', networks: null, scope: null };
		expect(await firewallState(PORT, async () => null)).toEqual(unknown);
		expect(await firewallState(PORT, async () => '')).toEqual(unknown);
		expect(await firewallState(PORT, async () => 'Get-NetFirewallRule is not recognized')).toEqual(
			unknown
		);
	});

	/* THE ANSWER IS READ, NOT CLAIMED. Somebody who cancels the prompt, a machine where elevation
	 * is forbidden by policy, and a rule created and then removed by something else are three
	 * different stories with one true ending. */
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
	 * given seconds because nobody is waiting on it. A read that inherited the long one would hang
	 * a screen for three minutes on a machine where PowerShell never answers. */
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
