---
title: 'Step 1: Install Sift'
description: Install Sift on Windows from its installer.
sidebar:
  order: 1
---

Sift is a desktop app for 64-bit Windows, and it installs from one installer file. To get it, download the file ending in `-x64-setup.exe` from the [latest release](https://github.com/nuvibes/sift/releases/latest) on GitHub.

## Run the installer

1. Open the installer you downloaded.
2. If Windows shows **Windows protected your PC**, click **More info**, then click **Run anyway**.
3. On **Where to install Sift**, keep the folder the installer offers or choose another, then click **Install**.
4. When **Sift is installed** appears, click **Next**, keep **Open Sift now** selected, and click **Finish**.

Sift opens in its own window and asks its first questions, which [Step 2: Your first start](/get-started/your-first-start/#_top) goes through.

## Why Windows warns you

Windows SmartScreen warns about an installer it doesn't recognize. The Sift installer isn't signed with a paid certificate, so Windows has no reputation for it yet. Sift checks every update against its own signature before the installer opens. To check a download yourself, read [Verifying a release](https://github.com/nuvibes/sift/blob/main/SECURITY.md#verifying-a-release).

## Where Sift keeps its files

Sift installs for you only, so Windows doesn't ask for administrator rights. The program goes in `%LOCALAPPDATA%\Programs\Sift`. Sift keeps its own data apart from it, in `%LOCALAPPDATA%\Sift` unless you choose another folder at your first start.

Your photos and videos stay where they are. Sift indexes them in place and keeps its thumbnails and previews in its own folder. Uninstalling Sift keeps your library too, unless you select **Also delete Sift's data (the database and settings)** in the uninstaller.

## Updates

Sift checks for a new version every few hours and tells you when one is out. To turn the check off, or to check straight away with **Check now**, open [Settings > Updates and Info > Check for new versions automatically](/settings/updates#updates.check_for_new_versions).

Sift never updates without asking you. When you install an update, Sift checks that the download came from Sift before the installer opens, and your library stays as it is.
