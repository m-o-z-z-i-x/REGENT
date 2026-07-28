# ---------------------------------------------------------------------------- #
# DESCRIPTION: deferred revert armed on the router before any risky change
# ---------------------------------------------------------------------------- #

# DEPENDENCIES --------------------------------------------------------------- #
from dataclasses import dataclass

from regent.ssh import SshError
from regent.logger import warning
from regent.metadata import SLUG
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
# named from SLUG so renaming the project does not leave orphaned files on the router
PID_FILE = f"/tmp/{SLUG}-rollback.pid"
BACKUP_DIR = f"/tmp/{SLUG}-rollback"

@dataclass
class RollbackOutcome:
  applied: bool
  confirmed: bool
  detail: str

def buildArm(delaySeconds, configs):
  # restore a copy of the files: uci revert only undoes staged changes, not committed ones
  #
  # detached, so the job survives the ssh session dying - the case being insured against
  snapshots = "; ".join(f"cp /etc/config/{config} {BACKUP_DIR}/{config}" for config in configs)
  restores = "; ".join(f"cp {BACKUP_DIR}/{config} /etc/config/{config}" for config in configs)
  script = f"sleep {delaySeconds}; {restores}; /etc/init.d/network restart"

  return f"mkdir -p {BACKUP_DIR}; {snapshots}; nohup sh -c '{script}' >/dev/null 2>&1 & echo $! > {PID_FILE}"

def buildDisarm():
  return f"kill $(cat {PID_FILE}) 2>/dev/null; rm -f {PID_FILE}; rm -rf {BACKUP_DIR}"

def buildProbe():
  return "echo alive"

async def withRollback(session, settings, configs, applyFn):
  # arm, apply, check the router still answers, then disarm. anything else leaves the timer running
  try:
    armed = await session.run(buildArm(settings.rollbackDelay, configs))
  except SshError as err:
    return RollbackOutcome(applied = False, confirmed = False, detail = f"could not arm the rollback timer: {err}")

  if not armed.ok:
    return RollbackOutcome(applied = False, confirmed = False, detail = f"could not arm the rollback timer: {armed.stderr or 'non-zero exit'}")

  try:
    await applyFn()
  except SshError as err:
    warning(f"change failed mid-apply, leaving the rollback armed: {err}")

    return RollbackOutcome(applied = True, confirmed = False, detail = f"the link dropped while applying - the router reverts itself in {settings.rollbackDelay}s")

  try:
    probe = await session.run(buildProbe())
  except SshError:
    warning("probe failed after the change, leaving the rollback armed")

    return RollbackOutcome(applied = True, confirmed = False, detail = f"the router stopped answering after the change - it reverts itself in {settings.rollbackDelay}s")

  if not probe.ok:
    return RollbackOutcome(applied = True, confirmed = False, detail = f"the router answered but not correctly - it reverts itself in {settings.rollbackDelay}s")

  await session.run(buildDisarm())

  return RollbackOutcome(applied = True, confirmed = True, detail = "change applied and confirmed; the rollback timer was cancelled")
# ---------------------------------------------------------------------------- #