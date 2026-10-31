#!/usr/bin/env python3
"""
Build the PrusaSlicer 3 vendor `hyigerINDX`: one CORE One INDX 8T printer whose
tools 1-7 are Prusa's HF0.4 and whose tool 8 may be HF0.4, standard 0.4, HF0.6 or
standard 0.25.

    PS3=~/Library/Application\\ Support/PrusaSlicer3-dev/presets/local/prusa-research-fff/PrusaResearch
    INI=<a PrusaResearch.ini whose config_version is 2.5.12>
    python3 tools/build_ps3_vendor.py "$PS3" "$INI" ps3/hyigerINDX

Why a vendor of our own. In PrusaSlicer 3.0.0-alpha12 only a vendor.yaml can
declare tools and printer configurations, a profile update replaces Prusa's
installed files, and presets resolve inheritance only inside their own vendor.
So this tool copies the part of Prusa's profiles an INDX can use, verbatim,
into a vendor that updates do not touch, and adds what Prusa does not ship.

What it writes, and where each value comes from:

  vendor.yaml       generated: Prusa's feature definitions and sheets, Prusa's INDX
                    printer definition with PRINTER_FEATURE_EDITS, the TOOLS, and
                    one printer configuration.
  copied files      Prusa 1.0.20 preset files an INDX reaches, plus every file they
                    inherit from (select_files()), unchanged apart from a header,
                    except:
    printer preset  renamed to PRINTER_NAME, with a new id;
    tool_print      new presets appended (TOOL_PRINT below);
    filaments       a Prusa material that falls through to an untuned preset on a
                    tool-8 nozzle Prusa has no profile for gets a '*quit*' variant
                    there (FALLTHROUGH_WHY).
  ported filaments  preset-filament-hyiger-*.yaml: this repo's committed 2.9
                    filament bundles for the tool-8 sizes Prusa's PS3 profiles do
                    not cover, translated key by key (port_filament()).

It refuses, writing nothing, on: a different Prusa or PrusaResearch.ini version
than pinned; a key PrusaSlicer 3 would drop; a Prusa file shaped differently
from what this tool was written against; a dangling preset id; or a printer that
would not offer the layer heights LAYERS_EXPECTED says it should.
"""
import argparse
import copy
import glob
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ps3_eval  # noqa: E402
import ps3_yaml  # noqa: E402
from build_print_profiles import load as load_ini, resolve as resolve_ini  # noqa: E402

REPO = os.path.dirname(HERE)
KEYDEFS = os.path.join(HERE, 'ps3_keydefs.tsv')

PS3_PIN = '1.0.20'      # Prusa Research FFF profile version (vendor.yaml `version:`)
INI_PIN = '2.5.12'      # PrusaResearch.ini the repo's 2.9 artifacts are built from
VALIDATED = False       # nothing here has been printed with PrusaSlicer 3 yet

VENDOR_ID = 'hyigerINDX'
VENDOR_NAME = 'hyiger INDX'
VENDOR_VERSION = '0.1.0'
REPO_DIR = 'hyiger-indx'          # <datadir>/presets/local/<REPO_DIR>/<VENDOR_ID>
PRINTER_NAME = 'Prusa CORE One INDX 8T JRH'   # the name the owner asked for
PRINTER_DEF_ID = 'COREONE_INDX_JRH'
PRINTER_DEF_NAME = 'Prusa CORE One INDX JRH'  # shown as "<this> 8T <tools>"
CONFIG_ID = 'c1indx-8t-jrh'
PRUSA_PRINTER_VARIANT = ('Prusa CORE One INDX', '8gQ0vRqlQQ++NPeD9RwmhQ')

# id -> (name, nozzle_diameter, nozzle_high_flow, why)
TOOLS = {
    '0.4HF': ('0.4 HF', 0.4, True, "Prusa's stock INDX tool: tools 1-7, and tool 8's default"),
    '0.4': ('0.4', 0.4, False, 'Bondtech INDX passive tool 0.4 for flexibles: standard flow, '
                               'no CHT (bondtech.se/product/indx-passive-tool-0-4mm-flexibles)'),
    '0.6HF': ('0.6 HF', 0.6, True, 'Bondtech INDX CHT tool 0.6: high-flow CHT '
                                   '(bondtech.se/product/bondtech-indx-cht-tool)'),
    '0.25': ('0.25', 0.25, False, 'Bondtech INDX fine nozzle 0.25: standard single bore '
                                  '(bondtech.se/product/bondtech-indx-fine-nozzle)'),
}
T8_SIZES = [('0.4HF',), ('0.4',), ('0.6HF',), ('0.25',)]

PRINTER_FEATURE_EDITS = {
    'supports_non_high_flow_nozzle': (True, 'tool 8 may hold the standard 0.4 or 0.25'),
    'supports_025_nozzle': (True, 'tool 8 may hold the 0.25'),
    'supports_06_nozzle': (True, 'tool 8 may hold the HF0.6'),
}

# Print presets each tool-8 nozzle must leave selectable with HF0.4 on tools 1-7.
# A layer height survives only if every tool has a tool_print preset at it
# (PresetEvaluator.cpp:668-683), so these are the intersections.
LAYERS_EXPECTED = {
    '0.4HF': (0.10, 0.15, 0.20, 0.25),
    '0.4': (0.10, 0.15, 0.20, 0.25),
    '0.6HF': (0.20, 0.25),
    '0.25': (0.07, 0.10, 0.15),
}

# --- tool_print sources --------------------------------------------------------

HELD_007 = ('0.10mm Fine @COREONEINDX 0.4HF',
            "tools 1-7 need a tool_print at 0.07 or a 0.25 tool 8 cannot print 0.07 "
            "(every tool needs one, PresetEvaluator.cpp:668-683); held from Prusa's "
            "nearest rung, 0.10 Fine, whose speeds give less flow at 0.07, not more")
STD04_WHY = ("Prusa's HF0.4 tool_print presets, unchanged: the repo's 2.9 standard-0.4 "
             "prints are Prusa's HF0.4 prints with only the binding changed")
SRC_025 = [('0.07mm Extra Fine @COREONEINDX 0.25', '0.07mm Extra Fine @COREONEINDX 0.25'),
           ('0.10mm Fine ★ @COREONEINDX 0.25', '0.10mm Fine ★ @COREONEINDX 0.25'),
           ('0.15mm Detail @COREONEINDX 0.25', '0.15mm Detail @COREONEINDX 0.25')]
SRC_025_WHY = ("Prusa's released INDX 0.25 prints in PrusaResearch.ini %s, test printed "
               "with this repo's 2.9 0.25 setup; Prusa's PS3 0.25 tool_print is still "
               "commented out" % INI_PIN)
# 0.32 Draft is not ported: tools 1-7 cannot print 0.32 (HF0.4 max_layer_height 0.30).
SRC_06 = [('0.20mm Detail @COREONEINDX HF0.6', '0.20mm Detail @COREONEINDX 0.6HF'),
          ('0.25mm Balanced ★ @COREONEINDX HF0.6', '0.25mm Balanced ★ @COREONEINDX 0.6HF')]
SRC_06_WHY = ("this repo's HF0.6 print profiles (vendor/hyigerINDX.ini), test printed; "
              "0.32 Draft is left out because HF0.4 on tools 1-7 cannot print 0.32")

# 2.9 print keys that are not carried, and why. Any other 2.9 key must be
# per-tool in PrusaSlicer 3 or print-only (reported, see print_only_report()).
PRINT_DROP = {
    'compatible_printers': '2.9 bookkeeping; PrusaSlicer 3 binds by YAML condition',
    'compatible_printers_condition': '2.9 bookkeeping; PrusaSlicer 3 binds by YAML condition',
    'print_settings_id': '2.9 bookkeeping',
    'notes': '2.9 bookkeeping',
    'layer_height': 'becomes the condition print.layer_height == <h>',
    'support_material_auto': 'folded into support_material, see SUPPORT_29',
    'top solid_infill_speed': "Prusa's typo in *INDX_025_common*; 2.9 drops it on load, "
                              "so the printed 0.25 profiles never used it",
}
# 2.9 spreads one choice over two keys: support_material (generate supports) and
# support_material_auto (unchecked: "supports will be generated inside the
# 'Support Enforcer' volumes only", PrintConfig.cpp at version_2.9.6). PrusaSlicer 3
# has one enum, support_material: none / enforcers_only / everywhere.
SUPPORT_29 = {('0', '0'): 'none', ('0', '1'): 'none', ('1', '0'): 'enforcers_only',
              ('1', '1'): 'everywhere'}
PRINT_RENAME = {
    'first_layer_infill_speed': ('first_layer_solid_infill_speed',
                                 '2.9.6 labels it "First layer solid infill speed" '
                                 '(PrintConfig.cpp:1731-1737); PrusaSlicer 3 has the '
                                 'solid key under that name, and a new sparse-infill one'),
}

# --- filament sources ------------------------------------------------------------

# (tool id, bundle, name tag, PS3 condition)
FIL_SOURCES = [
    ('0.6HF', 'filaments/hyigerINDX-0.6-filaments.ini', 'HF0.6',
     'tool.nozzle_diameter == 0.6 and tool.nozzle_high_flow'),
    ('0.25', 'filaments/hyigerINDX-0.25-filaments.ini', '0.25',
     'tool.nozzle_diameter == 0.25 and not tool.nozzle_high_flow'),
    ('0.4', 'filaments/hyigerINDX-0.4-filaments.ini', '0.4',
     'tool.nozzle_diameter == 0.4 and not tool.nozzle_high_flow'),
]
FIL_DROP = {
    'compatible_printers': '2.9 bookkeeping; PrusaSlicer 3 binds by YAML condition',
    'compatible_printers_condition': '2.9 bookkeeping; PrusaSlicer 3 binds by YAML condition',
    'compatible_prints': '2.9 bookkeeping',
    'compatible_prints_condition': '2.9 binding to print notes (HT_HOTEND_HT); PrusaSlicer 3 '
                                   'binds filaments to tools, and has no such key for them',
    'filament_settings_id': '2.9 bookkeeping',
    'inherits': '2.9 bookkeeping',
    'filament_load_time': 'MMU load-time estimate; no such key in PrusaSlicer 3',
    'filament_unload_time': 'MMU unload-time estimate; no such key in PrusaSlicer 3',
}
PA_LINE = re.compile(r'^M572 S(?P<pa>[0-9.]+) ; Pressure advance(?P<rest>.*)$', re.S)
PS3_FILAMENT_GCODE = '; Filament g-code'   # what Prusa's PS3 INDX filaments write

FALLTHROUGH_WHY = ("Prusa's INDX branch for this material has no '*quit*', so on a "
                   "nozzle Prusa has no INDX profile for it falls through to a preset "
                   "tuned for none; hidden there, at {sizes}")


def refuse(msg):
    sys.exit('refusing: ' + msg)


# --- values ----------------------------------------------------------------------

def ini_unescape(raw):
    """A PrusaSlicer INI string value: quoted ones use C-style escapes."""
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        out, i, s = [], 0, raw[1:-1]
        while i < len(s):
            if s[i] == '\\' and i + 1 < len(s):
                out.append({'n': '\n', 't': '\t', 'r': '\r'}.get(s[i + 1], s[i + 1]))
                i += 2
            else:
                out.append(s[i])
                i += 1
        return ''.join(out)
    return raw


# PrusaSlicer 3 Percentage keys whose 2.9 option is coPercents, which reads a bare
# number as a percentage ("The float's deserialize function shall ignore the
# trailing optional %", ConfigOptionPercentsTempl, Config.hpp at version_2.9.6).
# Only these get a '%' added; a bare number for any other Percentage key refuses,
# since a 2.9 float that PrusaSlicer 3 made a percentage would mean something else.
PERCENT_IN_29 = {'retract_before_wipe'}


def _number(text):
    if re.fullmatch(r'-?\d+', text):
        return int(text)
    return float(text)


def typed(key, raw, defs, strict=True):
    """A 2.9 INI string as the YAML value PrusaSlicer 3 expects for key. With
    strict=False (report-only use) an untranslatable value comes back as text."""
    t = defs[key].type
    s = ini_unescape(raw)
    if not strict:
        try:
            return typed(key, raw, defs)
        except SystemExit:
            return s
    try:
        if t in ('double', 'int'):
            v = _number(s)
            if t == 'int' and v != int(v):
                raise ValueError('not an integer')
            return int(v) if t == 'int' else v
        if t == 'bool':
            if s not in ('0', '1'):
                raise ValueError(s)
            return s == '1'
        if t in ('FloatOrPercentage', 'Percentage'):
            if s.endswith('%'):
                return s
            if t == 'Percentage' and key in PERCENT_IN_29:
                return '%s%%' % _number(s)
            if t == 'Percentage':
                raise ValueError(s)
            return _number(s)
        if t == 'EnumWrapper':
            if s not in defs[key].enum:
                raise ValueError('%r not in %s' % (s, defs[key].enum))
            return s
        if t == 'std::string':
            return s
        if t == 'std::optional<int>':
            return int(s)
    except ValueError as e:
        refuse('%s = %r does not read as %s (%s)' % (key, raw, t, e))
    refuse('%s has PrusaSlicer 3 type %s, which this tool does not translate' % (key, t))


# --- Prusa's installed profiles ------------------------------------------------------

class Prusa:
    def __init__(self, path):
        self.path = path
        vy = os.path.join(path, 'vendor.yaml')
        if not os.path.isfile(vy):
            refuse('%s has no vendor.yaml' % path)
        self.vendor = ps3_yaml.load_docs(vy)
        version = str(self.vendor[0].get('version'))
        if version != PS3_PIN:
            refuse('Prusa profiles are %s, this tool is pinned to %s: re-pinning is a '
                   'reviewed change (edit PS3_PIN and check the result)' % (version, PS3_PIN))
        self.text, self.docs = {}, {}
        for f in sorted(glob.glob(os.path.join(path, 'preset-*.yaml'))):
            name = os.path.basename(f)
            with open(f, encoding='utf-8') as fh:
                self.text[name] = fh.read()
            self.docs[name] = [d for d in ps3_yaml_load_all(self.text[name])]

    def vendor_doc(self, kind, id_=None):
        for d in self.vendor:
            if d.get('kind') == kind and (id_ is None or d.get('id') == id_):
                return d
        refuse('Prusa vendor.yaml has no %s %s' % (kind, id_ or ''))

    def kind_docs(self, kind, files):
        return [d for f in files for d in self.docs[f] if d.get('kind') == kind]


def ps3_yaml_load_all(text):
    import yaml
    return [d for d in yaml.safe_load_all(text) if d is not None]


def _refs(node):
    for key in ('inherits', 'unconditional_inherits'):
        for r in node.get(key) or []:
            yield key, str(r)
    for v in node.get('variants') or []:
        yield from _refs(v)


def _ids(node, top=True):
    if node.get('id') is not None:
        yield ('top' if top else 'node'), str(node['id'])
    for v in node.get('variants') or []:
        yield from _ids(v, False)


SEED_FILES = ('preset-printer-coreone-indx.yaml', 'preset-print-coreone-indx.yaml',
              'preset-tool-coreone-indx.yaml')


def select_files(prusa):
    """The INDX printer, print and tool files, every filament file that names the
    INDX, and the closure of all of them over inherits / unconditional_inherits."""
    for f in SEED_FILES:
        if f not in prusa.docs:
            refuse('Prusa profiles have no %s' % f)
    chosen = set(SEED_FILES) | {f for f in prusa.docs if f.startswith('preset-filament-')
                                and 'COREONE_INDX' in prusa.text[f]}
    where = {}
    for f, docs in prusa.docs.items():
        for d in docs:
            for scope, i in _ids(d):
                where.setdefault((d.get('kind'), scope, i), f)
                where.setdefault((d.get('kind'), 'node', i), f)
    changed = True
    while changed:
        changed = False
        for f in sorted(chosen):
            for d in prusa.docs[f]:
                for key, r in _refs(d):
                    scope = 'top' if key == 'inherits' else 'node'
                    src = where.get((d.get('kind'), scope, r))
                    if src is None:
                        refuse('%s: %s %r is defined nowhere in Prusa\'s profiles' % (f, key, r))
                    if src not in chosen:
                        chosen.add(src)
                        changed = True
    return sorted(chosen)


# --- the printer ------------------------------------------------------------------

def printer_features(prusa):
    pf = {k: v['default'] for k, v in prusa.vendor[0]['features']['printer'].items()}
    indx = prusa.vendor_doc('printer', 'COREONE_INDX')
    pf.update({k: v['default'] for k, v in (indx.get('features') or {}).items()})
    for k, (v, _) in PRINTER_FEATURE_EDITS.items():
        if k not in pf:
            refuse('Prusa declares no printer feature %s' % k)
        pf[k] = v
    return pf


def envs(prusa, tool_ids, layer=None):
    """Condition variables for each tool of the JRH printer (ValueMapBuilder.cpp)."""
    pf = printer_features(prusa)
    sheet = prusa.vendor_doc('sheet', 'satin')
    tf = {k: v['default'] for k, v in prusa.vendor[0]['features']['tool'].items()}
    base = {'printer.model': 'COREONE_INDX', 'printer.base_model': 'COREONE_INDX',
            'printer.tool_count': 8, 'feeder.model': '', 'feeder.base_model': '',
            'sheet.type': sheet.get('type')}
    base.update({'printer.' + k: v for k, v in pf.items()})
    base.update({'sheet.' + k: v['default'] for k, v in prusa.vendor[0]['features']['sheet'].items()})
    base.update({'sheet.' + k: v for k, v in (sheet.get('features') or {}).items()})
    out = []
    for t in tool_ids:
        _, dia, hf, _ = TOOLS[t]
        e = dict(base)
        e.update({'tool.' + k: v for k, v in tf.items()})
        e.update({'tool.nozzle_diameter': dia, 'tool.nozzle_high_flow': hf})
        if layer is not None:
            e['print.layer_height'] = layer
        out.append(e)
    return out


def build_vendor_yaml(prusa):
    vend = copy.deepcopy(prusa.vendor[0])
    vend['id'], vend['name'], vend['version'] = VENDOR_ID, VENDOR_NAME, VENDOR_VERSION
    vend['printer_families'] = [{'label': 'Core ONE INDX JRH', 'base_model': 'COREONE_INDX',
                                 'printer_configs_order': [CONFIG_ID]}]
    printer = copy.deepcopy(prusa.vendor_doc('printer', 'COREONE_INDX'))
    printer['id'], printer['name'], printer['tool_count'] = PRINTER_DEF_ID, PRINTER_DEF_NAME, 8
    printer.setdefault('features', {})
    for k, (v, _) in PRINTER_FEATURE_EDITS.items():
        printer['features'][k] = {'default': v}
    docs = [vend, printer]
    for tid, (name, dia, hf, _) in TOOLS.items():
        docs.append({'kind': 'tool', 'technology': 'FFF', 'id': tid, 'name': name,
                     'condition': 'printer.base_model == "COREONE_INDX"',
                     'features': {'nozzle_high_flow': {'default': hf},
                                  'nozzle_diameter': {'default': dia}}})
    docs += [copy.deepcopy(d) for d in prusa.vendor if d.get('kind') == 'sheet']
    docs.append({'kind': 'printer_config', 'id': CONFIG_ID, 'name': PRINTER_NAME,
                 'printer': PRINTER_DEF_ID, 'legacy_printer_model': ['COREONE_INDX8T'],
                 'tool_count': 8, 'tools': [{'tool': '0.4HF'}], 'sheet': 'satin'})
    head = [banner(),
            '# vendor.yaml for %s, generated by tools/build_ps3_vendor.py from Prusa' % VENDOR_ID,
            '# Research FFF profiles %s. The vendor features and sheets are Prusa\'s;' % PS3_PIN,
            '# the printer is Prusa\'s COREONE_INDX definition with tool_count 8 and:']
    head += ['#   %s: %s (%s)' % (k, v, why) for k, (v, why) in PRINTER_FEATURE_EDITS.items()]
    head.append('# Tools (all bound to this printer):')
    head += ['#   %s: %s' % (tid, t[3]) for tid, t in TOOLS.items()]
    head.append('# printer_config %s: tools 1-8 start as 0.4HF; pick tool 8 per session '
                '(alpha12 does not save it, prusa3d/PrusaSlicer#15737).' % CONFIG_ID)
    text = '\n'.join(head) + '\n' + '---\n'.join(ps3_yaml.dump(d) for d in docs)
    if ps3_yaml_load_all(text) != docs:
        refuse('vendor.yaml does not read back as written')
    return text


def patch_printer(text):
    name, pid = PRUSA_PRINTER_VARIANT
    old = '  name: %s\n  id: %s\n' % (name, pid)
    if text.count(old) != 1:
        refuse('Prusa\'s INDX printer preset no longer has exactly one "%s" variant (%s)'
               % (name, pid))
    new = '  name: %s\n  id: %s\n' % (PRINTER_NAME, ps3_yaml.new_id('printer', PRINTER_NAME))
    head = [banner(), '# Prusa Research FFF %s preset-printer-coreone-indx.yaml, copied by' % PS3_PIN,
            '# tools/build_ps3_vendor.py with one change: the "%s" printer is' % name,
            '# renamed "%s" with a new id, so it is not taken for Prusa\'s own.' % PRINTER_NAME]
    return '\n'.join(head) + '\n' + text.replace(old, new)


def banner():
    if VALIDATED:
        return '# Test printed with PrusaSlicer 3.'
    return ('# UNVALIDATED: nothing in this vendor has been printed with PrusaSlicer 3 yet.')


# --- tool_print -------------------------------------------------------------------

def _find(nodes, cond):
    hits = [n for n in nodes if n.get('condition') == cond]
    if len(hits) != 1:
        refuse('Prusa\'s INDX tool_print has %d nodes with condition %r, expected 1'
               % (len(hits), cond))
    return hits[0]


def tool_tree(docs):
    """(main doc, INDX node, HF node, HF0.4 node), refusing on any other shape."""
    mains = [d for d in docs
             if any(v.get('condition') == 'printer.model=="COREONE_INDX"'
                    and any(w.get('condition') == 'tool.nozzle_high_flow'
                            for w in v.get('variants') or [])
                    for v in d.get('variants') or [])]
    if len(mains) != 1:
        refuse('Prusa\'s INDX tool_print file is not shaped as this tool expects')
    main = mains[0]
    indx = _find(main['variants'], 'printer.model=="COREONE_INDX"')
    if len(indx['variants']) != 1:
        refuse('Prusa\'s INDX tool_print now has a non-HF branch: review before porting')
    hf = _find(indx['variants'], 'tool.nozzle_high_flow')
    if len(hf['variants']) != 1:
        refuse('Prusa\'s INDX tool_print now has HF nozzles other than 0.4: review first')
    hf04 = _find(hf['variants'], 'tool.nozzle_diameter == 0.4')
    if any(not str(v.get('condition', '')).startswith('print.layer_height == ')
           for v in hf04['variants']):
        refuse('Prusa\'s HF0.4 tool_print leaves are not all layer-height variants')
    return main, indx, hf, hf04


def port_print(resolved, defs, base_print, label):
    """A resolved 2.9 print profile as tool_print values, plus what was not carried."""
    values, report = {}, []
    resolved = dict(resolved)
    if 'support_material' in resolved:
        pair = (resolved['support_material'], resolved.get('support_material_auto', '1'))
        if pair not in SUPPORT_29:
            refuse('%s: support_material / support_material_auto = %s / %s' % ((label,) + pair))
        resolved['support_material'] = SUPPORT_29[pair]
    for k, raw in resolved.items():
        if k in PRINT_DROP:
            continue
        key = k
        if k in PRINT_RENAME:
            key = PRINT_RENAME[k][0]
        if key not in defs:
            refuse('%s: 2.9 key %r is not a PrusaSlicer 3 key and has no PRINT_DROP reason'
                   % (label, k))
        if not ps3_yaml.tool_key(defs, key):
            v = typed(key, raw, defs, strict=False)
            if key not in base_print:
                report.append('%s: %s = %r not carried (print-only in PrusaSlicer 3, and '
                              'Prusa\'s PS3 print does not set it)' % (label, key, v))
            elif base_print[key] != v:
                report.append('%s: %s = %r not carried (print-only in PrusaSlicer 3, which '
                              'uses %r)' % (label, key, v, base_print[key]))
            continue
        values[key] = typed(key, raw, defs)
    return values, report


def tool_print_additions(prusa, ini, repo_bundle, defs, print_base):
    main, indx, hf, hf04 = tool_tree(prusa.docs['preset-tool-coreone-indx.yaml'])
    notes, report = [], []
    by_name = {v.get('name'): v for v in hf04['variants']}
    if HELD_007[0] not in by_name:
        refuse('Prusa has no %r to hold the HF0.4 0.07 preset from' % HELD_007[0])
    name07 = '0.07mm Extra Fine @COREONEINDX 0.4HF'
    leaf07 = {'condition': 'print.layer_height == 0.07', 'name': name07,
              'id': ps3_yaml.new_id('tool_print', name07),
              'values': copy.deepcopy(by_name[HELD_007[0]].get('values') or {})}
    notes.append('%s: values of %s; %s' % (name07, HELD_007[0], HELD_007[1]))

    std_leaves = []
    for v in hf04['variants']:
        leaf = copy.deepcopy(v)
        if not str(leaf.get('name', '')).endswith(' 0.4HF'):
            refuse('Prusa HF0.4 tool_print %r is not named "... 0.4HF"' % leaf.get('name'))
        leaf['name'] = leaf['name'][:-len(' 0.4HF')] + ' 0.4'
        leaf['id'] = ps3_yaml.new_id('tool_print', leaf['name'])
        std_leaves.append(leaf)
        notes.append('%s: %s' % (leaf['name'], STD04_WHY))
    std04 = {'condition': 'tool.nozzle_diameter == 0.4', 'id': ps3_yaml.new_id('tool_print', 'std 0.4'),
             'values': dict(hf.get('values') or {}, **(hf04.get('values') or {})),
             'variants': std_leaves}

    def leaves(sections, sources, why, t8):
        out = []
        for src, name in sources:
            r = dict(resolve_ini(sections, 'print:' + src))
            layer = float(ini_unescape(r['layer_height']))
            vals, rep = port_print(r, defs, print_base(t8, layer), name)
            report.extend(rep)
            out.append({'condition': 'print.layer_height == %s' % ini_unescape(r['layer_height']),
                        'name': name, 'id': ps3_yaml.new_id('tool_print', name), 'values': vals})
            notes.append('%s: %s %r; %s' % (name, 'from', src, why))
        return out

    n025 = {'condition': 'tool.nozzle_diameter == 0.25', 'id': ps3_yaml.new_id('tool_print', '0.25'),
            'variants': leaves(ini, SRC_025, SRC_025_WHY, '0.25')}
    hf06 = {'condition': 'tool.nozzle_diameter == 0.6', 'id': ps3_yaml.new_id('tool_print', 'HF0.6'),
            'variants': leaves(repo_bundle, SRC_06, SRC_06_WHY, '0.6HF')}
    nonhf = {'condition': 'not tool.nozzle_high_flow', 'id': ps3_yaml.new_id('tool_print', 'non-HF'),
             'variants': [std04, n025]}
    return leaf07, hf06, nonhf, notes, report


def patch_tool(text, docs, leaf07, hf06, nonhf, notes, report):
    """Append the new presets at the end of Prusa's tool file, where YAML nests
    them by indentation, and prove by re-reading that they landed where meant."""
    expected = copy.deepcopy(docs)
    _, indx, hf, hf04 = tool_tree(expected)
    hf04['variants'].append(leaf07)
    hf['variants'].append(hf06)
    indx['variants'].append(nonhf)
    tail = ('\n# --- added by tools/build_ps3_vendor.py ---\n'
            + ps3_yaml.dump([leaf07], 6) + ps3_yaml.dump([hf06], 4) + ps3_yaml.dump([nonhf], 2))
    out = text.rstrip('\n') + '\n' + tail
    if ps3_yaml_load_all(out) != expected:
        refuse('appending to Prusa\'s tool_print file does not nest as intended: its last '
               'node is no longer the HF0.4 layer list')
    head = [banner(), '# Prusa Research FFF %s preset-tool-coreone-indx.yaml, copied by' % PS3_PIN,
            '# tools/build_ps3_vendor.py, with tool_print presets appended at the end:']
    head += ['#   ' + n for n in notes]
    if report:
        head.append('# 2.9 values these presets cannot carry, because PrusaSlicer 3 has the key')
        head.append('# for the whole print only (one value for all tools):')
        head += ['#   ' + r for r in report]
    return '\n'.join(head) + '\n' + out


# --- filaments ----------------------------------------------------------------------

def port_filament(section, defs, label):
    """A flattened 2.9 filament section as PrusaSlicer 3 filament values."""
    values, renamed = {}, []
    for k, raw in section.items():
        if k in FIL_DROP or raw == 'nil':
            continue
        if k == 'start_filament_gcode':
            values.update(translate_start_gcode(ini_unescape(raw), label))
            continue
        key = k
        if key not in defs and key.startswith('filament_') and ps3_yaml.filament_key(defs, key[9:]):
            key = key[9:]
            renamed.append(k)
        if not ps3_yaml.filament_key(defs, key):
            refuse('%s: 2.9 filament key %r is not a PrusaSlicer 3 filament key and has no '
                   'FIL_DROP reason' % (label, k))
        values[key] = typed(key, raw, defs)
    return values, renamed


def translate_start_gcode(gcode, label):
    """2.9 INDX filaments set pressure advance in their start G-code. PrusaSlicer 3
    has keys for it, and `automatic_calibration` emits the same two lines,
    M572 S<value> then M573 R (GCode.cpp:4075-4084, GCodeWriter.cpp:623-668)."""
    m = PA_LINE.match(gcode)
    if not m:
        refuse('%s: start_filament_gcode %r is not "M572 S<pa> ; Pressure advance..."'
               % (label, gcode))
    pa, rest = m.group('pa'), m.group('rest')
    if rest.startswith('\nM573 R'):
        tail = rest[len('\nM573 R'):].strip('\n')
        if 'M572' in tail or 'M573' in tail:
            refuse('%s: start_filament_gcode sets pressure advance twice: %r' % (label, gcode))
        return {'pressure_advance': 'automatic_calibration',
                'pressure_advance_value': _number(pa),
                'start_filament_gcode': tail or PS3_FILAMENT_GCODE}
    if 'M573' in rest or 'M572' in rest:
        refuse('%s: start_filament_gcode %r mixes pressure-advance commands' % (label, gcode))
    # No M573 R: PrusaSlicer 3 cannot emit a bare M572 S0, so the G-code stays.
    return {'pressure_advance': 'disabled', 'start_filament_gcode': gcode}


def material_of(section_name, tag):
    m = re.fullmatch(r'filament:(.+) @COREONEINDX %s' % re.escape(tag), section_name)
    if not m:
        refuse('%s is not named "<material> @COREONEINDX %s"' % (section_name, tag))
    return m.group(1)


def slug(material):
    return re.sub(r'[^A-Za-z0-9]+', '-', material).strip('-')


def prusa_features(fil_coll, material):
    """The features ($.type, $.tags, ...) Prusa gives a material, from its top
    document and the top documents it inherits."""
    tops = [d for d in fil_coll.tops if d.get('name') == material]
    if not tops:
        return None
    out = {}

    def walk(doc):
        for p in doc.get('inherits') or []:
            walk(fil_coll.top_by_id[p])
        out.update(doc.get('features') or {})
    walk(tops[0])
    return out


def build_filament_ports(prusa, files, defs):
    fil = ps3_eval.Collection(prusa.kind_docs('filament', files))
    # Standard 0.4 is the one tool-8 size Prusa's PS3 profiles cover for the INDX;
    # where Prusa has a preset there, Prusa's is kept and nothing is ported.
    covered = {'0.4': {p.name.split('@')[0].strip()
                       for p in fil.presets(envs(prusa, ['0.4'], 0.2))}}
    ports, notes = {}, []
    for tid, bundle, tag, cond in FIL_SOURCES:
        sections = load_ini(os.path.join(REPO, bundle))
        for sec, body in sections.items():
            if not sec.startswith('filament:'):
                continue
            mat = material_of(sec, tag)
            if tid in covered and mat in covered[tid]:
                notes.append('%s @COREONEINDX %s: Prusa\'s PS3 preset kept, not ported' % (mat, tag))
                continue
            values, renamed = port_filament(body, defs, sec)
            name = '%s @COREONEINDX %s' % (mat, tag)
            ports.setdefault(mat, []).append(
                (tid, {'condition': cond, 'name': name,
                       'id': ps3_yaml.new_id('filament', name), 'values': values}, bundle, renamed))
    texts = {}
    # Features ($.tags: abrasive, ...) are looked up in all of Prusa's filament
    # files, not only the copied ones: PC Blend Carbon Fiber's own file names no
    # INDX, so it is not copied, but its tags still describe the material.
    every = ps3_eval.Collection(prusa.kind_docs('filament', sorted(prusa.docs)))
    for mat in sorted(ports):
        variants = [v for _, v, _, _ in ports[mat]]
        feats = prusa_features(every, mat)
        if feats is None:
            feats = {'$.type': variants[0]['values']['filament_type']}
        doc = {'kind': 'filament', 'id': ps3_yaml.new_id('filament', 'top', mat), 'name': mat,
               'condition': 'printer.base_model == "COREONE_INDX"', 'features': feats,
               'variants': variants + [{'name': '*quit*',
                                        'id': ps3_yaml.new_id('filament', 'quit', mat)}]}
        head = [banner(),
                '# %s for the INDX at the tool-8 sizes Prusa has no PS3 profile for.' % mat,
                '# Generated by tools/build_ps3_vendor.py from this repo\'s 2.9 bundles, key',
                '# for key (port_filament()): bookkeeping dropped, filament_retract_* and the',
                '# like renamed to the plain keys PrusaSlicer 3 uses, and the M572/M573 start',
                '# G-code turned into pressure_advance / pressure_advance_value.']
        for tid, v, bundle, renamed in ports[mat]:
            head.append('#   %s: %s (provenance in its filament_notes)' % (v['name'], bundle))
            if renamed:
                head.append('#     renamed: %s' % ', '.join(renamed))
        fname = 'preset-filament-hyiger-%s.yaml' % slug(mat)
        if fname in texts:
            refuse('two materials map to %s; rename one or extend slug()' % fname)
        texts[fname] = '\n'.join(head) + '\n' + ps3_yaml.dump(doc)
    return texts, notes


def fallthrough_patches(prusa, files):
    """Hide, on the JRH printer's tool-8 sizes Prusa has no INDX profile for, every
    Prusa material that would fall through to an untuned preset there."""
    sizes = [t for t in TOOLS if t not in ('0.4HF', '0.4')]
    docs = prusa.kind_docs('filament', files)
    coll = ps3_eval.Collection(docs)
    hits = {}
    for t in sizes:
        for p in coll.presets(envs(prusa, [t], 0.2)):
            hits.setdefault(p.root, set()).add(t)
    patched, notes = {}, []
    for f in files:
        if not f.startswith('preset-filament-'):
            continue
        segs = re.split(r'(?m)^---[ \t]*\n', prusa.text[f])
        changed = False
        for i, seg in enumerate(segs):
            doc = ps3_yaml_load_all(seg)
            if len(doc) != 1 or doc[0].get('id') not in hits:
                continue
            doc = doc[0]
            if doc.get('match_mode') == 'all_matches':
                refuse('%s: %s uses all_matches; a leading *quit* would not hide it'
                       % (f, doc.get('id')))
            ts = sorted(hits[doc['id']])
            q = {'condition': 'printer.base_model == "COREONE_INDX" and tool.nozzle_diameter != 0.4',
                 'name': '*quit*', 'id': ps3_yaml.new_id('filament', 'fallthrough', str(doc['id']))}
            item = ps3_yaml.dump([q], 0)
            if re.search(r'(?m)^variants:\s*$', seg):
                new = re.sub(r'(?m)^variants:\s*$\n', lambda m: m.group(0) + item, seg, count=1)
            else:
                new = seg.rstrip('\n') + '\nvariants:\n' + item
            want = dict(doc, variants=[q] + list(doc.get('variants') or []))
            if ps3_yaml_load_all(new) != [want]:
                refuse('%s: could not insert a leading *quit* into %s' % (f, doc['id']))
            segs[i] = new
            changed = True
            notes.append('%s (%s): %s' % (doc.get('name') or doc['id'], f,
                                          FALLTHROUGH_WHY.format(sizes=', '.join(ts))))
        if changed:
            body = '---\n'.join(s if s.endswith('\n') or not s else s + '\n' for s in segs)
            want = []
            for d in prusa.docs[f]:
                if d.get('id') in hits and d.get('match_mode') != 'all_matches':
                    q = {'condition': 'printer.base_model == "COREONE_INDX" and '
                                      'tool.nozzle_diameter != 0.4',
                         'name': '*quit*',
                         'id': ps3_yaml.new_id('filament', 'fallthrough', str(d['id']))}
                    d = dict(d, variants=[q] + list(d.get('variants') or []))
                want.append(d)
            if ps3_yaml_load_all(body) != want:
                refuse('%s: inserting *quit* variants changed more than those variants' % f)
            patched[f] = body
    return patched, notes


# --- checks ------------------------------------------------------------------------

def offered_layers(prusa, print_docs, tool_docs, t8):
    """Print presets the JRH printer offers with t8 in slot 8: a print preset is
    kept only if every tool has a matching tool_print (PresetEvaluator.cpp:668-683)."""
    tools = ['0.4HF'] * 7 + [t8]
    prints = ps3_eval.Collection(print_docs).presets(envs(prusa, tools))
    tool_coll = ps3_eval.Collection(tool_docs)
    out = set()
    for p in prints:
        h = p.values.get('layer_height')
        if h is None:
            continue
        if all(tool_coll.presets([e]) for e in envs(prusa, tools, float(h))):
            out.add(round(float(h), 3))
    return tuple(sorted(out))


def build(prusa_dir, ini_path, out_dir):
    defs = ps3_yaml.load_keydefs(KEYDEFS)
    prusa = Prusa(prusa_dir)
    ini = load_ini(ini_path)
    cv = ini.get('vendor', {}).get('config_version')
    if cv != INI_PIN:
        refuse('%s is PrusaResearch %s, this tool is pinned to %s' % (ini_path, cv, INI_PIN))
    repo_bundle = load_ini(os.path.join(REPO, 'vendor', 'hyigerINDX.ini'))
    files = select_files(prusa)

    out = {}
    for f in files:
        out[f] = (banner() + '\n# Prusa Research FFF %s %s, copied unchanged by '
                  'tools/build_ps3_vendor.py.\n' % (PS3_PIN, f) + prusa.text[f])
    out['vendor.yaml'] = build_vendor_yaml(prusa)
    out['preset-printer-coreone-indx.yaml'] = patch_printer(prusa.text['preset-printer-coreone-indx.yaml'])

    prints = ps3_eval.Collection(prusa.kind_docs('print', files))

    def print_base(t8, layer):
        """The print PrusaSlicer 3 uses at this layer with t8 in tool 8; of several
        with the same name the first by (name, id) wins (PresetEvaluator.cpp:690-718)."""
        for p in prints.presets(envs(prusa, ['0.4HF'] * 7 + [t8])):
            if abs(float(p.values.get('layer_height', -1)) - layer) < 1e-9:
                return p.values
        refuse('Prusa\'s PS3 profiles have no %.2f mm print for a %s tool 8' % (layer, t8))

    leaf07, hf06, nonhf, tnotes, treport = tool_print_additions(prusa, ini, repo_bundle, defs, print_base)
    out['preset-tool-coreone-indx.yaml'] = patch_tool(
        prusa.text['preset-tool-coreone-indx.yaml'], prusa.docs['preset-tool-coreone-indx.yaml'],
        leaf07, hf06, nonhf, tnotes, treport)

    ports, pnotes = build_filament_ports(prusa, files, defs)
    patched, fnotes = fallthrough_patches(prusa, files)
    for f, body in patched.items():
        out[f] = (banner() + '\n# Prusa Research FFF %s %s, copied by tools/build_ps3_vendor.py; a '
                  'leading *quit* variant is\n# added to each material listed here:\n' % (PS3_PIN, f)
                  + ''.join('#   %s\n' % n for n in fnotes if '(%s)' % f in n) + body)
    out.update(ports)

    # Everything below checks the result as PrusaSlicer 3 would read it.
    by_kind = {}
    for f, text in out.items():
        if f == 'vendor.yaml':
            continue
        for d in ps3_yaml_load_all(text):
            by_kind.setdefault(d.get('kind'), []).append(d)
    for kind, docs in by_kind.items():
        try:
            ps3_eval.Collection(docs).check_references()
        except ps3_eval.DanglingId as e:
            refuse('the %s presets would reference ids that do not exist: %s' % (kind, e))
        for d in docs:
            for _, i in _ids(d):
                if '@' in i and i not in prusa_ids(prusa, kind):
                    refuse('generated %s id %r contains @' % (kind, i))
    layers = {}
    for t8, want in LAYERS_EXPECTED.items():
        got = offered_layers(prusa, by_kind['print'], by_kind['tool_print'], t8)
        if got != tuple(round(w, 3) for w in want):
            refuse('with %s on tool 8 the printer would offer layer heights %s, not %s'
                   % (t8, got, want))
        layers[t8] = got
    fil = ps3_eval.Collection(by_kind['filament'])
    seen = {}
    for t in TOOLS:
        names = [p.name for p in fil.presets(envs(prusa, [t], LAYERS_EXPECTED[t][0]))]
        if len(set(n.split('@')[0].strip() for n in names)) != len(names):
            refuse('tool %s would list a material twice: %s' % (t, names))
        if t not in ('0.4HF', '0.4') and any('@COREONEINDX' not in n for n in names):
            refuse('tool %s would still list an untuned Prusa preset: %s'
                   % (t, [n for n in names if '@COREONEINDX' not in n]))
        seen[t] = names

    write(out, out_dir, prusa_dir, prusa)
    report(files, tnotes, treport, pnotes, fnotes, layers, seen)


def prusa_ids(prusa, kind):
    return {i for docs in prusa.docs.values() for d in docs if d.get('kind') == kind
            for _, i in _ids(d)}


def write(out, out_dir, prusa_dir, prusa):
    parent = os.path.dirname(os.path.abspath(out_dir))
    os.makedirs(parent, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='.build-', dir=parent)
    try:
        for f, text in sorted(out.items()):
            with open(os.path.join(tmp, f), 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(text)
        indx = prusa.vendor_doc('printer', 'COREONE_INDX')
        assets = os.path.join(tmp, 'assets')
        os.makedirs(assets)
        for a in sorted(set((indx.get('visual') or {}).values())):
            src = os.path.join(prusa_dir, 'assets', a)
            if not os.path.isfile(src):
                refuse('Prusa\'s INDX asset %s is missing' % a)
            shutil.copyfile(src, os.path.join(assets, a))
        if os.path.exists(out_dir):
            shutil.rmtree(out_dir)
        os.rename(tmp, out_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def report(files, tnotes, treport, pnotes, fnotes, layers, seen):
    if not VALIDATED:
        print('*** UNVALIDATED: nothing in this vendor has been printed with PrusaSlicer 3 ***')
    print('copied %d Prusa %s files' % (len(files), PS3_PIN))
    print('tool_print presets added:')
    for n in tnotes:
        print('  ' + n)
    for r in treport:
        print('  not carried: ' + r)
    print('filaments:')
    for n in pnotes:
        print('  ' + n)
    print('hidden on tool-8 sizes Prusa has no profile for:')
    for n in fnotes:
        print('  ' + n)
    print('with HF0.4 on tools 1-7, tool 8 offers:')
    for t8, got in layers.items():
        print('  %-6s layers %s; %d filaments' % (t8, ', '.join('%.2f' % h for h in got), len(seen[t8])))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('prusa', help="Prusa's installed PS3 vendor dir (.../prusa-research-fff/PrusaResearch)")
    ap.add_argument('ini', help='PrusaResearch.ini %s (the 2.9 bundles\' source)' % INI_PIN)
    ap.add_argument('out', help='output vendor directory, replaced on success')
    a = ap.parse_args(argv)
    build(a.prusa, a.ini, a.out)


if __name__ == '__main__':
    main()
