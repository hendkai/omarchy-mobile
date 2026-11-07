# SPDX-License-Identifier: GPL-3.0-or-later
"""Tests fuer den USB-Gadget-Schutz (bin/snaildrive-aoa-gadget) gegen ein
temporaeres configfs-Abbild. Schreibt nie nach /sys."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN = HERE.parent / "bin" / "snaildrive-aoa-gadget"
loader = importlib.machinery.SourceFileLoader("aoa_gadget", str(BIN))
spec = importlib.util.spec_from_loader("aoa_gadget", loader)
assert spec is not None
gadget = importlib.util.module_from_spec(spec)
sys.modules["aoa_gadget"] = gadget  # dataclasses braucht das Modul in sys.modules
loader.exec_module(gadget)


class Gadget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "usb_gadget")
        self.udc_dir = os.path.join(self.tmp.name, "udc")
        os.makedirs(self.root)
        os.makedirs(self.udc_dir)
        Path(self.udc_dir, "fe-udc.1").write_text("", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *args):
        out, err = io.StringIO(), io.StringIO()
        extra = ["--root", self.root]
        if args[0] != "release":
            extra += ["--udc-dir", self.udc_dir]
        with redirect_stdout(out), redirect_stderr(err):
            rc = gadget.main(["snaildrive-aoa-gadget", *args, *extra])
        return rc, out.getvalue(), err.getvalue()

    def test_plan_writes_nothing(self):
        rc, out, _ = self.run_cmd("plan", "--bind")
        self.assertEqual(rc, 0)
        self.assertIn("UDC", out)
        self.assertEqual(os.listdir(self.root), [])

    def test_apply_creates_function_and_binds_free_udc(self):
        rc, out, _ = self.run_cmd("apply", "--bind")
        self.assertEqual(rc, 0)
        base = os.path.join(self.root, gadget.GADGET)
        self.assertEqual(Path(base, "UDC").read_text(encoding="utf-8"), "fe-udc.1")
        self.assertTrue(os.path.isdir(os.path.join(base, "functions", gadget.FUNCTION)))
        self.assertTrue(os.path.islink(os.path.join(base, "configs", gadget.CONFIG, gadget.FUNCTION)))
        self.assertEqual(Path(base, "strings/0x409/manufacturer").read_text(encoding="utf-8"), "SnailDrive")

    def test_refuses_when_ssh_gadget_holds_the_udc(self):
        ssh = os.path.join(self.root, "g1")
        os.makedirs(ssh)
        Path(ssh, "UDC").write_text("fe-udc.1", encoding="utf-8")
        rc, _, err = self.run_cmd("apply", "--bind")
        self.assertEqual(rc, 3)
        self.assertIn("g1", err)
        self.assertFalse(os.path.exists(os.path.join(self.root, gadget.GADGET)))

    def test_unbound_apply_does_not_need_a_udc(self):
        os.remove(os.path.join(self.udc_dir, "fe-udc.1"))  # kein UDC vorhanden
        rc, _, _ = self.run_cmd("apply")
        self.assertEqual(rc, 0)
        udc = Path(self.root, gadget.GADGET, "UDC")
        self.assertTrue(not udc.exists() or udc.read_text(encoding="utf-8") == "")

    def test_bind_without_udc_is_refused(self):
        os.remove(os.path.join(self.udc_dir, "fe-udc.1"))
        rc, _, err = self.run_cmd("apply", "--bind")
        self.assertEqual(rc, 3)
        self.assertIn("kein UDC", err)

    def test_missing_configfs_is_refused(self):
        os.rmdir(self.root)
        rc, _, err = self.run_cmd("plan", "--bind")
        self.assertEqual(rc, 3)
        self.assertIn("configfs fehlt", err)

    def test_release_clears_only_our_udc(self):
        self.run_cmd("apply", "--bind")
        rc, out, _ = self.run_cmd("release")
        self.assertEqual(rc, 0)
        self.assertEqual(Path(self.root, gadget.GADGET, "UDC").read_text(encoding="utf-8"), "")

    def test_second_apply_refuses_when_already_active(self):
        self.run_cmd("apply", "--bind")
        rc, _, err = self.run_cmd("apply", "--bind")
        self.assertEqual(rc, 3)
        self.assertIn("bereits aktiv", err)


if __name__ == "__main__":
    unittest.main()
