Tasks and Activity is where you choose when each task runs and see what Sift has done. To open it, go to [Settings > Tasks and Activity](/settings/tasks).

Come here to run a task now, change when it runs, choose what each import stage makes, or find out what happened to a file. The pane has three tabs.

![The Tasks and Activity pane in Settings](../../../assets/screens/settings-tasks.jpg)

## The tabs

- **Tasks**: every task, its **Edit** and its **Run now** press and its When. A When is **As files arrive**, **On a schedule**, **During quiet hours** or **Only when I press it**, as each task offers. Under the tasks, **Activity**: what is running and what ran, with each task's status, time left and progress, and the **Task Queue**. While a scan is still counting folders, the Scan row and every task after it show **Not known until every folder is counted.** A task that waits for a scan to read its files says **Waiting for the scan to finish.** When a network share holds the reading back, the Scan row names the library folders on that share. **Run now** on a bar opens that task's row.
- <a id="activity.history"></a>**App History**: everything Sift and you have done in your library, newest first. Select a name to open what it happened to.
- <a id="activity.log"></a>**Logs**: a record of what Sift did. Read it first when something goes wrong.

## On the Tasks tab

- <a id="tasks.quiet-hours"></a>**Quiet hours**: a stretch of each day when your computer is usually free. A task set to run during quiet hours waits for it to begin and pauses when it ends. **Edit** changes the hours.
- <a id="tasks.stages"></a>**Import tasks**: Scan, Generate and Identify, the three things Sift does with each file as it arrives, in the order Sift runs them. Each row says when it runs and what is still waiting for it; **Edit** opens the stage's own settings.
- <a id="importing.scan-stage"></a>**Scan**: finds new, changed and removed files in your library folders.
- <a id="importing.generate-stage"></a>**Generate**: creates thumbnails, hover previews, scrubber strips and fingerprints for new files.
- <a id="importing.identify-stage"></a>**Identify**: recognizes faces, describes files for Smart Search and reads watermarks. Its settings hold the three recognition switches.
- <a id="importing.folders"></a>**Folder-specific import settings**: gives one library folder its own settings, or lets it follow the default. **Edit** beside a folder opens them.
- **Other tasks**: work Sift does for the library as a whole, such as finding duplicates and creating backups.
- <a id="tasks.activity"></a>**Activity**: what is running and what ran, under **Library tasks** and **Other tasks**. Under it the **Task Queue**, twenty a page, with **Options** and **Type** to show one type of task.
- **Other settings**: settings filed with the tasks that belong to no task above.

## On the App History tab

- **Show**: **Everything**, or only the **Decisions**, each with its **Undo**.
- **Type**: shows only one type of thing.
- **Action**: shows only one action.
- **Undo** and **Undo all**: take back a decision, or every decision one press made, while they still can.
- **Report**: opens a task's report: how many files it read, how long it took and on which device. **Copy report** copies it.
- <a id="activity.decisions"></a>**Decisions**: App History showing only what was decided on Organize and what Sift filed by itself.
- <a id="activity.saved"></a>**Saved to a device**: App History showing only the copies someone saved to their own device.
- <a id="activity.runs"></a>**How long tasks take**: every task that ran over your library, from App History.

## Reading and saving the log

- **Show**, on the **Logs** tab: the least serious line to show: **Critical**, **Error**, **Warning**, **Info** or **Debug**. Each choice also shows everything more serious than it.
- **Filter the log**: shows only the lines holding the words you type.
- **Refresh**: reads the newest lines.
- **Copy for a bug report**: copies the lines on screen as they are, without redaction. Read the lines before you paste anywhere public.
- **Download log**: saves every log whole as one zip file, whatever the screen is filtered to, without your personal details and secrets. In the Sift app the file holds the library's log and the app's own. In a browser it holds the library's. The line under it, **Download redacted log**, says so. Attach this file to a bug report.
