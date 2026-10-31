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
import textwrap
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


import ps3_eval  # noqa: E402

INDX = {'printer.base_model': 'COREONE_INDX', 'printer.model': 'COREONE_INDX',
        'feeder.base_model': '', 'feeder.model': '', 'sheet.type': 'satin'}


def tool(dia, hf, layer=None):
    env = dict(INDX, **{'tool.nozzle_diameter': dia, 'tool.nozzle_high_flow': hf})
    if layer is not None:
        env['print.layer_height'] = layer
    return env


class Conditions(unittest.TestCase):
    def check(self, text, env, expected):
        self.assertEqual(ps3_eval.condition(text, [env]), expected, text)

    def test_grammar(self):
        e = tool(0.4, True)
        self.check('printer.base_model == "COREONE_INDX"', e, True)
        self.check('printer.base_model=~/(COREONE|MK4)/', e, False)  # full-string match
        self.check('printer.base_model=~/(COREONE|MK4|COREONE_INDX)/', e, True)
        self.check('tool.nozzle_diameter == 0.4 and not tool.nozzle_high_flow', e, False)
        self.check('! tool.nozzle_high_flow or tool.nozzle_diameter >= 0.4', e, True)
        self.check('feeder.base_model!~/.*MMU.*/ or feeder.single_mode', e, True)  # short circuit
        self.check('(printer.base_model == "X") and feeder.single_mode', e, False)

    def test_unknown_variable_raises(self):
        with self.assertRaises(ps3_eval.UnknownVariable):
            ps3_eval.condition('print.layer_height == 0.2', [tool(0.4, True)])

    def test_any_tool(self):
        envs = [tool(0.4, True), tool(0.25, False)]
        self.assertTrue(ps3_eval.condition('tool.nozzle_diameter == 0.25', envs))
        self.assertFalse(ps3_eval.condition('tool.nozzle_diameter == 0.6', envs))


def docs(text):
    return [d for d in yaml.safe_load_all(textwrap.dedent(text)) if d]


COMMON_NO_QUIT = '''
kind: filament
id: '*ASA*'
name: '*ASA*'
condition: printer.base_model=~/(COREONE|MK4)/
values: {filament_type: ASA, temperature: 260}
variants:
- condition: printer.base_model == "COREONE_INDX"
  id: indxbranch
  values: {retract_length: 0.8}
  variants:
  - condition: tool.nozzle_diameter == 0.4
    id: four
    values: {filament_max_volumetric_speed: 15}
    variants:
    - condition: tool.nozzle_high_flow
      id: fourhf
      values: {filament_max_volumetric_speed: 30}
---
kind: filament
inherits: ['*ASA*']
name: Generic ASA
id: Generic ASA
condition: printer.base_model=~/(COREONE|MK4|COREONE_INDX)/
values: {filament_vendor: Generic}
'''

OWN_QUIT = '''
---
kind: filament
inherits: ['*ASA*']
name: Prusament ASA
id: Prusament ASA
condition: printer.base_model=~/(COREONE|MK4|COREONE_INDX)/
variants:
- condition: printer.base_model == "COREONE_INDX"
  id: pindx
  variants:
  - condition: tool.nozzle_diameter == 0.4
    id: p4
  - name: '*quit*'
    id: pquit
'''


class Resolution(unittest.TestCase):
    def visible(self, text, env):
        coll = ps3_eval.Collection(docs(text))
        return {p.name: p for p in coll.presets([env])}

    def test_fallthrough_is_visible_on_other_nozzles(self):
        got = self.visible(COMMON_NO_QUIT, tool(0.6, True))
        self.assertEqual(set(got), {'Generic ASA'})
        self.assertEqual(got['Generic ASA'].values['retract_length'], 0.8)
        self.assertNotIn('filament_max_volumetric_speed', got['Generic ASA'].values)

    def test_values_merge_down_the_tree(self):
        got = self.visible(COMMON_NO_QUIT, tool(0.4, True))
        v = got['Generic ASA'].values
        self.assertEqual((v['filament_max_volumetric_speed'], v['temperature'],
                          v['filament_vendor']), (30, 260, 'Generic'))

    def test_own_quit_hides_and_parent_quit_does_not(self):
        got = self.visible(COMMON_NO_QUIT + OWN_QUIT, tool(0.6, True))
        self.assertEqual(set(got), {'Generic ASA'})
        got = self.visible(COMMON_NO_QUIT + OWN_QUIT, tool(0.4, False))
        self.assertEqual(set(got), {'Generic ASA', 'Prusament ASA'})

    def test_derive_name_tags(self):
        self.assertEqual(ps3_eval.derive_name('0.20mm Balanced @COREONEINDX 0.6HF', ''),
                         '0.20mm Balanced @COREONEINDX 0.6HF')
        self.assertEqual(ps3_eval.derive_name('', 'A @x'), 'A @x')
        self.assertEqual(ps3_eval.derive_name('B @y', 'A @x'), 'B @y@x')
        self.assertEqual(ps3_eval.derive_name('*quit*', 'Prusament ASA'), '*quit*')

    def test_unconditional_inherits_copies_the_path(self):
        text = COMMON_NO_QUIT + textwrap.dedent('''
        ---
        kind: filament
        id: user
        variants:
        - condition: printer.base_model == "COREONE_INDX"
          id: u1
          name: Mine
          unconditional_inherits: [fourhf]
          values: {temperature: 270}
        ''')
        got = self.visible(text, tool(0.6, True))
        v = got['Mine'].values
        self.assertEqual((v['filament_max_volumetric_speed'], v['temperature'], v['retract_length']),
                         (30, 270, 0.8))

    def test_dangling_id_raises(self):
        with self.assertRaises(ps3_eval.DanglingId):
            ps3_eval.Collection(docs('''
            kind: filament
            id: x
            name: X
            inherits: ['*missing*']
            ''')).check_references()


import subprocess  # noqa: E402

import build_ps3_vendor as bpv  # noqa: E402

VENDOR_YAML = '''
kind: vendor
id: PrusaResearch
name: Prusa Research FFF
version: {version}
features:
  printer:
    supports_high_flow_nozzle: {{default: true}}
    supports_non_high_flow_nozzle: {{default: true}}
    supports_025_nozzle: {{default: true}}
    supports_04_nozzle: {{default: true}}
    supports_06_nozzle: {{default: true}}
    multi_extruder: {{default: false}}
  tool:
    nozzle_high_flow: {{default: false}}
    nozzle_diameter: {{default: 0.4}}
  sheet:
    cold: {{default: false}}
---
kind: printer
technology: FFF
name: Prusa CORE One INDX
id: COREONE_INDX
model: {{base_model: COREONE_INDX, model: COREONE_INDX}}
tool_count: 4
features:
  supports_non_high_flow_nozzle: {{default: false}}
  supports_025_nozzle: {{default: false}}
  supports_06_nozzle: {{default: false}}
  multi_extruder: {{default: true}}
visual: {{bed_model: bed.stl}}
---
kind: sheet
id: satin
name: Satin
type: satin
'''

TOOL_FILE = '''id: '*common COREONE_INDX*'
kind: tool_print
variants:
- condition: printer.model=="COREONE_INDX"
  id: common1
  values:
    retract_length: 0.7
---
id: main
kind: tool_print
inherits:
- '*common COREONE_INDX*'
variants:
- condition: printer.model=="COREONE_INDX"
  id: indx
  values:
    perimeters: 2
  variants:
  - condition: tool.nozzle_high_flow
    id: hf
    values:
      max_print_speed: 300
    variants:
    - condition: tool.nozzle_diameter == 0.4
      id: hf04
      variants:
      - condition: print.layer_height == 0.10
        name: 0.10mm Fine @COREONEINDX 0.4HF
        id: fine
        values:
          perimeters: 3
# # 0.6 high-flow nozzle, commented out upstream
    # - condition: tool.nozzle_diameter == 0.6
      # id: x
'''


class Generator(Scratch):
    def prusa_dir(self, version='1.0.20'):
        d = os.path.join(self.dir, 'PrusaResearch')
        self.write('PrusaResearch/vendor.yaml', VENDOR_YAML.format(version=version))
        self.write('PrusaResearch/preset-tool-coreone-indx.yaml', TOOL_FILE)
        return d

    def test_refuses_wrong_pin_and_writes_nothing(self):
        prusa = self.prusa_dir(version='1.0.21')
        out = os.path.join(self.dir, 'out', 'hyigerINDX')
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'build_ps3_vendor.py'),
                            prusa, os.path.join(self.dir, 'none.ini'), out],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('refusing:', r.stderr)
        self.assertIn('pinned to 1.0.20', r.stderr)
        self.assertFalse(os.path.exists(out))

    def test_vendor_yaml_shape(self):
        p = bpv.Prusa(self.prusa_dir())
        docs = [d for d in yaml.safe_load_all(bpv.build_vendor_yaml(p)) if d]
        cfg = [d for d in docs if d['kind'] == 'printer_config']
        self.assertEqual(len(cfg), 1)
        self.assertEqual((cfg[0]['tool_count'], cfg[0]['tools']), (8, [{'tool': '0.4HF'}]))
        self.assertEqual({d['id'] for d in docs if d['kind'] == 'tool'}, set(bpv.TOOLS))
        printer = next(d for d in docs if d['kind'] == 'printer')
        self.assertEqual((printer['id'], printer['tool_count']), (bpv.PRINTER_DEF_ID, 8))
        self.assertTrue(printer['features']['supports_025_nozzle']['default'])
        self.assertEqual(docs[0]['id'], bpv.VENDOR_ID)

    def test_tool_print_lands_where_meant(self):
        p = bpv.Prusa(self.prusa_dir())
        text = p.text['preset-tool-coreone-indx.yaml']
        leaf07 = {'condition': 'print.layer_height == 0.07', 'name': 'A', 'id': 'a',
                  'values': {'perimeters': 3}}
        hf06 = {'condition': 'tool.nozzle_diameter == 0.6', 'id': 'b',
                'variants': [{'condition': 'print.layer_height == 0.2', 'name': 'B', 'id': 'bb'}]}
        nonhf = {'condition': 'not tool.nozzle_high_flow', 'id': 'c',
                 'variants': [{'condition': 'tool.nozzle_diameter == 0.25', 'id': 'cc',
                               'variants': [{'condition': 'print.layer_height == 0.07',
                                             'name': 'C', 'id': 'ccc'}]}]}
        out = bpv.patch_tool(text, p.docs['preset-tool-coreone-indx.yaml'],
                             leaf07, hf06, nonhf, [], [])
        coll = ps3_eval.Collection([d for d in yaml.safe_load_all(out) if d])
        names = lambda e: [x.name for x in coll.presets([e])]  # noqa: E731
        self.assertEqual(names(tool(0.4, True, 0.07)), ['A'])
        self.assertEqual(names(tool(0.6, True, 0.2)), ['B'])
        self.assertEqual(names(tool(0.25, False, 0.07)), ['C'])
        self.assertEqual(names(tool(0.4, True, 0.1)), ['0.10mm Fine @COREONEINDX 0.4HF'])

    def test_tool_print_refuses_new_upstream_size(self):
        p = bpv.Prusa(self.prusa_dir())
        docs = p.docs['preset-tool-coreone-indx.yaml']
        docs[1]['variants'][0]['variants'][0]['variants'].append(
            {'condition': 'tool.nozzle_diameter == 0.6', 'id': 'new'})
        with self.assertRaises(SystemExit) as e:
            bpv.tool_tree(docs)
        self.assertIn('HF nozzles other than 0.4', str(e.exception))


class Closure(Scratch):
    def test_refuses_dangling_inherits(self):
        self.write('P/vendor.yaml', VENDOR_YAML.format(version='1.0.20'))
        self.write('P/preset-tool-coreone-indx.yaml', TOOL_FILE)
        self.write('P/preset-print-coreone-indx.yaml',
                   'kind: print\nid: p\ninherits: ["*missing*"]\n')
        self.write('P/preset-printer-coreone-indx.yaml', 'kind: printer\nid: q\n')
        p = bpv.Prusa(os.path.join(self.dir, 'P'))
        with self.assertRaises(SystemExit) as e:
            bpv.select_files(p)
        self.assertIn("'*missing*' is defined nowhere", str(e.exception))


class Translation(unittest.TestCase):
    DEFS = ps3_yaml.load_keydefs(os.path.join(TOOLS, 'ps3_keydefs.tsv'))

    def test_pa_translation(self):
        self.assertEqual(bpv.translate_start_gcode('M572 S0.02 ; Pressure advance\nM573 R', 'x'),
                         {'pressure_advance': 'automatic_calibration',
                          'pressure_advance_value': 0.02,
                          'start_filament_gcode': '; Filament g-code'})
        self.assertEqual(bpv.translate_start_gcode(
            'M572 S0.015 ; Pressure advance\nM573 R\n\nM906 P2 ; set E current', 'x'),
            {'pressure_advance': 'automatic_calibration', 'pressure_advance_value': 0.015,
             'start_filament_gcode': 'M906 P2 ; set E current'})
        g = 'M572 S0 ; Pressure advance\n\nM906 P2 ; set E current'
        self.assertEqual(bpv.translate_start_gcode(g, 'x'),
                         {'pressure_advance': 'disabled', 'start_filament_gcode': g})
        for bad in ('M900 K0.02', 'M572 S0.02 ; Pressure advance\nM573 R\nM572 S0.03',
                    'M572 S0 ; Pressure advance\nM573'):
            with self.assertRaises(SystemExit):
                bpv.translate_start_gcode(bad, 'x')

    def test_unknown_print_key_refuses(self):
        with self.assertRaises(SystemExit) as e:
            bpv.port_print({'perimeters': '3', 'made_up_key': '1'}, self.DEFS, {}, 'x')
        self.assertIn("'made_up_key'", str(e.exception))

    def test_print_rename_and_support_pair(self):
        vals, report = bpv.port_print(
            {'first_layer_infill_speed': '70', 'support_material': '1',
             'support_material_auto': '0', 'perimeters': '3', 'fill_density': '20%'},
            self.DEFS, {'support_material': 'enforcers_only'}, 'x')
        self.assertEqual(vals, {'first_layer_solid_infill_speed': 70, 'perimeters': 3,
                                'fill_density': '20%'})
        self.assertEqual(report, [])

    def test_filament_rename_percent_and_nil(self):
        vals, renamed = bpv.port_filament(
            {'filament_retract_length': '0.8', 'filament_retract_before_wipe': '20',
             'filament_wipe': 'nil', 'filament_settings_id': '""', 'temperature': '250',
             'start_filament_gcode': '"M572 S0.02 ; Pressure advance\\nM573 R"'},
            self.DEFS, 'x')
        self.assertEqual(vals, {'retract_length': 0.8, 'retract_before_wipe': '20%',
                                'temperature': 250, 'pressure_advance': 'automatic_calibration',
                                'pressure_advance_value': 0.02,
                                'start_filament_gcode': '; Filament g-code'})
        self.assertEqual(sorted(renamed), ['filament_retract_before_wipe', 'filament_retract_length'])

    def test_unknown_filament_key_refuses(self):
        with self.assertRaises(SystemExit):
            bpv.port_filament({'filament_made_up': '1'}, self.DEFS, 'x')


if __name__ == '__main__':
    unittest.main()
