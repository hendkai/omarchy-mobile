"""Erkennung: EQB-Kfz-Anschluss vs. PC / Ladegeraet / Fremdgeraet.

Entscheidend ist NICHT VBUS (jedes Ladegeraet liefert VBUS). Das Signal ist
das Android-Open-Accessory-Verhalten (AOA):

  * Der Kfz-Host (MBUX) fuehrt den AOA-Handshake (Vendor-Requests 51/52/53)
    gegen das Phone aus. Danach re-enumeriert das Phone mit der
    AOA-Accessory-Kennung 18d1:2d00 (bzw. 18d1:2d01 mit ADB).
  * Ein PC oder ein reines Ladegeraet fuehrt keinen AOA-Handshake aus und
    erscheint daher nie mit dieser Kennung -> kein Fehlalarm.

Zusaetzlich gibt es eine optionale Allowlist fuer Fahrzeug-Kennungen
(``extra_car_ids`` in der Konfiguration). Sie ist absichtlich LEER: echte
Werte gehoeren erst nach einer Beobachtung am Fahrzeug hinein (Regel: nur
anstecken + beobachten, nichts veraendern).

Reine Python-3-Standardbibliothek, ohne Hardware testbar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Tuple

#: Google-Vendor-ID der AOA-Spezifikation (oeffentlich).
AOA_VENDOR_ID = "18d1"

#: AOA-Accessory-Produkt-IDs: 2d00 = accessory, 2d01 = accessory + adb.
AOA_PRODUCT_IDS = frozenset({"2d00", "2d01"})

REASON_AOA = "aoa-accessory"
REASON_EXTRA = "extra-car-id"
REASON_NOT_AOA = "not-aoa"
REASON_NO_IDS = "no-ids"


@dataclass(frozen=True)
class UsbId:
    """Vendor-/Product-Kennung eines USB-Geraets (lowercase hex, 4 Stellen)."""

    vendor: str
    product: str

    @staticmethod
    def parse(vendor: Optional[str], product: Optional[str]) -> Optional["UsbId"]:
        if not vendor or not product:
            return None
        v = vendor.strip().lower().zfill(4)
        p = product.strip().lower().zfill(4)
        return UsbId(v, p)

    @staticmethod
    def from_udev_product(prop: Optional[str]) -> Optional["UsbId"]:
        """udev PRODUCT=vid/pid/bcd -> UsbId (None bei unbrauchbarem Wert)."""
        if not prop:
            return None
        parts = prop.split("/")
        if len(parts) < 2:
            return None
        return UsbId.parse(parts[0], parts[1])

    def __str__(self) -> str:
        return f"{self.vendor}:{self.product}"


@dataclass(frozen=True)
class Verdict:
    is_car: bool
    reason: str
    usb_id: Optional[UsbId] = None


@dataclass(frozen=True)
class DetectConfig:
    extra_car_ids: Tuple[UsbId, ...] = field(default_factory=tuple)


def classify(usb_id: Optional[UsbId], config: DetectConfig = DetectConfig()) -> Verdict:
    """Entscheidet, ob ein Geraet (vid/pid) der Kfz-Anschluss ist."""
    if usb_id is None:
        return Verdict(False, REASON_NO_IDS)
    if usb_id.vendor == AOA_VENDOR_ID and usb_id.product in AOA_PRODUCT_IDS:
        return Verdict(True, REASON_AOA, usb_id)
    if usb_id in config.extra_car_ids:
        return Verdict(True, REASON_EXTRA, usb_id)
    return Verdict(False, REASON_NOT_AOA, usb_id)


def usb_id_from_env(env: dict) -> Optional[UsbId]:
    """Liest die Kennung aus einer udev-Umgebung.

    Reihenfolge: ID_VENDOR_ID/ID_MODEL_ID (udev-Hwdb), sonst PRODUCT=vid/pid/bcd.
    """
    uid = UsbId.parse(env.get("ID_VENDOR_ID"), env.get("ID_MODEL_ID"))
    if uid is not None:
        return uid
    return UsbId.from_udev_product(env.get("PRODUCT"))


def parse_extra_ids(items: Iterable[str]) -> Tuple[UsbId, ...]:
    """'vid:pid' Strings aus der Konfiguration -> UsbId-Tupel. Ungueltige werden verworfen."""
    out = []
    for raw in items:
        if ":" not in raw:
            continue
        v, p = raw.split(":", 1)
        uid = UsbId.parse(v, p)
        if uid is not None and all(c in "0123456789abcdef" for c in uid.vendor + uid.product):
            out.append(uid)
    return tuple(out)
