import pytest

from regent.secrets import (
  isSecretName, isSecretPath, redactUciOutput, redactMapping, countRedactions, MASK
)

# shaped like the passwall output that used to expose a node's credentials
REAL_PASSWALL = """passwall.2nQhjp1S=nodes
passwall.2nQhjp1S.protocol='vless'
passwall.2nQhjp1S.port='443'
passwall.2nQhjp1S.address='node.example.com'
passwall.2nQhjp1S.uuid='00000000-0000-4000-8000-000000000000'
passwall.2nQhjp1S.reality_publicKey='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
passwall.2nQhjp1S.reality_shortId='beef'
passwall.2nQhjp1S.tls_serverName='example.com'"""

REAL_WIRELESS = """wireless.wifinet1=wifi-iface
wireless.wifinet1.mode='ap'
wireless.wifinet1.ssid='HomeNet'
wireless.wifinet1.encryption='psk-mixed'
wireless.wifinet1.key='s3cr3t-psk'"""

@pytest.mark.parametrize("name", ["key", "uuid", "password", "reality_publicKey", "PSK", "Token"])
def test_secretNamesAreRecognisedRegardlessOfCase(name):
  assert isSecretName(name) is True

@pytest.mark.parametrize("name", ["ssid", "protocol", "port", "address", "mode", "encryption"])
def test_ordinaryNamesAreLeftAlone(name):
  assert isSecretName(name) is False

def test_theLeakedNodeCredentialsAreMasked():
  redacted = redactUciOutput(REAL_PASSWALL)

  assert "00000000-0000-4000-8000-000000000000" not in redacted
  assert "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in redacted
  assert "beef" not in redacted

def test_thePathStaysVisibleSoTheModelKnowsTheFieldExists():
  redacted = redactUciOutput(REAL_PASSWALL)

  assert f"passwall.2nQhjp1S.uuid={MASK}" in redacted

def test_nonSecretFieldsSurviveUntouched():
  redacted = redactUciOutput(REAL_PASSWALL)

  assert "passwall.2nQhjp1S.protocol='vless'" in redacted
  assert "passwall.2nQhjp1S.address='node.example.com'" in redacted

def test_theWifiPskIsMaskedToo():
  redacted = redactUciOutput(REAL_WIRELESS)

  assert "s3cr3t-psk" not in redacted
  assert "wireless.wifinet1.ssid='HomeNet'" in redacted

def test_sectionHeaderLinesAreNotMangled():
  # "passwall.2nQhjp1S=nodes" has no option segment and must pass through
  assert "passwall.2nQhjp1S=nodes" in redactUciOutput(REAL_PASSWALL)

def test_redactMappingMasksParsedStructures():
  masked = redactMapping({"ssid": "HomeNet", "key": "hunter2", "uuid": "abc"})

  assert masked == {"ssid": "HomeNet", "key": MASK, "uuid": MASK}

def test_countRedactionsReportsHowMuchWasHidden():
  assert countRedactions(redactUciOutput(REAL_PASSWALL)) == 3

# the path segment is an account token, but the option is called "url" like any other
REAL_SUBSCRIPTION = "passwall.@subscribe_list[0].url='https://example.ru/provider/sub/user/token'"

def test_aSubscriptionUrlIsTreatedAsACredential():
  redacted = redactUciOutput(REAL_SUBSCRIPTION)

  assert "provider/sub/user/token" not in redacted
  assert "passwall.@subscribe_list[0].url=" in redacted

def test_anOrdinaryUrlIsLeftReadable():
  # masking every url would hide blocklist sources and firmware mirrors as well
  plain = "adblock.sources.adaway.url='https://adaway.org/hosts.txt'"

  assert redactUciOutput(plain) == plain

def test_isSecretPathMatchesOnlyTheSubscriptionShape():
  assert isSecretPath("passwall.@subscribe_list[0].url") is True
  assert isSecretPath("adblock.sources.adaway.url") is False

# uci renders the same section two ways, so matching the path catches only one of them
CANONICAL_SUBSCRIPTION = """passwall.cfg128b02=subscribe_list
passwall.cfg128b02.remark='SoloNet'
passwall.cfg128b02.url='https://example.ru/provider/sub/user/token'
passwall.cfg128b02.auto_update='0'"""

def test_aSubscriptionUrlIsMaskedUnderItsShortSectionNameToo():
  redacted = redactUciOutput(CANONICAL_SUBSCRIPTION)

  assert "provider/sub/user/token" not in redacted
  assert "passwall.cfg128b02.url=***redacted***" in redacted

def test_theSectionTypeLineSurvivesRedaction():
  assert "passwall.cfg128b02=subscribe_list" in redactUciOutput(CANONICAL_SUBSCRIPTION)

def test_harmlessFieldsInASecretSectionStayReadable():
  redacted = redactUciOutput(CANONICAL_SUBSCRIPTION)

  assert "passwall.cfg128b02.remark='SoloNet'" in redacted
  assert "passwall.cfg128b02.auto_update='0'" in redacted

def test_aUrlOutsideASecretSectionIsStillReadable():
  ordinary = """adblock.sources=source_list
adblock.sources.url='https://adaway.org/hosts.txt'"""

  assert redactUciOutput(ordinary) == ordinary

def test_outputWithoutSecretsIsUnchanged():
  plain = "network.lan=interface\nnetwork.lan.proto='static'"

  assert redactUciOutput(plain) == plain