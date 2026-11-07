# SPDX-License-Identifier: MIT
"""modemlocation: NMEA-Parser, Bring-up-Gate, Abschalten. Phone-frei.

Alle Koordinaten sind erfundene Werte (48.0 / 9.0), alle mmcli-Antworten
kommen aus einem Fake-Runner. Kein Modem, kein D-Bus.
"""
import unittest
from datetime import datetime, timezone

import modemlocation as ml

NOW = datetime(2026, 11, 7, 0, 0, 30, tzinfo=timezone.utc)


def nmea(body: str) -> str:
    """$body*hh mit korrekter XOR-Pruefsumme."""
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


def gga(time="100000", lat="4800.000,N", lon="00900.000,E", quality="1", hdop="0.9"):
    return nmea(f"GPGGA,{time},{lat},{lon},{quality},08,{hdop},250.0,M,47.0,M,,")


def rmc(time="100000", status="A", date="071126", lat="4800.000,N", lon="00900.000,E"):
    return nmea(f"GPRMC,{time},{status},{lat},{lon},0.5,90.0,{date},,,A")


def local(y, mo, d, h, mi, s):
    """UTC-Zeit als naive lokale Zeit, so wie das Modul sie liefert."""
    return datetime.fromtimestamp(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())


class Parser(unittest.TestCase):
    def test_rmc_gives_utc_date_and_time_as_local(self):
        fix = ml.parse_sentence(rmc(), NOW)
        self.assertIsNotNone(fix)
        self.assertEqual(fix.ts, local(2026, 11, 7, 10, 0, 0))
        self.assertAlmostEqual(fix.lat, 48.0)
        self.assertAlmostEqual(fix.lon, 9.0)

    def test_gga_takes_todays_utc_date(self):
        fix = ml.parse_sentence(gga("100000"), datetime(2026, 11, 7, 10, 0, 30, tzinfo=timezone.utc))
        self.assertIsNotNone(fix)
        self.assertEqual(fix.ts, local(2026, 11, 7, 10, 0, 0))
        self.assertEqual(fix.hdop, 0.9)

    def test_gga_in_the_future_is_from_yesterday(self):
        # 10:00 UTC an einem Morgen um 00:00:30 UTC kann nur der Vortag sein
        fix = ml.parse_sentence(gga("100000"), NOW)
        self.assertEqual(fix.ts, local(2026, 11, 6, 10, 0, 0))

    def test_gga_just_before_midnight_is_yesterday(self):
        # NOW ist 00:00:30 UTC; ein Fix von 23:59:58 UTC ist von gestern.
        fix = ml.parse_sentence(gga("235958"), NOW)
        self.assertEqual(fix.ts, local(2026, 11, 6, 23, 59, 58))

    def test_southern_and_western_hemisphere_are_negative(self):
        fix = ml.parse_sentence(rmc(lat="3330.000,S", lon="07000.000,W"), NOW)
        self.assertAlmostEqual(fix.lat, -33.5)
        self.assertAlmostEqual(fix.lon, -70.0)

    def test_checksum_mismatch_is_ignored(self):
        bad = gga().replace("4800.000", "4801.000")   # Koerper geaendert, Summe nicht
        self.assertIsNone(ml.parse_sentence(bad, NOW))

    def test_rmc_void_status_is_no_fix(self):
        self.assertIsNone(ml.parse_sentence(rmc(status="V"), NOW))

    def test_gga_quality_zero_is_no_fix(self):
        self.assertIsNone(ml.parse_sentence(gga(quality="0"), NOW))

    def test_null_island_and_out_of_range_are_rejected(self):
        self.assertIsNone(ml.parse_sentence(rmc(lat="0000.000,N", lon="00000.000,E"), NOW))
        self.assertIsNone(ml.parse_sentence(rmc(lat="9500.000,N"), NOW))

    def test_junk_and_other_sentence_types_are_ignored(self):
        self.assertIsNone(ml.parse_sentence("hello", NOW))
        self.assertIsNone(ml.parse_sentence(nmea("GPGSV,1,1,00"), NOW))

    def test_parse_output_finds_sentences_in_noise_and_dedupes(self):
        text = "location: GPS\n  NMEA: " + rmc() + "\n  NMEA: " + rmc() + "\n" \
               "  junk ignored\n  NMEA: " + rmc(time="100010") + "\n"
        fixes = ml.parse_output(text, NOW)
        self.assertEqual([f.ts for f in fixes],
                         [local(2026, 11, 7, 10, 0, 0), local(2026, 11, 7, 10, 0, 10)])


class FakeRunner:
    """Antwortet je mmcli-Argumentliste; jeder Aufruf wird protokolliert."""

    def __init__(self, answers, get_sequence=None):
        self.answers = answers          # key: Tuple der Argumente -> (rc, out, err)
        self.get_sequence = list(get_sequence or [])
        self.calls = []

    def __call__(self, argv, timeout):
        args = tuple(argv[1:])
        self.calls.append(args)
        if args == ("-m", "0", "--location-get") and self.get_sequence:
            return self.get_sequence.pop(0) if len(self.get_sequence) > 1 else self.get_sequence[0]
        return self.answers.get(args, (0, "", ""))

    def names(self):
        return [" ".join(c) for c in self.calls]


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


MODEM_LIST = (0, '{"modem-list": ["/org/freedesktop/ModemManager1/Modem/0"]}', "")


def modem_state(state):
    return (0, '{"modem": {"generic": {"state": "%s"}}}' % state, "")


def base_answers(state="enabled"):
    return {("-L", "-J"): MODEM_LIST,
            ("-m", "0", "-J"): modem_state(state),
            ("-m", "0", "--location-enable-gps-nmea"): (0, "", ""),
            ("-m", "0", "--location-disable-gps-nmea"): (0, "", "")}


class Gate(unittest.TestCase):
    def test_no_modem_is_unavailable_and_gnss_stays_off(self):
        run = FakeRunner({("-L", "-J"): (0, '{"modem-list": []}', "")})
        with self.assertRaises(ml.GpsUnavailable):
            ml.collect_fixes(runner=run)
        self.assertNotIn("-m 0 --location-enable-gps-nmea", run.names())

    def test_modem_not_up_keeps_gnss_off(self):
        for state in ("disabled", "locked", "initializing", "enabling", "failed"):
            with self.subTest(state=state):
                run = FakeRunner(base_answers(state))
                with self.assertRaises(ml.GpsUnavailable):
                    ml.collect_fixes(runner=run)
                self.assertFalse(any("enable" in n for n in run.names()), run.names())

    def test_missing_mmcli_is_unavailable(self):
        with self.assertRaises(ml.GpsUnavailable):
            ml.subprocess_runner(["omamobile-mmcli-gibt-es-nicht"], 1)

    def test_mmcli_error_message_reaches_the_caller(self):
        run = FakeRunner({("-L", "-J"): (1, "", "error: Authorization denied")})
        with self.assertRaises(ml.GpsUnavailable) as cm:
            ml.collect_fixes(runner=run)
        self.assertIn("Authorization denied", str(cm.exception))


class Collect(unittest.TestCase):
    def test_first_fix_returns_and_gnss_goes_off_again(self):
        run = FakeRunner(base_answers(), get_sequence=[(0, rmc(), "")])
        fixes = ml.collect_fixes(runner=run, sleep=lambda s: None)
        self.assertEqual(len(fixes), 1)
        self.assertAlmostEqual(fixes[0].lat, 48.0)
        names = run.names()
        self.assertLess(names.index("-m 0 --location-enable-gps-nmea"), names.index("-m 0 --location-get"))
        self.assertEqual(names[-1], "-m 0 --location-disable-gps-nmea")

    def test_no_fix_within_timeout_raises_and_still_disables(self):
        clock = FakeClock()
        run = FakeRunner(base_answers(), get_sequence=[(0, "nothing here", "")])
        with self.assertRaises(ml.GpsUnavailable) as cm:
            ml.collect_fixes(timeout_s=5, interval_s=1, runner=run, clock=clock, sleep=clock.sleep)
        self.assertIn("kein GPS-Fix", str(cm.exception))
        self.assertEqual(run.names()[-1], "-m 0 --location-disable-gps-nmea")

    def test_get_errors_are_retried_until_a_fix(self):
        clock = FakeClock()
        run = FakeRunner(base_answers(),
                         get_sequence=[(1, "", "busy"), (1, "", "busy"), (0, gga(), "")])
        fixes = ml.collect_fixes(timeout_s=30, interval_s=1, runner=run, clock=clock, sleep=clock.sleep)
        self.assertEqual(len(fixes), 1)

    def test_window_collects_fixes_after_the_first_one(self):
        clock = FakeClock()
        seq = [(0, rmc(time="100000"), ""), (0, rmc(time="100002"), ""),
               (0, rmc(time="100004"), ""), (0, rmc(time="100006"), "")]
        run = FakeRunner(base_answers(), get_sequence=seq)
        fixes = ml.collect_fixes(window_s=4, timeout_s=30, interval_s=2, runner=run,
                                 clock=clock, sleep=clock.sleep)
        self.assertEqual([f.ts.second for f in fixes], [0, 2, 4])
        self.assertEqual(run.names()[-1], "-m 0 --location-disable-gps-nmea")

    def test_failing_disable_does_not_hide_the_fixes(self):
        answers = base_answers()
        answers[("-m", "0", "--location-disable-gps-nmea")] = (1, "", "boom")
        run = FakeRunner(answers, get_sequence=[(0, rmc(), "")])
        fixes = ml.collect_fixes(runner=run, sleep=lambda s: None)
        self.assertEqual(len(fixes), 1)

    def test_failing_enable_is_reported_and_disable_is_tried(self):
        answers = base_answers()
        answers[("-m", "0", "--location-enable-gps-nmea")] = (1, "", "no gps")
        run = FakeRunner(answers)
        with self.assertRaises(ml.GpsUnavailable) as cm:
            ml.collect_fixes(runner=run, sleep=lambda s: None)
        self.assertIn("no gps", str(cm.exception))
        self.assertEqual(run.names()[-1], "-m 0 --location-disable-gps-nmea")


if __name__ == "__main__":
    unittest.main()
