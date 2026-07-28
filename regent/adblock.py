# ---------------------------------------------------------------------------- #
# DESCRIPTION: adblock domain - DNS blocklists sized to what the router can hold
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import re
import asyncio
from typing_extensions import TypedDict

# custom
from regent.guard import READ, WRITE, checkTier, annotationsFor
from regent.errors import toolSafe
from regent.shell import requireSafePath
from regent.metadata import SLUG
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class AdblockStatus(TypedDict):
  installed: bool
  enabled: bool
  status: str | None
  blockedDomains: int
  activeSources: list[str]
  dnsBackend: str | None
  lastRun: str | None

class SourceInfo(TypedDict):
  name: str
  size: str

class ApplyAdblockReply(TypedDict):
  preset: str | None
  sources: list[str]
  estimatedWeight: int
  budget: int
  applied: bool
  status: str | None
  blockedDomains: int
  failedSources: list[str]
  warnings: list[str]
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
SOURCES_ARCHIVE = "/etc/adblock/adblock.sources.gz"

# relative sizes of adblock's list tags, used to compare a chosen set against free memory
SIZE_WEIGHTS = {"S": 1, "M": 3, "L": 8, "XL": 20, "XXL": 55, "VAR": 8}

# measured on a real router: about 100 bytes per domain, 70 of them inside dnsmasq
BYTES_PER_DOMAIN_DNSMASQ = 70
BYTES_PER_DOMAIN_FILE = 30

# domains per megabyte. in tmpfs the download and the sort also take memory
WEIGHT_PER_MB_TMPFS = 1.6
WEIGHT_PER_MB_DISK = 3.8

# adblock's working directory, and where it leaves the file dnsmasq reads
TMPBASE_OPTION = "adblock.global.adb_tmpbase"
BACKUPDIR_OPTION = "adblock.global.adb_backupdir"

# curated list sets. there is no "everything": the largest lists exhaust a small router
PRESETS = {
  "minimal": (
    "adaway", "yoyo", "disconnect",
  ),

  "balanced": (
    "adaway", "adguard", "adguard_tracking", "disconnect", "easylist", "easyprivacy",
    "yoyo", "android_tracking", "smarttv_tracking", "firetv_tracking", "games_tracking",
    "winspy", "reg_ru",
  ),

  "aggressive": (
    "adaway", "adguard", "adguard_tracking", "anti_ad", "disconnect", "easylist",
    "easyprivacy", "yoyo", "android_tracking", "smarttv_tracking", "firetv_tracking",
    "games_tracking", "winspy", "winhelp", "reg_ru", "oisd_small", "notracking",
    "phishing_army", "spam404",
  )
}

STATUS_FIELDS = {
  "adblock_status": "status",
  "blocked_domains": "blockedDomains",
  "active_sources": "activeSources",
  "dns_backend": "dnsBackend",
  "last_run": "lastRun"
}

STATUS_LINE = re.compile(r"^\s*\+\s*(\w+)\s*:\s*(.+?)\s*$", re.MULTILINE)
SOURCE_ENTRY = re.compile(r'"([a-z0-9_]+)":\s*\{')

# adblock logs a line per list it could not fetch, carrying the curl diagnosis at the end
DOWNLOAD_FAILURE = re.compile(r"download of '([a-z0-9_]+)' failed.*?log: (.+?)\s*$", re.MULTILINE)

class AdblockError(Exception):
  pass

def parseStatus(output):
  found = {}

  for name, value in STATUS_LINE.findall(output):
    key = STATUS_FIELDS.get(name)

    if not key:
      continue

    if key == "blockedDomains":
      digits = re.sub(r"[^\d]", "", value)
      found[key] = int(digits) if digits else 0
    elif key == "activeSources":
      found[key] = [item for item in re.split(r"[\s,]+", value) if item and item != "-"]
    else:
      found[key] = None if value in ("-", "") else value

  return found

def parseSources(jsonText):
  # only the top-level list names and their size tags; the urls are of no use here
  sources = []
  currentName = None

  for line in jsonText.splitlines():
    match = SOURCE_ENTRY.search(line)

    if match and line.startswith(("\t\"", '  "')):
      currentName = match.group(1)
      continue

    if currentName and '"size"' in line:
      size = line.split(":", 1)[1].strip().strip('",')
      sources.append({"name": currentName, "size": size})
      currentName = None

  return sources

def estimateWeight(names, sizeByName):
  return sum(SIZE_WEIGHTS.get(sizeByName.get(name, "M"), 3) for name in names)

def memoryBudget(availableBytes, workingOnDisk = False):
  weightPerMb = WEIGHT_PER_MB_DISK if workingOnDisk else WEIGHT_PER_MB_TMPFS

  return int((availableBytes / (1024 * 1024)) * weightPerMb)

def estimateDomainCeiling(availableBytes, workingOnDisk = False):
  # how many domains fit in the memory available
  perDomain = BYTES_PER_DOMAIN_DNSMASQ if workingOnDisk else BYTES_PER_DOMAIN_DNSMASQ + BYTES_PER_DOMAIN_FILE

  return int(availableBytes / perDomain)

def resolveSources(preset, sources):
  if sources:
    return list(sources), None

  if preset not in PRESETS:
    raise AdblockError(f"unknown preset '{preset}' - choose one of {', '.join(PRESETS)}, or pass sources explicitly")

  return list(PRESETS[preset]), preset

# reload returns as soon as the job starts, so the status right after it is still the old one
RUN_POLL_SECONDS = 10
RUN_MAX_WAIT_SECONDS = 240

def parseDownloadFailures(logOutput):
  # keep only the most recent failure for each source
  failures = {}

  for name, reason in DOWNLOAD_FAILURE.findall(logOutput):
    failures[name] = reason.strip()

  return failures

async def readDownloadFailures(session, sinceLines = 60):
  result = await session.run(f"logread -e adblock | tail -{int(sinceLines)}")

  return parseDownloadFailures(result.stdout)

PID_FILE = "/var/run/adblock.pid"
RUNTIME_FILE_DEFAULT = "/tmp/adb_runtime.json"

async def clearStaleLock(session):
  # a killed run leaves a pid file behind, and adblock then refuses to start while reporting success
  pid = (await session.run(f"cat {PID_FILE} 2>/dev/null")).stdout.strip()

  if not pid.isdigit():
    return {"cleared": False, "pid": None}

  alive = (await session.run(f"test -d /proc/{pid} && echo yes || echo no")).stdout.strip() == "yes"

  if alive:
    return {"cleared": False, "pid": int(pid)}

  await session.run(f"rm -f {PID_FILE}")

  # the runtime file records the status, so a killed run keeps reporting "running"
  runtime = (await session.run(f"uci get adblock.global.adb_rtfile 2>/dev/null")).stdout.strip() or RUNTIME_FILE_DEFAULT

  await session.run(f"rm -f {runtime}")

  return {"cleared": True, "pid": int(pid)}

async def readLastRun(session):
  return parseStatus((await session.run("/etc/init.d/adblock status 2>&1")).stdout).get("lastRun")

async def waitForRun(session, previousRun = None, maxWaitSeconds = None, pollSeconds = None):
  # resolved here, not in the signature, so tests can change the module constants
  maxWaitSeconds = RUN_MAX_WAIT_SECONDS if maxWaitSeconds is None else maxWaitSeconds
  pollSeconds = RUN_POLL_SECONDS if pollSeconds is None else pollSeconds

  # an idle status is not enough: only a changed run stamp means the new run has finished
  #
  # counted, not timed: a zero-length poll would never advance an elapsed-time bound
  attempts = max(1, int(maxWaitSeconds / pollSeconds) if pollSeconds else 1)

  for attempt in range(attempts):
    parsed = parseStatus((await session.run("/etc/init.d/adblock status 2>&1")).stdout)
    status = parsed.get("status")
    lastRun = parsed.get("lastRun")
    settled = status != "running"
    moved = previousRun is None or lastRun != previousRun

    if settled and moved:
      return status

    if attempt + 1 < attempts:
      await asyncio.sleep(pollSeconds)

  return "running"

async def readWorkingStorage(session):
  # /tmp is RAM. moving the working files to disk removes the memory spike during a rebuild
  current = (await session.run(f"uci get {TMPBASE_OPTION} 2>/dev/null")).stdout.strip() or "/tmp"
  fsType = (await session.run(f"df -T {current} 2>/dev/null | tail -1 | awk '{{print $2}}'")).stdout.strip()

  if not fsType:
    fsType = (await session.run(f"stat -f -c %T {current} 2>/dev/null")).stdout.strip()

  freeOutput = (await session.run(f"df -k {current} 2>/dev/null | tail -1 | awk '{{print $4}}'")).stdout.strip()
  freeBytes = int(freeOutput) * 1024 if freeOutput.isdigit() else 0

  return {
    "path": current,
    "filesystem": fsType or "unknown",
    "onDisk": bool(fsType) and fsType not in ("tmpfs", "ramfs"),
    "freeBytes": freeBytes
  }

RAM_FILESYSTEMS = ("tmpfs", "ramfs", "devtmpfs")
READ_ONLY_FILESYSTEMS = ("squashfs", "iso9660")

# these are views of storage listed elsewhere, so counting them would report it twice
VIRTUAL_FILESYSTEMS = ("overlay", "overlayfs", "aufs", "unionfs")

# below this there is no point moving anything
MIN_USABLE_BYTES = 256 * 1024 * 1024

def parseMounts(dfOutput, minBytes = MIN_USABLE_BYTES):
  # parsed in python rather than filtered on the router, so it can be tested
  candidates = []

  for line in dfOutput.splitlines()[1:]:
    parts = line.split()

    if len(parts) < 7:
      continue

    filesystem, mount = parts[1], parts[-1]

    if not mount.startswith("/"):
      continue

    if filesystem in RAM_FILESYSTEMS + READ_ONLY_FILESYSTEMS + VIRTUAL_FILESYSTEMS:
      continue

    try:
      freeBytes = int(parts[4]) * 1024
    except ValueError:
      continue

    if freeBytes >= minBytes:
      candidates.append({"mount": mount, "filesystem": filesystem, "freeBytes": freeBytes})

  return sorted(candidates, key = lambda entry: entry["freeBytes"], reverse = True)

async def findPersistentStorage(session):
  # pick storage that has room, survives a reboot, and is not RAM
  return parseMounts((await session.run("df -Tk")).stdout)

async def moveWorkingStorage(session, settings, path):
  checkTier(WRITE, settings)

  # the caller supplies this path, so it is checked before it reaches the shell
  path = requireSafePath(path)

  await session.run(f"mkdir -p {path}/adblock {path}/adblock-backup")

  probe = await session.run(f"touch {path}/adblock/.{SLUG}-write-test && rm -f {path}/adblock/.{SLUG}-write-test && echo ok")

  if probe.stdout.strip() != "ok":
    raise AdblockError(f"cannot write to {path} - is it mounted read-only?")

  await session.run(f"uci set {TMPBASE_OPTION}='{path}/adblock'")
  await session.run(f"uci set {BACKUPDIR_OPTION}='{path}/adblock-backup'")
  await session.run("uci commit adblock")

  return await readWorkingStorage(session)

async def readAvailableMemory(session):
  # MemAvailable counts reclaimable cache, so it reflects what can really be allocated
  result = await session.run("grep MemAvailable /proc/meminfo")
  match = re.search(r"(\d+)", result.stdout)

  return int(match.group(1)) * 1024 if match else 0

async def getStatus(session, settings):
  checkTier(READ, settings)

  installed = (await session.run("test -x /etc/init.d/adblock && echo yes || echo no")).stdout.strip() == "yes"

  if not installed:
    return {
      "installed": False, "enabled": False, "status": None, "blockedDomains": 0,
      "activeSources": [], "dnsBackend": None, "lastRun": None
    }

  enabled = (await session.run("uci get adblock.global.adb_enabled 2>/dev/null")).stdout.strip() == "1"
  parsed = parseStatus((await session.run("/etc/init.d/adblock status 2>&1")).stdout)

  return {
    "installed": True,
    "enabled": enabled,
    "status": parsed.get("status"),
    "blockedDomains": parsed.get("blockedDomains", 0),
    "activeSources": parsed.get("activeSources", []),
    "dnsBackend": parsed.get("dnsBackend"),
    "lastRun": parsed.get("lastRun")
  }

async def listSources(session, settings):
  checkTier(READ, settings)

  result = await session.run(f"zcat {SOURCES_ARCHIVE} 2>/dev/null")

  if not result.stdout:
    raise AdblockError(f"cannot read {SOURCES_ARCHIVE} - is the adblock package installed?")

  return {"sources": parseSources(result.stdout), "presets": {name: list(names) for name, names in PRESETS.items()}}

async def configureAdblock(session, settings, preset = "balanced", sources = None, force = False):
  checkTier(WRITE, settings)

  chosen, presetName = resolveSources(preset, sources)
  warnings = []

  catalogue = await listSources(session, settings)
  sizeByName = {entry["name"]: entry["size"] for entry in catalogue["sources"]}

  unknown = [name for name in chosen if name not in sizeByName]

  if unknown:
    raise AdblockError(f"this adblock build has no source called {', '.join(unknown)}")

  weight = estimateWeight(chosen, sizeByName)
  storage = await readWorkingStorage(session)
  budget = memoryBudget(await readAvailableMemory(session), workingOnDisk = storage["onDisk"])

  if weight > budget:
    message = (
      f"these {len(chosen)} lists score {weight} against a budget of {budget} for the free memory "
      "on this router - loading them risks the OOM killer taking dnsmasq, which would leave "
      "the network without DNS at all"
    )

    if not storage["onDisk"]:
      persistent = await findPersistentStorage(session)

      if persistent:
        message += (
          f". adblock is working in {storage['path']} ({storage['filesystem']}), which is RAM - "
          f"moving it onto {persistent[0]['mount']} would raise this budget"
        )

    if not force:
      raise AdblockError(message + ". Choose a smaller preset, or pass force=True if you accept the risk")

    warnings.append(message + " - applied anyway because force was set")

  await session.run(f"uci set adblock.global.adb_sources='{' '.join(chosen)}'")
  await session.run("uci set adblock.global.adb_enabled='1'")
  await session.run("uci commit adblock")

  # a lock left by a run that died blocks every later start, silently
  stale = await clearStaleLock(session)

  if stale["cleared"]:
    warnings.append(f"cleared a lock left behind by a dead run (pid {stale['pid']}) - adblock would otherwise refuse to start")

  # remember the current run stamp so the wait can tell the next run apart from it
  previousRun = await readLastRun(session)

  # a reload re-downloads and rebuilds; without it the config changes and nothing else
  await session.run("/etc/init.d/adblock reload")

  finalStatus = await waitForRun(session, previousRun = previousRun)

  if finalStatus == "running":
    warnings.append(
      f"still rebuilding after {RUN_MAX_WAIT_SECONDS}s - downloading and sorting these lists is slow "
      "on this hardware. Call routerAdblockStatus in a few minutes for the outcome"
    )

  status = await getStatus(session, settings)

  if status["status"] == "error":
    warnings.append("adblock finished with an error - check routerLog for what failed to download")

  failures = {name: reason for name, reason in (await readDownloadFailures(session)).items() if name in chosen}

  if failures:
    warnings.append(
      f"{len(failures)} of the {len(chosen)} lists did not download and are contributing nothing: "
      + "; ".join(f"{name} ({reason})" for name, reason in sorted(failures.items()))
    )

  return {
    "preset": presetName,
    "sources": chosen,
    "estimatedWeight": weight,
    "budget": budget,
    "applied": True,
    "status": status["status"],
    "blockedDomains": status["blockedDomains"],
    "failedSources": sorted(failures),
    "warnings": warnings
  }

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "Ad blocking status"))
  @toolSafe
  async def routerAdblockStatus() -> AdblockStatus:
    """Whether DNS ad blocking is running, how many domains it blocks and which lists feed it"""
    return await getStatus(session, settings)

  @mcp.tool(annotations = annotationsFor(READ, "Ad blocking storage"))
  @toolSafe
  async def routerAdblockStorage() -> dict:
    """Where adblock does its downloading and sorting, and whether that is RAM or real
    storage. Working in tmpfs means every downloaded list competes with dnsmasq for the
    same memory, which is what limits how much can be blocked"""
    return {
      "current": await readWorkingStorage(session),
      "candidates": await findPersistentStorage(session)
    }

  @mcp.tool(annotations = annotationsFor(WRITE, "Move ad blocking to disk"))
  @toolSafe
  async def routerAdblockUseStorage(path: str) -> dict:
    """Point adblock's working and backup directories at persistent storage instead of
    RAM. Does not change which lists are active - run routerAdblockConfigure afterwards
    to take advantage of the extra headroom"""
    return await moveWorkingStorage(session, settings, path)

  @mcp.tool(annotations = annotationsFor(READ, "Available blocklists"))
  @toolSafe
  async def routerAdblockSources() -> dict:
    """Every blocklist this adblock build knows, with its size tag, plus the curated presets"""
    return await listSources(session, settings)

  @mcp.tool(annotations = annotationsFor(WRITE, "Configure ad blocking"))
  @toolSafe
  async def routerAdblockConfigure(
    preset: str = "balanced",
    sources: list[str] | None = None,
    force: bool = False
  ) -> ApplyAdblockReply:
    """Enable DNS ad and tracker blocking with a chosen set of lists.

    Presets are minimal, balanced and aggressive. Pass `sources` to name lists directly.
    The chosen set is weighed against the router's free memory first and refused if it
    would not fit, since an out-of-memory dnsmasq leaves the network with no DNS at all.
    Pass force=True to override that check"""
    return await configureAdblock(session, settings, preset = preset, sources = sources, force = force)
# ---------------------------------------------------------------------------- #