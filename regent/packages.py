# ---------------------------------------------------------------------------- #
# DESCRIPTION: packages domain - what opkg has installed and what it could install
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from typing_extensions import TypedDict

from regent.guard import READ, WRITE, checkTier, annotationsFor
from regent.errors import toolSafe
from regent.shell import requireSafeName
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class Package(TypedDict):
  name: str
  version: str
  description: str | None

class FoundPackage(TypedDict):
  name: str
  version: str
  description: str | None
  installed: bool

class PackagesReply(TypedDict):
  packages: list[Package]
  count: int

class FindReply(TypedDict):
  term: str
  packages: list[FoundPackage]
  count: int
  truncated: bool
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
SEARCH_LIMIT = 50

# below this, installing anything risks filling the root filesystem
MIN_FREE_BYTES = 2 * 1024 * 1024

# removing any of these takes away the way back in, or the machinery that puts it back
PROTECTED_PACKAGES = {
  "dropbear": "this is the ssh server - removing it ends this session and prevents the next",
  "opkg": "this is the package manager; removing it means nothing can be installed again",
  "apk": "this is the package manager; removing it means nothing can be installed again",
  "netifd": "this manages every interface, including the one this connection uses",
  "firewall4": "removing it leaves the router with no packet filter at all",
  "dnsmasq": "this serves DHCP and DNS to every client",
  "uhttpd": "this is the web interface, the fallback way in when ssh is gone",
  "busybox": "this is nearly every command on the router",
}

class PackageError(Exception):
  pass

def parsePackageList(output):
  # opkg prints "<name> - <version>" and sometimes " - <description>" after that
  packages = []

  for line in output.splitlines():
    parts = line.split(" - ", 2)

    if len(parts) < 2 or not parts[0].strip():
      continue

    packages.append({
      "name": parts[0].strip(),
      "version": parts[1].strip(),
      "description": parts[2].strip() if len(parts) > 2 else None
    })

  return packages

def quoteTerm(term):
  return str(term).replace("'", "'\\''")

async def getInstalledPackages(session, settings):
  checkTier(READ, settings)

  result = await session.run("opkg list-installed")
  packages = parsePackageList(result.stdout)

  return {"packages": packages, "count": len(packages)}

async def findPackage(session, settings, term):
  # without fetched lists opkg returns nothing rather than an error, which reads as "not found"
  checkTier(READ, settings)

  result = await session.run(f"opkg list | grep -i '{quoteTerm(term)}' | head -{SEARCH_LIMIT}")
  packages = parsePackageList(result.stdout)

  installed = await session.run(f"opkg list-installed | grep -i '{quoteTerm(term)}'")
  installedNames = {package["name"] for package in parsePackageList(installed.stdout)}

  for package in packages:
    package["installed"] = package["name"] in installedNames

  return {
    "term": term,
    "packages": packages,
    "count": len(packages),
    "truncated": len(packages) >= SEARCH_LIMIT
  }

async def detectManager(session):
  # openwrt 24.10 replaced opkg with apk; asking beats assuming
  return (await session.run("command -v apk >/dev/null && echo apk || echo opkg")).stdout.strip() or "opkg"

async def readAvailableSpace(session):
  result = await session.run("df -k /overlay 2>/dev/null | tail -1 | awk '{print $4}'")
  free = result.stdout.strip()

  return int(free) * 1024 if free.isdigit() else 0

async def installPackage(session, settings, name, confirm = False):
  checkTier(WRITE, settings)

  safeName = requireSafeName(name, "package name")
  manager = await detectManager(session)

  # a full filesystem makes services fail in ways that look unrelated
  free = await readAvailableSpace(session)

  if free and free < MIN_FREE_BYTES and not confirm:
    raise PackageError(
      f"only {free // 1024 // 1024} MB free on /overlay - installing more risks filling the "
      "root filesystem, which breaks services in ways that look unrelated. "
      "Pass confirm=True to proceed anyway"
    )

  command = (f"apk add {safeName} 2>&1" if manager == "apk" else f"opkg install {safeName} 2>&1")
  result = await session.run(command)

  installed = await isInstalled(session, manager, safeName)

  return {
    "package": safeName,
    "manager": manager,
    "installed": installed,
    "detail": result.stdout.strip()[:400] or "no output"
  }

async def removePackage(session, settings, name, confirm = False):
  # opkg will remove a package other things depend on, taking a service's binary with it
  checkTier(WRITE, settings)

  safeName = requireSafeName(name, "package name")

  if safeName in PROTECTED_PACKAGES and not confirm:
    raise PackageError(
      f"'{safeName}' - {PROTECTED_PACKAGES[safeName]}. Pass confirm=True if that is what you mean"
    )

  manager = await detectManager(session)
  command = (f"apk del {safeName} 2>&1" if manager == "apk" else f"opkg remove {safeName} 2>&1")
  result = await session.run(command)

  return {
    "package": safeName,
    "manager": manager,
    "installed": await isInstalled(session, manager, safeName),
    "detail": result.stdout.strip()[:400] or "no output"
  }

async def isInstalled(session, manager, name):
  if manager == "apk":
    command = f"apk info -e {name} 2>/dev/null | grep -q . && echo yes || echo no"
  else:
    command = f"opkg list-installed 2>/dev/null | grep -q '^{name} ' && echo yes || echo no"

  return (await session.run(command)).stdout.strip() == "yes"

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "Installed packages"))
  @toolSafe
  async def routerPackages() -> PackagesReply:
    """Every package currently installed on the router, with versions"""
    return await getInstalledPackages(session, settings)

  @mcp.tool(annotations = annotationsFor(READ, "Search packages"))
  @toolSafe
  async def routerFindPackage(term: str) -> FindReply:
    """Search the opkg index for packages matching a term, flagging which are installed"""
    return await findPackage(session, settings, term)

  @mcp.tool(annotations = annotationsFor(WRITE, "Install a package"))
  @toolSafe
  async def routerInstallPackage(name: str, confirm: bool = False) -> dict:
    """Install a package. Uses apk or opkg, whichever this OpenWrt has.

    Refuses when /overlay is nearly full unless confirm=True, because a full root
    filesystem breaks services in ways that look unrelated to the install"""
    return await installPackage(session, settings, name, confirm = confirm)

  @mcp.tool(annotations = annotationsFor(WRITE, "Remove a package"))
  @toolSafe
  async def routerRemovePackage(name: str, confirm: bool = False) -> dict:
    """Remove a package. Packages that carry the way back in - the ssh server, the
    package manager, the interface daemon - need confirm=True"""
    return await removePackage(session, settings, name, confirm = confirm)
# ---------------------------------------------------------------------------- #