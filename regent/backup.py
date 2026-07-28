# ---------------------------------------------------------------------------- #
# DESCRIPTION: backup domain - snapshot the configuration, and put it back
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
import re
from typing_extensions import TypedDict

# custom
from regent.guard import READ, WRITE, DESTRUCTIVE, checkTier, annotationsFor
from regent.errors import toolSafe
from regent.shell import requireSafeName, requireSafePath
from regent.metadata import SLUG
# ---------------------------------------------------------------------------- #

# TYPES ---------------------------------------------------------------------- #
class Backup(TypedDict):
  name: str
  path: str
  bytes: int
  createdAt: str | None

class BackupReply(TypedDict):
  backup: Backup
  location: str
  detail: str

class BackupList(TypedDict):
  directory: str
  onPersistentStorage: bool
  backups: list[Backup]

class RestoreReply(TypedDict):
  restored: str
  changedConfigs: list[str]
  rebooting: bool
  detail: str
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
# the same archive format LuCI produces, so a backup can be restored without this server
BACKUP_COMMAND = "sysupgrade -b"

# a name that ends up in a filename. dates and words only
SAFE_LABEL = re.compile(r"^[A-Za-z0-9_-]{1,48}$")

FALLBACK_DIRECTORY = "/tmp"

LISTING = re.compile(r"^(\S+)\s+(\d+)\s+(.+)$")

class BackupError(Exception):
  pass

def requireLabel(label):
  text = str(label or "").strip()

  if not SAFE_LABEL.match(text):
    raise BackupError(f"'{text[:40]}' is not a usable name - letters, digits, dash and underscore only")

  return text

def backupName(label, stamp):
  return f"{SLUG}-{label}-{stamp}.tar.gz"

def parseListing(output, directory):
  # "<name> <bytes> <iso date>" produced by the find below, one per line
  backups = []

  for line in output.splitlines():
    match = LISTING.match(line.strip())

    if not match:
      continue

    name, size, created = match.groups()

    backups.append({
      "name": name,
      "path": f"{directory}/{name}",
      "bytes": int(size),
      "createdAt": created.strip() or None
    })

  return sorted(backups, key = lambda entry: entry["name"], reverse = True)

def parseChangedConfigs(output):
  # `tar -tzf` lists the archive; only the config files matter to a reader
  configs = []

  for line in output.splitlines():
    line = line.strip()

    if line.startswith("etc/config/") and not line.endswith("/"):
      configs.append(line.split("/")[-1])

  return sorted(set(configs))

async def chooseDirectory(session, preferred = None):
  # /tmp does not survive a reboot, so prefer real storage when there is any
  if preferred:
    return requireSafePath(preferred), True

  from regent.adblock import findPersistentStorage

  candidates = await findPersistentStorage(session)

  if candidates:
    return f"{candidates[0]['mount']}/{SLUG}-backups", True

  return FALLBACK_DIRECTORY, False

async def listBackups(session, settings, directory = None):
  checkTier(READ, settings)

  target, persistent = await chooseDirectory(session, directory)

  result = await session.run(
    f"mkdir -p {target} 2>/dev/null; "
    f"find {target} -maxdepth 1 -name '{SLUG}-*.tar.gz' -exec sh -c "
    f"'printf \"%s %s %s\\n\" \"$(basename \"$1\")\" \"$(wc -c < \"$1\")\" \"$(date -r \"$1\" 2>/dev/null)\"' _ {{}} \\;"
  )

  return {
    "directory": target,
    "onPersistentStorage": persistent,
    "backups": parseListing(result.stdout, target)
  }

async def createBackup(session, settings, label = "manual", directory = None):
  checkTier(WRITE, settings)

  safeLabel = requireLabel(label)
  target, persistent = await chooseDirectory(session, directory)

  stamp = (await session.run("date +%Y%m%d-%H%M%S")).stdout.strip()

  if not stamp:
    raise BackupError("could not read the time from the router - refusing to write an unnamed backup")

  name = backupName(safeLabel, stamp)
  path = f"{target}/{name}"

  await session.run(f"mkdir -p {target}")

  result = await session.run(f"{BACKUP_COMMAND} {path} 2>&1")

  size = (await session.run(f"wc -c < {path} 2>/dev/null")).stdout.strip()

  if not size.isdigit() or int(size) == 0:
    raise BackupError(f"the backup was not written: {result.stdout.strip()[:200] or 'no output'}")

  detail = f"configuration archived to {path}"

  if not persistent:
    detail += " - this is tmpfs and will not survive a reboot, which is what a backup is for"

  return {
    "backup": {"name": name, "path": path, "bytes": int(size), "createdAt": stamp},
    "location": target,
    "detail": detail
  }

async def inspectBackup(session, settings, path):
  checkTier(READ, settings)

  safePath = requireSafePath(path)
  listing = await session.run(f"tar -tzf {safePath} 2>/dev/null")

  if not listing.stdout.strip():
    raise BackupError(f"{safePath} is not a readable archive")

  return {"path": safePath, "configs": parseChangedConfigs(listing.stdout)}

async def restoreBackup(session, settings, path, confirm = False):
  # restoring overwrites every config in the archive and needs a reboot
  checkTier(DESTRUCTIVE, settings, confirmed = confirm)

  safePath = requireSafePath(path)

  exists = (await session.run(f"test -f {safePath} && echo yes || echo no")).stdout.strip()

  if exists != "yes":
    raise BackupError(f"there is no backup at {safePath}")

  inspection = await inspectBackup(session, settings, safePath)

  result = await session.run(f"sysupgrade -r {safePath} 2>&1")

  if result.exitCode != 0:
    raise BackupError(f"restore failed: {result.stdout.strip()[:200] or result.stderr.strip()[:200]}")

  # detached, because the reboot kills the session carrying the command
  await session.run("nohup sh -c 'sleep 2; reboot' >/dev/null 2>&1 &")

  return {
    "restored": safePath,
    "changedConfigs": inspection["configs"],
    "rebooting": True,
    "detail": "configuration restored; the router is rebooting to apply it and will be gone for a minute"
  }

def registerTools(mcp, session, settings):
  @mcp.tool(annotations = annotationsFor(READ, "List configuration backups"))
  @toolSafe
  async def routerBackups(directory: str | None = None) -> BackupList:
    """Backups taken by this server, newest first, and whether they sit on storage that
    survives a reboot"""
    return await listBackups(session, settings, directory = directory)

  @mcp.tool(annotations = annotationsFor(WRITE, "Back up the configuration"))
  @toolSafe
  async def routerBackupCreate(label: str = "manual", directory: str | None = None) -> BackupReply:
    """Archive the router's whole configuration, the same archive LuCI offers, so it can
    be restored by hand even without this server. Written to persistent storage when the
    router has any, since a backup in tmpfs does not survive the reboot it insures"""
    return await createBackup(session, settings, label = label, directory = directory)

  @mcp.tool(annotations = annotationsFor(READ, "Inspect a backup"))
  @toolSafe
  async def routerBackupInspect(path: str) -> dict:
    """Which configuration files a backup contains, without restoring it"""
    return await inspectBackup(session, settings, path)

  @mcp.tool(annotations = annotationsFor(DESTRUCTIVE, "Restore a backup"))
  @toolSafe
  async def routerBackupRestore(path: str, confirm: bool = False) -> RestoreReply:
    """Put a backup back and reboot into it. Overwrites every configuration file the
    archive contains. Requires the write gate and confirm=True"""
    return await restoreBackup(session, settings, path, confirm = confirm)
# ---------------------------------------------------------------------------- #