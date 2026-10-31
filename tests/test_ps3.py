"""
Synthetic tests for the PrusaSlicer 3 tools: tools/ps3_yaml.py,
tools/build_ps3_vendor.py and tools/install_ps3.py.

None needs PrusaSlicer, Prusa's installed YAML or a PrusaResearch.ini: each test
writes the few documents it needs into a scratch directory.

    python3 -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

import yaml

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'tools')
sys.path.insert(0, TOOLS)

import ps3_yaml  # noqa: E402


class Scratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        return path


class Dump(unittest.TestCase):
    def test_dump_round_trip(self):
        d = {'a': '0x0', 'b': '12:30', 'c': 'yes', 'd': '#FF8000', 'e': '85%',
             'f': 'G1 X0\nG1 Y0\n', 'g': 0.1, 'h': ['16x16/QOI'], 'i': None,
             'j': '0.4', 'k': 'M572 S0 ; Pressure advance\n\nM906 P2 ; set E current',
             'l': {'$.type': 'PLA', '$.tags': ['abrasive']}, 'm': True, 'n': 3,
             'o': "it's", 'p': '0.20mm Balanced ★', 'q': '', 'r': ' lead', 's': 1e-05,
             't': [{'tool': '0.4HF'}, {'tool': '0.25'}], 'u': 'a: b', 'v': '- x'}
        self.assertEqual(yaml.safe_load(ps3_yaml.dump(d)), d)

    def test_dump_keeps_order(self):
        text = ps3_yaml.dump({'kind': 'tool_print', 'id': 'x', 'variants': []})
        self.assertLess(text.index('kind'), text.index('id'))
        self.assertLess(text.index('id'), text.index('variants'))

    def test_new_id_deterministic_no_at(self):
        a = ps3_yaml.new_id('filament', 'Prusament PLA @COREONEINDX HF0.6')
        self.assertEqual(a, ps3_yaml.new_id('filament', 'Prusament PLA @COREONEINDX HF0.6'))
        self.assertNotEqual(a, ps3_yaml.new_id('filament', 'Prusament PLA @COREONEINDX 0.25'))
        self.assertNotIn('@', a)
        self.assertNotIn('=', a)
        self.assertEqual(len(a), 22)


class KeyDefs(Scratch):
    FDM = '''
    def = defs.add("perimeter_speed", typeid(double));
    def->location = Print;
    def->overrides_in = Locations{ Tool, Object, Volume };
    def = defs.add("perimeter_acceleration", typeid(double));
    def->location = Print;
    def->overrides_in = { Tool };
    def = defs.add("skirts", typeid(int));
    def->location = Print;
    def = defs.add("retract_length", typeid(double));
    def->location = Print;
    def->overrides_in = Locations{ Tool, Filament };
    def = defs.add("pressure_advance", typeid(EnumWrapper));
    def->location = Filament;
    def->init_fn = init_with(
        PressureAdvance::Disabled,
        {{int(PressureAdvance::Disabled), "disabled", L("Disabled")},
         {int(PressureAdvance::AutomaticCalibration), "automatic_calibration", L("Auto")}}
    );
    '''
    COMMON = '''
    def = defs.add("elefant_foot_compensation", typeid(double));
    if (technology == SLA) {
        def->location = printer;
        def->overrides_in = Locations{ sla_material, sla_object};
    }
    if (technology == FFF) {
        def->location = FDMConfigLocation::Print;
        def->overrides_in = Locations{ fdm_tool, fdm_object, fdm_volume };
    }
    def = defs.add("layer_height", typeid(double));
    def->location = print;
    if (technology == FFF)
        def->overrides_in = Locations{ fdm_object, fdm_volume };
    '''

    def test_extract_both_override_syntaxes_and_common(self):
        import extract_ps3_keydefs
        fdm = self.write('ConfigDefsFDM.cpp', self.FDM)
        common = self.write('ConfigCommon.cpp', self.COMMON)
        out = os.path.join(self.dir, 'keydefs.tsv')
        extract_ps3_keydefs.main([fdm, common, out])
        defs = ps3_yaml.load_keydefs(out)
        self.assertEqual(defs['perimeter_speed'].overrides, frozenset({'Tool', 'Object', 'Volume'}))
        self.assertEqual(defs['perimeter_acceleration'].overrides, frozenset({'Tool'}))
        self.assertEqual(defs['skirts'].overrides, frozenset())
        self.assertEqual(defs['pressure_advance'].location, 'Filament')
        self.assertEqual(defs['pressure_advance'].enum, ('disabled', 'automatic_calibration'))
        self.assertEqual(defs['elefant_foot_compensation'].location, 'Print')
        self.assertIn('Tool', defs['elefant_foot_compensation'].overrides)
        self.assertEqual(defs['layer_height'].overrides, frozenset({'Object', 'Volume'}))
        self.assertTrue(ps3_yaml.tool_key(defs, 'retract_length'))
        self.assertTrue(ps3_yaml.filament_key(defs, 'retract_length'))
        self.assertFalse(ps3_yaml.tool_key(defs, 'skirts'))
        self.assertTrue(ps3_yaml.filament_key(defs, 'pressure_advance'))


if __name__ == '__main__':
    unittest.main()
