# REGENT

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](./LICENSE)
[![Latest Release](https://img.shields.io/github/v/release/m-o-z-z-i-x/REGENT?style=for-the-badge)](https://github.com/m-o-z-z-i-x/REGENT/releases)
![Last Commit](https://img.shields.io/github/last-commit/m-o-z-z-i-x/REGENT?v=1&style=for-the-badge)
![Stars](https://img.shields.io/github/stars/m-o-z-z-i-x/REGENT?v=1&style=for-the-badge)

MCP server that lets an AI configure your OpenWrt router. You say what the network should do — "give everyone on the cable and Wi-Fi internet" — and it works out which `uci` and `ubus` commands that takes, in which order, and which service has to be reloaded before any of it takes effect

> [!IMPORTANT]
>
> **Nothing changes by default.** Until `OPENWRT_ENABLE_WRITE=1` is set, every tool that writes is refused and only reading works. Rebooting, flashing, and resetting ask for more than that: each of those calls has to carry its own confirmation. The server opens no network ports — the MCP client starts it and talks to it over stdin and stdout

> [!CAUTION]
>
> **Disclaimer:** This project is provided under the **MIT License** and hands an AI **root access to a live router**.
> A model can misread an instruction, and the safeguards here — the write gate, the confirmations, the rollback timer — **reduce that risk without removing it**.
> A bad change can cut your connection, take the network down, or leave a device that needs a physical reset or a reflash to recover.
> **Take a backup first, keep physical access to the router, and never point this at something you cannot afford to lose.**
> The author **disclaims all liability** for any damage, downtime, or data loss. By using this software you take full responsibility for what it does to your equipment

---

## 🔍 Example

The router this was built against stopped passing traffic, and nothing in the web interface looked wrong. Four separate faults were causing it, none of them visible on the page where it was configured.
**One call to `routerTopology`** names all four and says what each one breaks:

```
UPLINK   wwan via phy1-sta0, 192.168.1.4/24, gateway 192.168.1.1
SERVES   lan  192.168.1.1/24   DHCP OFF
AP       HomeNet  2.4 GHz  →  wwan2

WARNINGS 4
  ! lan (192.168.1.0/24) is on the same subnet as the uplink — the default
    gateway resolves to this router itself and nothing routes out
  ! access point HomeNet is attached to 'wwan2', which is not up —
    clients will associate and get no address
  ! dhcp is not serving lan — clients there must be configured by hand
  ! the uplink sits in zone lan, which does not masquerade — replies to
    clients have nowhere to return to
```

- **Any one of the four is enough to stop traffic** - together they took an afternoon to find by hand, because a router in this state reports itself as healthy
- **`routerShareUplink` repairs all four in one call** - it puts the changes in an order that keeps the router reachable while they are applied, gives a reason for each one, and arms a rollback timer first, in case the repair cuts the connection it is travelling over

---

## 🚀 Key Features

- **🩺 A Diagnosis, Not a Dump** - `routerTopology` reads the whole configuration in one pass and reports what is broken and why that stops traffic, rather than handing back a listing for you to interpret
- **🧠 Whole Recipes, Not Single Commands** - "share the uplink with clients" is six changes across three config files, and the wrong order locks you out partway through. That recipe lives in the server, so the model does not have to reassemble it correctly every time
- **⏱️ Insurance Against Lockout** - You reach the router through the network the router provides, so a bad change can take away the means of undoing it. Before a risky change the config files are copied aside and a restore is scheduled on the device itself. If the router still answers afterwards the restore is canceled; if it does not, the restore runs and the old settings come back
- **🔒 Three Levels of Access** - Reading always works. Changing anything needs the write gate open. Rebooting, flashing, and resetting need the gate *and* a separate confirmation on each individual call
- **🔑 Secrets Stay In** - The router returns Wi-Fi passwords, VPN keys, and subscription links in clear text. They are stripped before anything is sent back to the model, and before anything is written to the command log or read out of the system log
- **📡 Routed Client Mode** - Built for the case where the OpenWrt box has no wire of its own: it joins somebody else's router over Wi-Fi as a client, then serves its own LAN and Wi-Fi behind NAT, adding the VPN and ad-blocking that the upstream cannot provide
- **🧪 Testable Without Hardware** - Command building and output parsing are pure functions, so all 457 tests run against a fake SSH transport. The fixtures they run on are output copied verbatim from a live router, including while it was broken

---

## 🛠️ Using

You never launch the server yourself. The MCP client spawns it, talks to it over stdin and stdout, and shuts it down on exit, so it only has to be registered once. Nothing needs installing first — `uvx` fetches the package on the first run. After that you describe what you want in ordinary words

- **Register with the client** - add this to wherever that client keeps its MCP configuration
  ```json
  {
    "mcpServers": {
      "regent": {
        "command": "uvx",
        "args": ["regent-mcp"]
      }
    }
  }
  ```
- **Point it at the router** - create a `.env` in the configuration directory for your system
  | | |
  |---|---|
  | **Windows** | `%APPDATA%\regent\.env` |
  | **Linux, macOS** | `~/.config/regent/.env` |

  ```ini
  OPENWRT_HOST=192.168.1.1
  ```
  That address is the only required line, since every other setting already holds what a stock OpenWrt answers on
  | Setting | Default |
  |---|---|
  | `OPENWRT_HOST` | **required** — the router's address |
  | `OPENWRT_USER` | `root` |
  | `OPENWRT_PORT` | `22` |
  | `OPENWRT_KEY` | the file named `key` beside this `.env` |
  | `OPENWRT_ENABLE_WRITE` | unset, so nothing can be changed |
  | `OPENWRT_TIMEOUT` | `30` seconds per command |
  | `OPENWRT_ROLLBACK_DELAY` | `90` seconds before an unconfirmed change reverts |
- **Give it a key of its own** - generate it next to that `.env`, never reusing a personal one
  ```bash
  ssh-keygen -t ed25519 -f key -N "" -C "regent"
  ```
- **Authorize the key on the router** - this sends the public half over and appends it in one go. A stock OpenWrt still accepts a password, which is what gets you in the first time
  ```bash
  ssh root@192.168.1.1 "mkdir -p /etc/dropbear; cat >> /etc/dropbear/authorized_keys; chmod 600 /etc/dropbear/authorized_keys" < key.pub
  ```

Settings are read once when the server starts, so **restart the client** after editing `.env`

What is available depends on what you have allowed:

- **Read only** - Works immediately, nothing to configure
- **Change settings** - Add `OPENWRT_ENABLE_WRITE=1` to `.env`
- **Reboot, flash, reset, restore a backup** - Needs explicit confirmation on the call as well

### 🧰 Tools

| Area | Tools |
|---|---|
| **Orientation** | `routerTopology` — what the router does and what is wrong with it |
| **Changes** | `routerApplyUci` — any change under the watchdog, with a dry run |
| **Backups** | `routerBackups`, `routerBackupCreate`, `routerBackupInspect`, `routerBackupRestore` |
| **System** | `routerSystemInfo`, `routerExec`, `routerReboot`, `routerFactoryReset`, `routerFirmwareUpgrade` |
| **Network** | `routerInterfaces`, `routerDhcpLeases`, `routerClients` |
| **Wi-Fi** | `routerWireless`, `routerWirelessClients` |
| **Firewall** | `routerFirewallZones`, `routerFirewallRuleset` |
| **Services** | `routerServices`, `routerServiceControl` |
| **Packages** | `routerPackages`, `routerFindPackage`, `routerInstallPackage`, `routerRemovePackage` |
| **Diagnostics** | `routerLog`, `routerPing`, `routerResolve` |
| **Ads and Trackers** | `routerAdblockStatus`, `routerAdblockSources`, `routerAdblockConfigure`, `routerAdblockStorage`, `routerAdblockUseStorage` |
| **VPN** | `routerVpnStatus`, `routerVpnProbeNode` |
| **Intents** | `routerJoinUpstreamWifi`, `routerPlanShareUplink`, `routerShareUplink` |

These cover what gets asked for most, but they do not have to cover everything. `routerExec` runs an arbitrary command on the router, so a request nobody anticipated still has a way through — it just gives up the guardrails the dedicated tools provide

> [!TIP]
>
> Leave `OPENWRT_ROLLBACK_DELAY` alone unless you know why you are changing it. It is how many seconds the router waits before undoing a change nobody confirmed, 90 by default, and it is your safety net for when the AI reconfigures the very interface you are connected through

> [!WARNING]
>
> **Confirmed on one device so far** — a TP-Link Archer C59 running OpenWrt 23.05.4. Every parser was checked against what that router prints, including while it was broken. That proves they match *its* output, not that the output is universal. Package handling will not work on OpenWrt 24.10, where `opkg` became `apk`

---

## 🔨 Develop

### 🔧 Requirements

- Python 3.10+
- Essential packages
  ```bash
  fastmcp asyncssh python-dotenv typing-extensions pytest pytest-asyncio
  ```

### ⚡️ Quick Setup Guide

- **Clone & Open Project**
  ```bash
  git clone https://github.com/m-o-z-z-i-x/REGENT.git
  cd REGENT
  code .
  ```
- **Set Up Virtual Environment**
  - In VS Code
    - Open terminal (`Ctrl+~`)
    - Run
      ```bash
      python -m venv .venv
      ```
  - **Restart the terminal** so `.venv` activates itself
- **Install Dependencies**
  ```bash
  pip install -e ".[dev]"
  ```
- **Generate an SSH Key** *(one per router — never reuse a personal key)*
  ```bash
  ssh-keygen -t ed25519 -f keys/key -N "" -C "regent"
  ```
- **Run the Tests** *(no router needed)*
  ```bash
  pytest -q
  ```
- **Try It Against a Real Router** - copy `.env.example` to `.env`, fill in the address, then point a client at the checkout instead of at `uvx`, so every edit is live without rebuilding
  ```json
  {
    "mcpServers": {
      "regent": {
        "command": "<project>/.venv/Scripts/python.exe",
        "args": ["-m", "regent.server"]
      }
    }
  }
  ```

A checkout keeps `.env`, `keys/` and `logs/` beside the code and prefers them over the configuration directory, so working on the project never reads or writes an installed copy's settings

> [!TIP]
>
> Settings are read once at startup, so the MCP client has to be restarted after editing `.env` or the code. `.env` carries everything needed to reach the router — address, user, port, key. The two tunables that do not vary between routers, the command timeout and the rollback delay, sit in `regent/config.py`, where an environment variable can still override either of them

---

## 🌟 Future Roadmap

**Done**
- [x] **Core over SSH** - One reused session, with command building and parsing kept apart from it
- [x] **Risk Tiers** - Read, write, and destructive, each with its own explicit permission
- [x] **Watchdog Rollback** - Survives the SSH session dying, because that is what it insures against
- [x] **Reading the State** - Network, Wi-Fi, firewall, packages, diagnostics
- [x] **Topology Analysis** - With the common faults named, rather than a plain dump
- [x] **Safe Arbitrary Changes** - Snapshot, rollback, and a reload of whichever service owns the config
- [x] **Ad Blocking** - Weighed against the router's free memory before it is applied
- [x] **VPN** - PassWall status and the four faults that make it look like it is working
- [x] **Composite Intents** - Joining an upstream network and sharing it with clients
- [x] **Backups** - Create, inspect, and restore the configuration

**Planned improvements**
- [ ] **apk Support** - Package handling on OpenWrt 24.10 and newer
- [ ] **Other Devices** - Only the Archer C59 is confirmed so far
- [ ] **Guest Network** - A separate isolated Wi-Fi in one call

*Suggestions?* [Open an Issue](https://github.com/m-o-z-z-i-x/REGENT/issues/new) to discuss new features!

---

## 🤝 Contribution

Contributions are welcome! Here's how to help improve the project

- **Fork the repository**
- **Create a feature branch**
  ```bash
  git checkout -b feature/your-feature-name
  ```
- **Commit your changes**
  ```bash
  git commit -m "Add: your feature description"
  ```
- **Push to your fork**
  ```bash
  git push origin feature/your-feature-name
  ```
- **Open a Pull Request** to the main branch of this repository

---

## 🔗 Acknowledgments

This project would not be possible without these amazing open-source contributions

- **[OpenWrt](https://openwrt.org/)** - the firmware, and the UCI/ubus interfaces this server drives
- **[FastMCP](https://gofastmcp.com/)** by Prefect — the MCP server framework
- **[AsyncSSH](https://github.com/ronf/asyncssh)** by Ron Frederick — pure-Python asyncio SSH

---

## 📈 Repo Activity

<img src="https://repobeats.axiom.co/api/embed/55b1c923ae689f1fe2c1fbc1e4988d6d57515356.svg" width="100%" alt="Repobeats analytics image">

---

## 🙏 Support

> ⭐ **Love this project? Give it a star!**

If you find this tool helpful and want to support its development — consider buying me a coffee!

<p align="left">
  <a href="https://yoomoney.ru/to/4100118628464111" target="_blank">
    <img src="https://raw.githubusercontent.com/m-o-z-z-i-x/m-o-z-z-i-x/main/res/logos/yoomoney.jpg" width="150px" alt="YooMoney">
  </a>
  <a href="https://boosty.to/m-o-z-z-i-x/donate" target="_blank">
    <img src="https://raw.githubusercontent.com/m-o-z-z-i-x/m-o-z-z-i-x/main/res/logos/boosty.png" width="150px" alt="Boosty">
  </a>
  <br><br>
  <b>TON:</b>
  <blockquote>UQBZVRZFeZI4CepVq_OF5_KiQ_oo62SzmWlGvppfGuyRqUSE</blockquote>

  <b>Bitcoin:</b>
  <blockquote>bc1qunr4lkes5xdanln8j5l0gm6e7x0kfw2e6z4yve</blockquote>

  <b>Monero:</b>
  <blockquote>89vPf9GUBdFXmpEhFiBSiQMQbAeWZYCGDZBfr6e45zpNVUQ8cMnYFc8ct5FH3TJvftSbKTgkHzkiPB9QoYKhNhBdLeWvesC</blockquote>

  <b>USDT (TRC20):</b>
  <blockquote>TCKL4YBLAFEHFesUuGBiB85aywNE38zVSQ</blockquote>
</p>

---

## 📬 Contacts

All my contact links are available [here](https://github.com/m-o-z-z-i-x/m-o-z-z-i-x?tab=readme-ov-file#-contacts)

---

## 📝 License

This project is licensed under the [MIT License](./LICENSE)