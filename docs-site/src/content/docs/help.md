---
title: Help
description: Where to ask about Sift and how to report a bug.
---

Help with Sift is on GitHub, where you can ask a question or report a bug, and on [Sift's Discord]({{discord}}). To start, open [Sift's issues](https://github.com/nuvibes/sift/issues).

## Ask a question or report a bug

Search the issues first, because someone may have asked already. If nothing matches, open a new issue and say what you did, what you expected and what happened instead. A bug report that lists the steps to see it again is the quickest to fix.

A security problem never goes in a public issue. [SECURITY.md](https://github.com/nuvibes/sift/blob/main/SECURITY.md#report-a-vulnerability) has the private way to report it.

## Add the log to a bug report

Sift's log records what it did, and it's the first thing to read when something goes wrong. To open it, go to [Settings > Tasks and Activity > Logs](/settings/tasks#activity.log).

1. Click **Download log**. The file is a redacted zip of the logs: personal details and secrets are removed before it's saved. It holds every log whole, whatever the screen is filtered to.
2. Attach the file to your issue.

In the Sift app the file holds the logs on that computer. If the app shows a library on another computer, download the log there too.

> **Note:** **Copy for a bug report** copies the lines on screen as they are, without redaction. Read the lines before you paste them anywhere public.

## If Sift keeps stopping

When Sift stops several times in a row, or stops while it's starting, the Sift app shows a dialog. It says what happened, its exit code and what that code usually means. It offers these:

- **Start without them**: starts Sift this once with face recognition, Smart Search and watermark reading held off. Your settings don't change.
- **Download log**: saves the same redacted zip without Sift running. The dialog comes back and says where the file is, and its folder opens once you've answered it. Attach the file to your issue.
- **Quit**: closes Sift.

Your library may not be where Sift was set up to find it, for example on a drive that isn't plugged in. The dialog then names the folder and offers these instead:

- **Start again**: plug the drive in first, then start again with the same folder.
- **Choose again**: asks where your library is. A folder with no library in it starts a new, empty library there, and your library stays where it is.
- **Download log** and **Quit**, as above.

## Discord

On Sift's Discord, people who use Sift ask questions and help each other. To join it, open [Sift's Discord]({{discord}}).
