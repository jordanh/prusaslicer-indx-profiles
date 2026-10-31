#!/usr/bin/env python3
"""
Extract PrusaSlicer 3's FFF option table into tools/ps3_keydefs.tsv.

    python3 tools/extract_ps3_keydefs.py \\
        <PrusaSlicer>/src/slic3r-domain/src/Slic3r/Domain/ConfigDefsFDM.cpp \\
        <PrusaSlicer>/src/slic3r-domain/src/Slic3r/Domain/ConfigCommon.cpp \\
        tools/ps3_keydefs.tsv

Both files are read from a checkout of the tag the table is pinned to
(version_3.0.0-alpha12). Each `def = defs.add("key", typeid(T))` block gives:

  location   def->location (Print, Filament, Printer, ...)
  overrides  def->overrides_in, written either `Locations{ Tool, Object }` or
             `{ Tool }`; both spellings occur
  type       T
  enum       the serialized names in init_fn's {{int(...), "name", ...}} list

ConfigCommon.cpp defines keys shared with SLA and sets their FFF location and
overrides inside `if (technology == FFF)`. That branch wins when present, and its
names are mapped: fdm_tool -> Tool, fdm_object -> Object, fdm_volume -> Volume,
FDMConfigLocation::Print and `print` -> Print.
"""
import re
import sys

_ALIAS = {'fdm_tool': 'Tool', 'fdm_object': 'Object', 'fdm_volume': 'Volume',
          'fdm_filament': 'Filament', 'print': 'Print', 'printer': 'Printer',
          'filament': 'Filament'}


def _name(token):
    token = token.strip().split('::')[-1]
    return _ALIAS.get(token, token)


def _fff_branch(body):
    m = re.search(r'if\s*\(\s*technology\s*==\s*FFF\s*\)\s*(\{[^}]*\}|[^;]*;)', body)
    return m.group(1) if m else None


def parse(src):
    rows = []
    parts = re.split(r'\bdef\s*=\s*defs\.add\(\s*"([^"]+)"\s*,\s*typeid\(([^)]*)\)\s*\)', src)
    for i in range(1, len(parts), 3):
        key, typ, body = parts[i], parts[i + 1].strip(), parts[i + 2]
        fff = _fff_branch(body)
        scope = body
        loc = re.search(r'def->location\s*=\s*([A-Za-z_:]+)', fff or '') or \
            re.search(r'def->location\s*=\s*([A-Za-z_:]+)', scope)
        ovr = re.search(r'overrides_in\s*=\s*(?:Locations)?\s*\{([^}]*)\}', fff or '') or \
            re.search(r'overrides_in\s*=\s*(?:Locations)?\s*\{([^}]*)\}', scope)
        enum = re.findall(r'\{\s*int\([^)]*\)\s*,\s*"([^"]+)"', body)
        rows.append((key,
                     _name(loc.group(1)) if loc else '',
                     ','.join(_name(t) for t in ovr.group(1).split(',') if t.strip()) if ovr else '',
                     typ, ','.join(enum)))
    return rows


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    *sources, out = argv
    rows, seen = [], set()
    for path in sources:
        with open(path, encoding='utf-8') as f:
            for row in parse(f.read()):
                if row[0] not in seen:  # the first definition wins, FDM before common
                    seen.add(row[0])
                    rows.append(row)
    with open(out, 'w', encoding='utf-8') as f:
        f.write('# PrusaSlicer 3 FFF option table, extracted by tools/extract_ps3_keydefs.py\n')
        f.write('# from: %s\n' % ', '.join(p.split('/src/', 1)[-1] for p in sources))
        f.write('# columns: key, location, overrides_in, type, enum names\n')
        for row in sorted(rows):
            f.write('\t'.join(row) + '\n')


if __name__ == '__main__':
    main(sys.argv[1:])
