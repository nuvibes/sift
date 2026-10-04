# Security

This page says how to report a vulnerability in Sift, what Sift is built to protect, and how to
check that a download is a real release.

## Report a vulnerability

Report it privately, never as a public issue. Use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository: open the **Security** tab and click **Report a vulnerability**. Only the
maintainers can read the thread it opens.

Include the version, what an attacker can do, and the smallest way to reproduce it. A short proof
of concept is welcome. Leave out passwords, keys and media from a real library.

You'll get an acknowledgment within a week. If the report is confirmed, you're told when the fix is
released and, unless you'd rather not be, credited in the advisory. Test only against a Sift you
run yourself.

## What Sift protects

Sift is a server a person runs for themselves and the people they invite. It holds files a person
may not want seen, the keys to their Sites and tunnels, and the computer it runs on. The threat
model in one page:

| Who | What they can reach | What Sift keeps from them |
|---|---|---|
| Anyone on the internet | Nothing, unless the admin exposes Sift behind a proxy | Every route needs a signed-in User; sign-in is slowed per name, never locked |
| Anyone on the same network | The sign-in page, after the admin turns on network sharing | The same; the connection is plain HTTP, so it's for a network you trust |
| A User who isn't an admin | What an admin shared with them | Every other file, folder and record, and any sign that a Hidden file exists |
| An admin | The whole library, except what's Hidden | Hidden files until they unlock Hidden |
| A web page, link or media file | Whatever Sift does with what it was handed | Running a command, reading a file, or reaching the computer through it |
| Another device in a swap | Only after both people compare the swap's code | Every file nobody picked to send |

### In scope

Anything that lets one person reach what their sign-in may not, or reach the computer Sift runs on:

- Signing in, sessions, passwords and the Hidden PIN.
- Hidden: reading a Hidden file, or learning that one exists.
- Sharing: reading, writing or listing something no share covers.
- Reaching a file outside the folders Sift was given, or Sift changing a file nobody confirmed.
- Running a command, reading a file or making a request on Sift's behalf from something a person
  supplied: a link, a file name, a media file, a setting.
- Tunnels: a download leaving by the wrong route, or a tunnel's key becoming readable.
- The desktop app: a page that isn't Sift reaching the computer through it, or an update installed
  without a valid signature.
- Swap: a device that wasn't paired reading or sending files.
- Anything that sends a file's fingerprint, name or picture to a stash-box or AcoustID when the
  admin hasn't turned that lookup on.

### Not in scope

- An admin changing the library or its settings. That's what the admin role is for.
- Denial of service by asking Sift for a lot of work it was built to do, such as a large scan or a
  long transcode.
- An attack that needs an attacker already on the computer, or already holding a User's password.
- A flood of sign-in attempts against a Sift exposed to the internet with no rate limit at the
  proxy. The limit is part of running Sift that way (see below).
- A scanner's finding with no working attack behind it.

## How Sift is meant to be run

- **On one Windows computer.** The server Sift starts listens on `127.0.0.1` only, until the admin
  turns on **Share this library on my network**, which serves plain HTTP to that network.
- **Changing your files only when asked.** A scan only reads. Moving, renaming and deleting are
  presses a person confirms.
- **Behind a reverse proxy when it faces the internet.** The proxy, on the same computer, does two
  things Sift can't do from inside: it serves Sift over HTTPS, and it rate-limits sign-in by the
  caller's real address.

Checking a password runs a deliberately slow hash, so a flood of sign-ins slows everybody's. Sift
already delays repeated wrong guesses on one name and runs only a few hashes at the same time. The proxy
limit stops the flood itself. A minimal nginx example:

```nginx
limit_req_zone $binary_remote_addr zone=sift_auth:10m rate=5r/s;

location /api/auth/ {
    limit_req zone=sift_auth burst=10 nodelay;
    proxy_pass http://127.0.0.1:5171;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

The proxy sends `X-Forwarded-Proto: https`, so Sift marks the session cookie Secure.

## Saved Cookies and a forgotten password

A Site's saved Cookies are encrypted with a key locked with the password of the User who
saved them. The password is stored nowhere, so a backup file by itself gives nobody those Cookies.

A forgotten password is reset on the computer Sift runs on, which nobody on the internet can
reach. Quit Sift, then run this in a Command Prompt:

```bat
"%LOCALAPPDATA%\Programs\Sift\resources\runtime\python.exe" -m sift.slices.auth.admin_cli reset-password
```

From a source checkout, run `uv run sift-admin reset-password`. The reset keeps your library and
deletes that User's saved Cookies, because nothing can open them without the old password. The
command says so and asks before it changes anything.

## Supported versions

Security fixes go into the newest release only. Sift tells you when a new release is out.

## Verifying a release

Every installer is published beside a `.sha256` file with its SHA-256, a `.manifest.json` naming
its version, file name and SHA-256, and a [minisign](https://jedisct1.github.io/minisign/)
signature (`.minisig`) over each of those two. The public key is also built into the desktop app:

```
untrusted comment: minisign public key CC723BD80C70FB26
RWQm+3AM2DtyzCk6rdK/3lnoHwbSpaaIvkUnzyyx5eioE8OlcHsjM1RP
```

To check a download by hand, save the key as `sift.pub` beside the three files and run:

```sh
minisign -Vm Sift-<version>-x64-setup.exe.sha256 -p sift.pub
```

Then compare the installer's SHA-256 with the one in the verified file. Sift checks the signed
manifest and the SHA-256 before it runs an update, and refuses a release that isn't newer than the
one running.

## The checks on every change

This isn't a claim that Sift is secure. It's what a change has to get past before it's published:

- `gitleaks` over every commit, and over the whole history before every push.
- `semgrep` rules written for Sift's own rules, each with tests that it fires on a violation and
  stays quiet on the correct code.
- `pip-audit` on the locked Python packages, and `npm audit` on the browser client and the desktop
  app.
- An authorization matrix that sends every route every role, and a browser run that walks a
  guest's screens for anything they shouldn't be offered.
