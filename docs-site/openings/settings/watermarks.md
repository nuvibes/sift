Watermarks is where you set up the scan that finds a Site's watermark on your files. To open it, go to [Settings > Watermarks](/settings/watermarks).

Come here to see how many files are scanned, or to choose what the scan runs on. The scan uses the PP-OCRv4 models, on your computer.

![The Watermarks pane in Settings](../../../assets/screens/settings-watermarks.jpg)

## On this pane

The first rows have no heading: the switch, how many files are scanned, when it runs, and **More settings**. The switch is turned on and off in [Settings > Tasks and Activity > Import tasks > Identify settings](/settings/tasks#importing.recognition). Then the pane draws one group:

- **Start over**: delete the watermark results.

## Rows the pane draws by hand

- <a id="watermarks.more"></a>**More settings**: **Edit** opens what Sift runs the scan on, and a fresh copy of the models. A download that can't connect shows why and what to check. It uses the proxy set in Windows, or in `HTTPS_PROXY`. A proxy set by an automatic configuration script isn't read.
- <a id="watermarks.forget"></a>**Watermark results**: **Delete results** deletes the results, so the next scan checks every file again.
