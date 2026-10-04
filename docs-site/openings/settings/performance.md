Performance is where you see what your computer is, how much Sift does on it at the same time, and how Sift is keeping up. To open it, go to [Settings > Performance](/settings/performance).

Come here to benchmark your computer, to download GPU support, or to change how much Sift does at the same time.

![The Performance pane in Settings](../../../assets/screens/settings-performance.jpg)

## On this pane

The pane draws these groups, in this order:

- **Device running Sift**: its CPU, threads, memory, GPU, driver and video encoders, and what face recognition, Smart Search and converting video run on. **Copy this device's details** copies them.
- <a id="performance.graphics_card"></a>**GPU**: whether Sift can use an NVIDIA GPU. **Download the GPU runtime** downloads GPU support, and **Test the GPU** checks that a real model runs on it.
- **Benchmarking this device**: **Benchmark this device** measures what your computer can do and suggests how many things Sift does at the same time.
- **Is Sift keeping up?**: whether Sift has stopped responding since it started, with the readings behind the answer. **For a bug report** holds the tables to copy into a report.
- <a id="performance.gpu-remove"></a>**GPU support**: **Delete GPU support** sends face recognition, Smart Search and watermark reading back to the CPU and frees the disk space.

## Rows the pane draws by hand

- **Concurrency**: **Edit** opens how many tasks, previews and folder scans run at the same time, and how much of your computer face recognition may use.
- **Restart Sift**: shown after GPU support is downloaded, because Sift loads the GPU runtime only when it starts.

The rows below under **Every setting** are on the pane itself and on the Concurrency page behind **Edit**.
