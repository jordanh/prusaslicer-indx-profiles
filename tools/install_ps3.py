#!/usr/bin/env python3
"""
Install ps3/hyigerINDX into a PrusaSlicer 3 data folder.

    python3 tools/install_ps3.py                 # the default PrusaSlicer3-dev folder
    python3 tools/install_ps3.py --datadir DIR   # any other
    python3 tools/install_ps3.py --no-migrate    # vendor only

Quit PrusaSlicer first, or restart it afterwards: it reads presets at startup.

What it does:
  1. Replaces <datadir>/presets/local/hyiger-indx/hyigerINDX with ps3/hyigerINDX.
     PrusaSlicer 3.0.0-alpha12 loads every presets/local/<repo>/<vendor>/vendor.yaml
     (BundleLoader.cpp:75-105), and its updater only touches repositories it
     knows (PresetUpdaterUtils.cpp:909-912), so Prusa updates leave this alone.
  2. Copies your own presets for Prusa's vendor (presets/user/prusa-research-fff/
     PrusaResearch) to presets/user/hyiger-indx/hyigerINDX, so they apply to the
     JRH printer too. A preset is copied only if every preset id it inherits
     from exists in hyigerINDX (alpha12 aborts on a dangling id), and never over
     a file already there: from then on the two copies are separate.
"""
import argparse
import os
import shutil
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = 'hyiger-indx'
VENDOR_ID = 'hyigerINDX'
PRUSA = ('prusa-research-fff', 'PrusaResearch')


def default_datadir():
    home = os.path.expanduser('~')
    if sys.platform == 'darwin':
        return os.path.join(home, 'Library', 'Application Support', 'PrusaSlicer3-dev')
    if sys.platform == 'win32':
        return os.path.join(os.environ.get('APPDATA', home), 'PrusaSlicer3-dev')
    return os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.join(home, '.config')),
                        'PrusaSlicer3-dev')


def _docs(path):
    with open(path, encoding='utf-8') as f:
        return [d for d in yaml.safe_load_all(f) if d]


def _walk(node):
    yield node
    for v in node.get('variants') or []:
        yield from _walk(v)


def vendor_ids(vendor_dir):
    ids = set()
    for f in os.listdir(vendor_dir):
        if f.endswith('.yaml') and f != 'vendor.yaml':
            for d in _docs(os.path.join(vendor_dir, f)):
                ids.update((d.get('kind'), str(n['id'])) for n in _walk(d) if n.get('id') is not None)
    return ids


def needed(doc):
    out = set()
    for n in _walk(doc):
        for key in ('inherits', 'unconditional_inherits'):
            out.update(str(i) for i in n.get(key) or [])
        based = (n.get('features') or {}).get('based_id')
        if based:
            out.add(str(based))
    return {(doc.get('kind'), i) for i in out}


def install(src, datadir, migrate=True):
    presets = os.path.join(datadir, 'presets')
    if not os.path.isdir(presets):
        sys.exit('refusing: %s has no presets folder; is it a PrusaSlicer 3 data folder?' % datadir)
    if not os.path.isfile(os.path.join(src, 'vendor.yaml')):
        sys.exit('refusing: %s is not a vendor folder (no vendor.yaml)' % src)
    dest = os.path.join(presets, 'local', REPO_DIR, VENDOR_ID)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='.install-', dir=os.path.dirname(dest))
    try:
        shutil.rmtree(tmp)
        shutil.copytree(src, tmp)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.rename(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    report = {'vendor': dest, 'copied': [], 'kept': [], 'skipped': []}
    if not migrate:
        return report
    theirs = os.path.join(presets, 'user', *PRUSA)
    ours = os.path.join(presets, 'user', REPO_DIR, VENDOR_ID)
    if not os.path.isdir(theirs):
        return report
    known = vendor_ids(dest)
    for f in sorted(os.listdir(theirs)):
        if not f.endswith('.yaml'):
            continue
        target = os.path.join(ours, f)
        if os.path.exists(target):
            report['kept'].append(f)
            continue
        missing = set()
        for d in _docs(os.path.join(theirs, f)):
            missing |= needed(d) - known
        if missing:
            report['skipped'].append('%s (needs %s)' % (f, ', '.join(sorted(i for _, i in missing))))
            continue
        os.makedirs(ours, exist_ok=True)
        shutil.copyfile(os.path.join(theirs, f), target)
        report['copied'].append(f)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--datadir', default=default_datadir())
    ap.add_argument('--src', default=os.path.join(os.path.dirname(HERE), 'ps3', VENDOR_ID))
    ap.add_argument('--no-migrate', action='store_true')
    a = ap.parse_args(argv)
    r = install(a.src, a.datadir, not a.no_migrate)
    print('installed %s' % r['vendor'])
    for key in ('copied', 'kept', 'skipped'):
        for f in r[key]:
            print('  user preset %s: %s' % (key, f))
    print('Restart PrusaSlicer to load it.')


if __name__ == '__main__':
    main()
