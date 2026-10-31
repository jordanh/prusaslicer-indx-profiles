"""
YAML input/output and the option table for the PrusaSlicer 3 tools.

PrusaSlicer 3 (3.0.0-alpha12) stores presets as YAML documents. Reading uses
PyYAML. Writing does not: PyYAML's dumper reflows long G-code and picks quoting
by its own rules, so `dump()` writes block YAML itself and quotes exactly the
strings YAML would otherwise read back as something else. Every value it writes
is checked by reading it back (`yaml.safe_load(dump(x)) == x` is the contract,
and tests/test_ps3.py holds it to that).

The option table, tools/ps3_keydefs.tsv, is extracted from PrusaSlicer's own
option definitions by tools/extract_ps3_keydefs.py. It says, for every key,
where it lives (Print, Filament, Printer, ...) and where it may be overridden
(Tool, Filament, Object, Volume). A tool_print preset may set a key only if it
can be overridden in Tool; a filament preset only if it lives in, or can be
overridden in, Filament.
"""
import base64
import collections
import json
import re
import uuid

import yaml

# Fixed namespace, so ids are stable across runs and machines.
_NS = uuid.UUID('7c1f3c55-2b1e-4c55-9a51-1d7a3b2f0e01')

KeyDef = collections.namedtuple('KeyDef', 'location overrides type enum')


def new_id(*parts):
    """A stable id in Prusa's style (22 base64 characters). Never contains '@',
    which makes PrusaSlicer 3.0.0-alpha12 crash on restart."""
    raw = uuid.uuid5(_NS, '\x1f'.join(parts)).bytes
    return base64.b64encode(raw).decode('ascii').rstrip('=')


def load_docs(path):
    with open(path, encoding='utf-8') as f:
        return [d for d in yaml.safe_load_all(f) if d is not None]


def load_keydefs(path):
    defs = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if not line.strip() or line.startswith('#'):
                continue
            key, loc, ovr, typ, enum = line.rstrip('\n').split('\t')
            defs[key] = KeyDef(loc, frozenset(o for o in ovr.split(',') if o), typ,
                               tuple(e for e in enum.split(',') if e))
    return defs


def tool_key(defs, key):
    """May a tool_print preset set this key?"""
    d = defs.get(key)
    return d is not None and 'Tool' in d.overrides


def filament_key(defs, key):
    """May a filament preset set this key?"""
    d = defs.get(key)
    return d is not None and (d.location == 'Filament' or 'Filament' in d.overrides)


# --- writing -----------------------------------------------------------------

_PLAIN = re.compile(r'^[A-Za-z0-9_$.][^:#\n]*$')


def _reads_back(text, value):
    try:
        return yaml.safe_load('k: ' + text) == {'k': value}
    except yaml.YAMLError:
        return False


def _scalar(value):
    if value is None:
        return ''
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        if 'e' in text and '.' not in text:  # YAML 1.1 floats need a dot: 1.0e-05
            text = text.replace('e', '.0e', 1)
        return text
    if isinstance(value, str):
        if (value and _PLAIN.match(value) and value == value.strip()
                and _reads_back(value, value)):
            return value
        return json.dumps(value, ensure_ascii=False)  # a JSON string is YAML too
    raise TypeError('cannot write %r' % (value,))


def _literal(value, indent):
    """A multi-line string as a literal block, when YAML reads it back exactly."""
    if '\r' in value or '\t' in value or value.startswith((' ', '\n')):
        return None
    lines = value.split('\n')
    if any(line != line.rstrip(' ') for line in lines):
        return None
    if value.endswith('\n\n'):
        return None
    chomp = '' if value.endswith('\n') else '-'
    body = lines[:-1] if value.endswith('\n') else lines
    pad = ' ' * (indent + 2)
    return '|' + chomp + '\n' + '\n'.join((pad + line) if line else '' for line in body)


def _emit(value, indent, out):
    pad = ' ' * indent
    if isinstance(value, dict):
        if not value:
            out.append(pad + '{}')
            return
        for k, v in value.items():
            key = _scalar(str(k))
            if isinstance(v, (dict, list)) and v:
                out.append('%s%s:' % (pad, key))
                _emit(v, indent if isinstance(v, list) else indent + 2, out)
            elif isinstance(v, (dict, list)):
                out.append('%s%s: %s' % (pad, key, '{}' if isinstance(v, dict) else '[]'))
            elif isinstance(v, str) and '\n' in v and _literal(v, indent) is not None:
                out.append('%s%s: %s' % (pad, key, _literal(v, indent)))
            else:
                text = _scalar(v)
                out.append(('%s%s: %s' % (pad, key, text)).rstrip())
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                sub = []
                _emit(item, indent + 2, sub)
                sub[0] = pad + '- ' + sub[0][indent + 2:]
                out.extend(sub)
            elif isinstance(item, (dict, list)):
                out.append('%s- %s' % (pad, '{}' if isinstance(item, dict) else '[]'))
            else:
                out.append('%s- %s' % (pad, _scalar(item)))
        return
    raise TypeError('top level must be a mapping or a list')


def dump(obj, indent=0):
    """Block YAML for one document, ending in a newline."""
    out = []
    _emit(obj, indent, out)
    return '\n'.join(out) + '\n'
