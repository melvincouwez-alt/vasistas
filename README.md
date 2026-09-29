<p align="center">
  <img src="data/icons/hicolor/128x128/apps/io.github.melvincouwez.Vasistas.svg" width="128" height="128" alt="Vasistas logo">
</p>

<h1 align="center">Vasistas</h1>

<p align="center">Windows applications on the Linux desktop, one window at a time.</p>

<p align="center">
  <img src="docs/screenshot.png" alt="Microsoft Word from the Windows virtual machine next to the Vasistas companion app, on an elementary OS wallpaper">
  <br>
  <sub>Word, running in the Windows virtual machine, next to the Vasistas companion app.</sub>
</p>

Vasistas runs Windows in a virtual machine and shows its applications on your Linux desktop as
if they belonged there. Word, Excel or Power BI open from the Applications menu, get their own
icon in the dock, and move, resize and switch like any other window. The Windows desktop itself
stays out of sight: you only see the applications you use.

It is built for elementary OS and should work on other Debian and Ubuntu based systems with
GTK 4 and Granite. Version 0.5 is an early, experimental release.

## Not a remote desktop

Vasistas is not a remote desktop client. Windows runs on your own computer, so we chose not to
treat it as a distant machine: there is no remote session, no video stream and no network
protocol between the two systems. What we aim for is a controlled virtualization, kept as light
as possible and smooth enough for the professional tools people rely on every day.

Controlled, because each piece is chosen and kept in hand: the virtual machine is started
directly with QEMU, the screen, keyboard, mouse and clipboard go through our own channel, and a
small agent inside Windows takes care of the windows. Windows itself is installed from
Microsoft's media with settings made for this use, such as no lock screen and updates only when
you decide.

Light, because nothing is encoded or decoded: the screen is read from memory shared with the
virtual machine. Windows pauses when you are not using it, memory it does not need goes back to
Linux as it goes, and an optional step removes telemetry and services that serve no purpose
here. On our machine, with Outlook open, the virtual machine uses about 4 GB of the 8 GB it is
given.

Smooth enough for work: typing, scrolling and moving between Word, Excel, Outlook or Power BI
stay fluid, and in our measurements a key press shows on screen within roughly 25 to 40
milliseconds. It is not tuned for 3D or games, as explained below.

## The experience we are aiming for

- Each Windows application window is a real window of your desktop, with its own entry in the
  dock and in the window switcher. Menus, dialogs and tooltips appear where you expect them.
- Your Documents and Downloads folders show up as drives in Windows, and Windows' own
  Documents, Pictures or Downloads folders can point to them. Double-clicking a `.docx`, `.xlsx`
  or `.pbix` file in Files opens it in the matching Windows application.
- Text, formatted text and images copy and paste between both sides.
- Windows pauses itself when you are not using it and resumes on the next click; memory it does
  not need goes back to Linux.
- A setup assistant downloads Windows from Microsoft in the language you pick, installs it
  unattended with a local account, then installs Microsoft Office and other common
  applications.
- A companion app starts or stops Windows, chooses which applications appear in the menu,
  decides which file types open in Windows, trims Windows down and checks for updates.

## How it works

Windows runs in a QEMU/KVM virtual machine on your computer. Instead of streaming a remote
desktop, Vasistas reads the Windows screen directly from QEMU's shared memory (D-Bus display),
so nothing is encoded or sent over a network. A small agent inside Windows reports where each
window is and receives mouse, keyboard and clipboard events over a virtio channel. On the Linux
side, each Windows window becomes a GTK 4 window showing its part of the screen, labelled with
the application's own identity so the desktop can group and decorate it properly. Folders are
shared with virtio-fs.

The technical details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and the host/agent
protocol in [PROTOCOL.md](PROTOCOL.md).

## What it is not

Vasistas is meant for desktop applications, not for games. Without a graphics card passed to
the virtual machine, Windows renders in software: office and business applications stay
responsive, but 3D, demanding video and games do not, and anti-cheat systems usually refuse
virtual machines. Passing a dedicated graphics card to Windows is possible but experimental.

There is no sound, webcam or USB passthrough yet, and the interface is currently in French only.

## Requirements

- elementary OS 8 or 9, or another Debian or Ubuntu based system (Ubuntu 24.04 or later) with
  GTK 4 and Granite 7; only the Pantheon desktop is tested so far
- a processor with hardware virtualization enabled (KVM), 16 GB of memory recommended, and
  about 100 GB of free disk space
- a Windows license, or a 90-day evaluation version that the assistant can download; licenses
  or subscriptions for Office and any other paid software

## Installation

Download `vasistas-<version>.tar.gz` from the latest release, then:

```
tar xf vasistas-0.5.0.tar.gz
cd vasistas-0.5.0
./install.sh
```

The script installs Vasistas in your home folder without administrator rights. If system
packages are missing, it prints the `sudo apt install …` command to run. Then open "Vasistas"
from the Applications menu and follow the assistant. Updates are offered from the app itself.

To prepare a Windows installation made by other means, see
[docs/configure-windows.md](docs/configure-windows.md).

## Command line

The `vasistas` command (in `~/.local/bin`) also works from a terminal:

```
vasistas vm start|stop|status          # the virtual machine
vasistas launch-app winword            # a known application
vasistas open ~/Documents/report.docx  # a file, in its Windows application
vasistas files list|set csv excel      # file types opened in Windows
vasistas folders list|link|unlink      # Windows folders pointing to Linux folders
vasistas exec 'Get-Process'            # a PowerShell script inside Windows
vasistas companion                     # the companion app
```

## Development

The host is written in Python with GTK 4 and Granite (`host/vasistas`), the Windows agent in
C# for .NET Framework 4.8 (`guest/Vasistas.Agent`, built with `dotnet build -c Release`).
`./check.sh` runs the tests and builds the agent; `tools/make-release.sh` packages a release.

## License

Vasistas is released under the MIT License (see [LICENSE](LICENSE)).

Windows, Office, Power BI and the other software mentioned belong to their publishers. Vasistas
does not ship any Microsoft software or license; it downloads the official installers.
