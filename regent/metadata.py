# ---------------------------------------------------------------------------- #
# DESCRIPTION: static application metadata, and the single place the name lives
# ---------------------------------------------------------------------------- #

# LOGIC ---------------------------------------------------------------------- #
AUTHOR = "m-o-z-z-i-x"
APP_NAME = "REGENT"
APP_VERSION = "1.0.0"
DESCRIPTION = "MCP server that configures an OpenWrt router from natural-language intent"

# everything this server creates on the router is named from this, so a rename is one edit
SLUG = APP_NAME.lower()

# variables name what they configure, not the project, so a rename cannot invalidate a .env
ENV_PREFIX = "OPENWRT"

def envName(suffix):
  return f"{ENV_PREFIX}_{suffix}"
# ---------------------------------------------------------------------------- #