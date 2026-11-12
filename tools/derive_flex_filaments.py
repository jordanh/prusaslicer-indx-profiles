#!/usr/bin/env python3
"""
Derive INDX filament presets for flexibles Prusa has drafted but not released
for the INDX, on a standard-flow 0.4 mm nozzle: Bondtech's "INDX Passive Tool -
0.4mm Standard Flow", a single-bore nozzle without the CHT core splitter, sold
for soft TPU and TPE (bondtech.se/product/indx-passive-tool-0-4mm-flexibles).

    python3 derive_flex_filaments.py PrusaResearch.ini out.ini

Prusa releases two INDX flexibles, Generic FLEX and Prusament TPU 95A
`@COREONEINDX HF0.4` (FLEX_REF). Each is `<material> @COREONE; *FLEX04PGC1INDX*`
plus one body that both write the same way. Prusa's commented-out drafts for
other flexibles (`; [filament:<material> @COREONEINDX]`) have the same parents but
not that body, so as drafted they would ram at *C1INDX_common*'s PLA rate,
30 mm3/s, and skip the INDX flex start G-code. Each preset here is:

  * Prusa's draft, resolved (MATERIALS names it);
  * then each FLEX_ADOPT key, set to the value both released INDX flexibles
    agree on (refuses if they stop agreeing);
  * MVS = the draft's CORE One parent's MVS x the INDX / CORE One ratio both
    released flexibles share (2 / 2.5 at 2.5.12; refuses if they differ). The
    drafts restate the CORE One figure; Prusa cut it by that ratio on release;
  * for a material Prusa's own profile once shared with the drafted one
    (MATERIALS `identity`), that profile's physical constants (IDENTITY_KEYS);
  * bound to a standard-flow 0.4 nozzle only (BIND).

Temperatures, bed, fans, cooling and extrusion multiplier stay the draft's. They
are the material's, and the released pair's shared 220 C is below NinjaTek's
published minimum for NinjaFlex (MAKER, checked: out of range refuses).

Last, a material in CALIBRATED takes the values hyiger's calibration prints on
an INDX settled on, each with the test that set it; these replace whatever the
steps above produced and are listed, with the derived value they replaced, in
the preset's notes, the header and stdout. A calibrated key the derivation never
sets refuses (a misspelt key would otherwise be added silently).

Calibration prints are not a finished part: VALIDATED stays False, the header
says which presets are calibrated and which are unprinted, and an uncalibrated
preset's notes say to calibrate temperature, EM, retraction and MVS.
"""
import argparse
import collections
import sys

from build_print_profiles import load, load_drafts, printer_models, resolve
from derive_filaments import COMMON, COMMON_ADOPT, inherits
from derive_std_filaments import BIND as STD_BIND, bypasses, fmt
from nozzles import area

VALIDATED = False   # no finished part printed; SemiFlex and NinjaFlex have calibration prints only
DIA = '0.4'
INDX_BASE = '*FLEX04PGC1INDX*'
FLEX_REF = ('Generic FLEX @COREONEINDX HF0.4', 'Prusament TPU 95A @COREONEINDX HF0.4')
BIND = STD_BIND[DIA]
BIND_WHY = ("standard-flow 0.4 only: Prusa's INDX flexibles bind on diameter alone and so "
            "also reach the HF0.4 CHT, but Bondtech sells the standard-flow tool for soft "
            "TPU because it drops the CHT core splitter and its back-pressure")

# preset material -> Prusa draft it is built from, and (for a material Prusa
# once profiled together with the drafted one) the Prusa section naming it.
MATERIALS = collections.OrderedDict([
    ('NinjaTek NinjaFlex TPU', {
        'draft': 'NinjaTek NinjaFlex TPU @COREONEINDX', 'identity': None,
        'why': "Prusa's own unreleased INDX draft for NinjaFlex (85A)"}),
    ('NinjaTek SemiFlex TPU', {
        'draft': 'Fillamentum Flexfill 98A @COREONEINDX', 'identity': 'SemiFlex',
        'link': 'Flexfill 98A',
        'vendor': ('NinjaTek', "the material is NinjaTek SemiFlex; Prusa labels "
                               "its SemiFlex Generic because it once covered Flexfill 98A too"),
        'why': "Prusa had one profile for both 98A TPUs, \"SemiFlex or Flexfill 98A\" "
               "(SemiFlex's renamed_from), and identical EM 1.12 and MVS 1.35 after the "
               "split; it has no SemiFlex profile newer than the MK3 era, so the INDX "
               "draft for Flexfill 98A stands in"}),
])
# Physical constants of the material, taken from the `identity` section.
IDENTITY_KEYS = ('filament_density', 'filament_cost', 'filament_spool_weight')

# Keys both released INDX flexibles set alike, taken over the draft's value.
FLEX_ADOPT = collections.OrderedDict([
    ('start_filament_gcode', "adds Prusa's INDX flex line M906 P2 (extruder motor current) "
                             "to *C1INDX_FLEX_PA*'s M572 S0, which is all the drafts carry"),
    ('filament_multitool_ramming_flow', "2 mm3/s: the drafts resolve to *C1INDX_common*'s 30, "
                                        "a PLA ramming rate no flexible can follow"),
    ('filament_minimal_purge_on_wipe_tower', "Prusa's INDX flex purge, 15 over the common's 10"),
    ('filament_retract_length', "Prusa's INDX flex retraction (the drafts carry 3-3.5 from "
                                "their MK4 parents)"),
    ('filament_retract_speed', "Prusa's INDX flex retraction"),
    ('filament_deretract_speed', "Prusa's INDX flex retraction"),
    ('filament_retract_layer_change', "Prusa's INDX flex retraction: none on layer change"),
    ('filament_travel_ramping_lift', "off on both released INDX flexibles; the drafts leave "
                                     "it to the printer, which ramps"),
])

# Maker's published ranges, checked against each preset (None: no bound).
MAKER = {
    'NinjaTek NinjaFlex TPU': {
        'source': 'ninjatek.com/shop/ninjaflex and ninjatek.com/printing-guidelines',
        'temperature': (225, 250), 'first_layer_temperature': (225, 250),
        'bed_temperature': (None, 50), 'first_layer_bed_temperature': (None, 50),
        'max_mm_s': 35},
    # NinjaTek no longer lists SemiFlex (its shop page is gone), so there is no
    # published range to check against.
}
# Values settled by calibration prints on an INDX, per material: 2.9 key ->
# (value, reason). They replace the derived value; the reason names the test.
CALIBRATED = {
    'NinjaTek SemiFlex TPU': {
        'source': "hyiger's calibration prints, CORE One INDX tool 8 with Bondtech's Standard "
                  "Flow 0.4, PrusaSlicer 3.0.0-alpha12, 2026-11-07/08",
        'values': collections.OrderedDict([
            ('temperature', ('225', "temperature tower: 225 printed best")),
            ('first_layer_temperature', ('225', "kept at the printing temperature; the hotter "
                                                "bed gave the first layer its grip")),
            ('bed_temperature', ('60', "Flow Ratio coupons warped at the corners and one came "
                                       "free at 50; the retry at 60, Prusa's Generic FLEX "
                                       "@COREONEINDX HF0.4 bed, held")),
            ('first_layer_bed_temperature', ('60', "as bed_temperature")),
            ('extrusion_multiplier', ('1.15', "Flow Ratio pads 1.04-1.20 in steps of 0.02: "
                                              "1.14 and 1.16 best, split")),
            ('filament_max_volumetric_speed', ('3.6', "flow tower 1-4.5 mm3/s showed no clear "
                                                      "failure band; 80% of 4.5, a floor "
                                                      "rather than a measured limit")),
            ('filament_retract_length', ('0.2', "retraction tower: least stringing at band 2 "
                                                "(0.2 mm), more at every longer length")),
            ('min_fan_speed', ('25', "lowered from 45 against the corner curl on the first "
                                     "Flow Ratio coupons")),
            ('max_fan_speed', ('25', "as min_fan_speed")),
            ('bridge_fan_speed', ('66', "set in hyiger's calibrated preset; no test recorded "
                                        "for it")),
        ])},
    'NinjaTek NinjaFlex TPU': {
        'source': "hyiger's calibration prints, CORE One INDX tool 8 with Bondtech's Standard "
                  "Flow 0.4, PrusaSlicer 3.0.0-alpha12, 2026-11-09",
        'values': collections.OrderedDict([
            ('temperature', ('230', "temperature tower: 230 printed best; a calibration cube "
                                    "then printed cleanly at it")),
            ('first_layer_temperature', ('230', "kept at the printing temperature; the "
                                                "calibration cube's first layer held on the "
                                                "50 C bed")),
            ('filament_max_volumetric_speed', ('1.5', "Erik's Flow Ratio test failed twice at "
                                                      "the derived 2.4, its first layer running "
                                                      "about 3.1 mm3/s past the cap, and the "
                                                      "TPU tangled at the extruder gear; 1.5 "
                                                      "rules speed out and the calibration "
                                                      "cube printed cleanly. A floor rather "
                                                      "than a measured limit")),
            ('filament_retract_length', ('0.2', "SemiFlex's retraction-tower result, taken as "
                                                "the start for the softer 85A; the calibration "
                                                "cube printed cleanly with it. No retraction "
                                                "tower printed yet")),
            ('filament_retract_speed', ('25', "lowered from the released 95A flexibles' 60 so "
                                              "the soft 85A is not pulled hard through the "
                                              "gear; the calibration cube printed cleanly with "
                                              "it. Not tested on its own")),
            ('max_fan_speed', ('55', "set in hyiger's test preset; the calibration cube "
                                     "printed cleanly with it; no test recorded for it")),
            ('bridge_fan_speed', ('70', "as max_fan_speed")),
        ])},
}
WIDTH, LAYER = 0.45, 0.20   # Prusa's 0.20mm Balanced @COREONEINDX line, for the speed check


def refuse(msg):
    raise SystemExit('refusing: ' + msg)


def ref_values(S):
    """The released INDX flexibles, resolved, with their CORE One parents."""
    refs = []
    for name in FLEX_REF:
        key = 'filament:' + name
        if key not in S:
            refuse('Prusa no longer ships %s' % name)
        parents = inherits(S, key)
        if len(parents) != 2 or parents[1] != INDX_BASE or not parents[0].endswith(' @COREONE'):
            refuse('%s inherits %s, not "<material> @COREONE; %s"' % (name, parents, INDX_BASE))
        refs.append((name, resolve(S, key), resolve(S, 'filament:' + parents[0])))
    return refs


def adopted(refs):
    out = collections.OrderedDict()
    for k in FLEX_ADOPT:
        vals = {r.get(k) for _, r, _ in refs}
        if len(vals) != 1 or None in vals:
            refuse('the released INDX flexibles no longer agree on %s (%s); review FLEX_ADOPT'
                   % (k, ', '.join('%s %s' % (n, r.get(k)) for n, r, _ in refs)))
        out[k] = vals.pop()
    return out


def mvs_ratio(refs):
    ratios = {round(float(r['filament_max_volumetric_speed'])
                    / float(c['filament_max_volumetric_speed']), 6) for _, r, c in refs}
    if len(ratios) != 1:
        refuse('the released INDX flexibles cut CORE One MVS by different ratios (%s)'
               % ', '.join('%s %s/%s' % (n, r['filament_max_volumetric_speed'],
                                         c['filament_max_volumetric_speed']) for n, r, c in refs))
    return ratios.pop()


def check_maker(mat, cfg):
    rng = MAKER.get(mat)
    if not rng:
        return 'no maker range published'
    for k, v in rng.items():
        if k in ('source', 'max_mm_s'):
            continue
        lo, hi = v
        x = float(cfg[k])
        if (lo is not None and x < lo) or (hi is not None and x > hi):
            refuse('%s %s = %s is outside %s (%s)' % (mat, k, cfg[k], v, rng['source']))
    mm_s = float(cfg['filament_max_volumetric_speed']) / area(WIDTH, LAYER)
    if mm_s > rng['max_mm_s']:
        refuse('%s MVS %s allows %.0f mm/s at %.2f x %.2f, past the maker\'s %s mm/s (%s)'
               % (mat, cfg['filament_max_volumetric_speed'], mm_s, WIDTH, LAYER,
                  rng['max_mm_s'], rng['source']))
    return 'within %s (MVS = %.0f mm/s at %.2f x %.2f mm)' % (rng['source'], mm_s, WIDTH, LAYER)


def derive(S, drafts):
    refs = ref_values(S)
    adopt = adopted(refs)
    ratio = mvs_ratio(refs)
    toolchanger = set(resolve(S, 'filament:' + COMMON)) | set(COMMON_ADOPT)
    for mat, spec in MATERIALS.items():
        name = '%s @COREONEINDX %s' % (mat, DIA)
        for released in (name, spec['draft'], mat + ' @COREONEINDX HF0.4'):
            if 'filament:' + released in S:
                refuse('Prusa now ships %s; review %s before deriving over it' % (released, mat))
        dkey = 'filament:' + spec['draft']
        if dkey not in drafts:
            refuse('Prusa has no draft %s to build %s from' % (spec['draft'], mat))
        draft = collections.OrderedDict(drafts[dkey])
        parents = [p.strip() for p in draft.get('inherits', '').split(';') if p.strip()]
        if len(parents) != 2 or parents[1] != INDX_BASE or 'filament:' + parents[0] not in S \
                or not parents[0].endswith(' @COREONE'):
            refuse('%s inherits %s, not "<material> @COREONE; %s"' % (spec['draft'], parents, INDX_BASE))
        hit = sorted(set(draft) & toolchanger)
        if hit:
            refuse('%s sets INDX toolchanger key(s) %s in its own body; review it first'
                   % (spec['draft'], ', '.join(hit)))
        S2 = dict(S)
        S2[dkey] = draft
        if bypasses(S2, dkey, INDX_BASE):
            refuse('%s reaches %s other than through %s' % (spec['draft'], COMMON, INDX_BASE))
        cfg = collections.OrderedDict(resolve(S2, dkey))

        changed, moved = [], []
        for k, v in adopt.items():
            if cfg.get(k) != v:
                changed.append('%s %s -> %s' % (k, cfg.get(k) or 'unset', v))
                moved.append(k)
            cfg[k] = v
        co_mvs = float(resolve(S, 'filament:' + parents[0])['filament_max_volumetric_speed'])
        drafted = draft.get('filament_max_volumetric_speed')
        cfg['filament_max_volumetric_speed'] = fmt(round(co_mvs * ratio, 2))
        mvs_note = ('MVS %s = %s (%s) x %s, the INDX / CORE One ratio of both %s'
                    % (cfg['filament_max_volumetric_speed'], fmt(co_mvs), parents[0], fmt(ratio),
                       ' and '.join(FLEX_REF)))
        if drafted is not None:
            mvs_note += '; the draft types %s' % drafted

        ident = []
        if spec['identity']:
            ikey = 'filament:' + spec['identity']
            if ikey not in S:
                refuse('Prusa no longer has %s to take %s\'s constants from' % (spec['identity'], mat))
            if spec['link'] not in S[ikey].get('renamed_from', ''):
                refuse('%s no longer records that it was profiled with %s (renamed_from %r)'
                       % (spec['identity'], spec['link'], S[ikey].get('renamed_from')))
            src = resolve(S, ikey)
            for k in IDENTITY_KEYS:
                if src.get(k) is None:
                    cfg.pop(k, None)
                else:
                    cfg[k] = src[k]
                ident.append('%s %s' % (k, src.get(k) or 'unset'))
        if spec.get('vendor'):
            cfg['filament_vendor'] = spec['vendor'][0]

        cal = []
        for k, (v, why) in CALIBRATED.get(mat, {}).get('values', {}).items():
            if cfg.get(k) is None:
                refuse('%s: calibrated key %s is not set by the derivation; check its spelling'
                       % (mat, k))
            cal.append((k, cfg[k], v, why))
            cfg[k] = v
        maker = check_maker(mat, cfg)
        cfg.pop('renamed_from', None)
        cfg.pop('inherits', None)
        cfg['compatible_printers_condition'] = printer_models(
            refs[0][1].get('compatible_printers_condition')) + BIND
        cfg['compatible_printers'] = ''
        cfg['filament_settings_id'] = ''
        # filament_notes is one quoted INI string: name keys, never quote values.
        prov = ['INDX %s standard-flow preset from Prusa\'s draft %s, resolved: %s'
                % (DIA, spec['draft'], spec['why'].replace('"', "'"))]
        if moved:
            prov.append('As Prusa\'s released INDX flexibles: %s' % ', '.join(moved))
        prov.append(mvs_note)
        if ident:
            prov.append('Constants from Prusa\'s %s: %s' % (spec['identity'], ', '.join(ident)))
        if spec.get('vendor'):
            prov.append('Vendor %s: %s' % spec['vendor'])
        prov.append('Maker range: %s' % maker)
        if cal:
            prov.append('Calibrated, from %s: %s' % (
                CALIBRATED[mat]['source'],
                '; '.join('%s %s (derived %s): %s' % (k, v, old, why)
                          for k, old, v, why in cal)))
        if any('"' in p for p in prov):
            refuse('%s: provenance would put a double quote inside filament_notes' % name)
        if cal:
            if not VALIDATED:
                prov.append('No finished part printed yet')
        else:
            if not VALIDATED:
                prov.append('UNVALIDATED: not printed on an INDX')
            prov.append('Calibrate temperature, EM, retraction and MVS')
        notes = cfg.get('filament_notes', '""').strip('"')
        cfg['filament_notes'] = '"%s"' % ((notes + '\\n' if notes else '') + '. '.join(prov) + '.')
        yield name, cfg, (mat, spec['draft'], cfg['filament_max_volumetric_speed'], changed,
                          maker, cal)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('vendor', help='path to PrusaResearch.ini')
    ap.add_argument('out', help='output config bundle .ini')
    a = ap.parse_args(argv)
    S = load(a.vendor)
    drafts = load_drafts(a.vendor)
    version = S.get('vendor', {}).get('config_version', '?')
    rows, body = [], []
    for name, cfg, row in derive(S, drafts):
        body.append('[filament:%s]' % name)
        body += ['%s = %s' % (k, cfg[k]) for k in sorted(cfg)]
        body.append('')
        rows.append(row)
    out = ['# INDX 0.4 mm standard-flow nozzle presets for flexibles Prusa has drafted',
           '# but not released for the INDX, by hyiger.',
           '# Generated by tools/derive_flex_filaments.py from PrusaResearch.ini %s.' % version,
           '# Import via File > Import > Import Config Bundle.']
    unprinted = [r[0] for r in rows if not r[5]]
    calibrated = [r for r in rows if r[5]]
    if not VALIDATED and unprinted:
        out.append('# UNVALIDATED: %s not printed on an INDX. Calibrate first.'
                   % ', '.join(unprinted))
    for r in calibrated:
        out.append('# %s: calibrated on an INDX (%s)%s:'
                   % (r[0], CALIBRATED[r[0]]['source'],
                      '' if VALIDATED else ', calibration prints only, no finished part yet'))
        out += ['#   %s = %s (derived %s): %s' % (k, v, old, why) for k, old, v, why in r[5]]
    out.append('# Binding: %s.' % BIND_WHY)
    out.append('# Taken from Prusa\'s released INDX flexibles (%s):' % ', '.join(FLEX_REF))
    out += ['#   %s: %s' % (k, why) for k, why in FLEX_ADOPT.items()]
    out.append('')
    with open(a.out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out + body))
    if not VALIDATED:
        print('!! UNVALIDATED: no finished part printed on an INDX%s'
              % ('; never printed: ' + ', '.join(unprinted) if unprinted else ''))
    for mat, draft, mvs, changed, maker, cal in rows:
        print('%s: from %s, MVS %s; %s' % (mat, draft, mvs, maker))
        for c in changed:
            print('    ' + c)
        for k, old, v, why in cal:
            print('    calibrated %s %s -> %s: %s' % (k, old, v, why))
    print('%d presets -> %s' % (len(rows), a.out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
