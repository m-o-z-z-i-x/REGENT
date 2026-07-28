import json

import pytest

from regent.wireless import (
  redactSecrets, summariseRadio, parseAssoclist, getWirelessStatus, getWirelessClients
)
from regent.ssh import CommandResult
from regent.config import Settings
from regent.ubus import buildCall
from tests.fakes import FakeSession

# captured verbatim from the live Archer C59; the psk is present in the real reply
REAL_STATUS = json.dumps({
  "radio0": {
    "up": True, "disabled": False,
    "config": {"channel": "36", "band": "5g", "htmode": "VHT80"},
    "interfaces": [{
      "section": "wifinet1", "ifname": "phy0-ap0",
      "config": {
        "mode": "ap", "ssid": "HomeNet", "encryption": "psk-mixed",
        "key": "s3cr3t-psk", "network": ["lan"]
      }
    }]
  },
  "radio1": {
    "up": True, "disabled": False,
    "config": {"channel": "auto", "band": "2g", "htmode": "HT40", "country": "RU"},
    "interfaces": [{
      "section": "wifinet0", "ifname": "phy1-sta0",
      "config": {
        "mode": "sta", "ssid": "UpstreamAP", "encryption": "psk2",
        "key": "s3cr3t-psk", "bssid": "00:00:5E:00:53:20", "network": ["wwan"]
      }
    }]
  }
})

REAL_ASSOCLIST = """00:00:5E:00:53:60  -59 dBm / -106 dBm (SNR 47)  170 ms ago
\tRX: 6.0 MBit/s                                  7698 Pkts.
\tTX: 433.3 MBit/s, VHT-MCS 9, 80MHz, VHT-NSS 1      6727 Pkts.
\texpected throughput: unknown"""

def makeSettings():
  return Settings(
    host = "192.168.10.1", user = "root", port = 22, keyPath = "./keys/key",
    timeout = 30, rollbackDelay = 90, writeEnabled = False
  )

def test_redactSecretsRemovesThePsk():
  assert redactSecrets({"ssid": "HomeNet", "key": "hunter2"}) == {"ssid": "HomeNet"}

def test_redactSecretsCoversTheOtherSecretFieldNames():
  cleaned = redactSecrets({"wpa_psk": "a", "password": "b", "auth_secret": "c", "ssid": "x"})

  assert cleaned == {"ssid": "x"}

def test_summariseRadioNeverLeaksTheKey():
  summary = summariseRadio("radio0", json.loads(REAL_STATUS)["radio0"])

  assert "s3cr3t-psk" not in json.dumps(summary)

def test_summariseRadioKeepsWhatMatters():
  summary = summariseRadio("radio0", json.loads(REAL_STATUS)["radio0"])

  assert summary["band"] == "5g"
  assert summary["channel"] == "36"
  assert summary["htmode"] == "VHT80"
  assert summary["interfaces"][0]["ssid"] == "HomeNet"
  assert summary["interfaces"][0]["mode"] == "ap"
  assert summary["interfaces"][0]["networks"] == ["lan"]

def test_parseAssoclistReadsTheRealFormat():
  stations = parseAssoclist(REAL_ASSOCLIST)

  assert len(stations) == 1
  assert stations[0]["mac"] == "00:00:5e:00:53:60"
  assert stations[0]["signalDbm"] == -59
  assert stations[0]["noiseDbm"] == -106
  assert stations[0]["snr"] == 47
  assert stations[0]["inactiveMs"] == 170

def test_parseAssoclistReadsBothRates():
  station = parseAssoclist(REAL_ASSOCLIST)[0]

  assert station["rxMbits"] == 6.0
  assert station["txMbits"] == 433.3

def test_parseAssoclistHandlesNoStations():
  assert parseAssoclist("No station connected") == []

def test_parseAssoclistHandlesSeveralStations():
  doubled = REAL_ASSOCLIST + "\n" + REAL_ASSOCLIST.replace("00:00:5E:00:53:60", "AA:BB:CC:DD:EE:FF")

  assert len(parseAssoclist(doubled)) == 2

async def test_getWirelessStatusRedactsAcrossTheWholeReply():
  session = FakeSession(responses = {
    buildCall("network.wireless", "status"): CommandResult(exitCode = 0, stdout = REAL_STATUS, stderr = "")
  })

  result = await getWirelessStatus(session, makeSettings())

  assert "s3cr3t-psk" not in json.dumps(result)
  assert len(result["radios"]) == 2

async def test_getWirelessClientsQueriesEachInterface():
  session = FakeSession(
    responses = {
      buildCall("network.wireless", "status"): CommandResult(exitCode = 0, stdout = REAL_STATUS, stderr = ""),
      "iwinfo phy0-ap0 assoclist 2>/dev/null": CommandResult(exitCode = 0, stdout = REAL_ASSOCLIST, stderr = ""),
      "iwinfo phy1-sta0 assoclist 2>/dev/null": CommandResult(exitCode = 0, stdout = "No station connected", stderr = "")
    }
  )

  result = await getWirelessClients(session, makeSettings())
  ap = next(entry for entry in result["interfaces"] if entry["ifname"] == "phy0-ap0")

  assert ap["ssid"] == "HomeNet"
  assert ap["count"] == 1
  assert ap["stations"][0]["txMbits"] == 433.3