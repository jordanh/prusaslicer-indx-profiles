"""
A model of how PrusaSlicer 3.0.0-alpha12 turns preset documents into presets.

It exists so the generator can check, before anything is installed, which
presets a printer will offer: which filaments tool 8 sees at each nozzle, and
which layer heights survive the "every tool needs a tool_print" rule. It follows
the alpha12 source rule by rule; line numbers are version_3.0.0-alpha12's.

Conditions (src/slic3r-shared/src/Slic3r/Biz/Expr/Parser.cpp:95-150, Eval.cpp):
  or / ||  <  and / &&  <  == !=  <  < > <= >= =~ !~  <  + -  <  * /  <  ! not + -
  `and` and `or` short-circuit (Eval.cpp:69-101), so `feeder.single_mode` is never
  read on a printer without a feeder. `=~` is std::regex_match, a full-string
  match (Domain/Expr/RegEx.hpp:54). An unknown variable is an error, as in
  PrusaSlicer, where it voids every preset of the vendor for that printer
  (PresetCollectionEvaluator.cpp:452-463).

  Variables (ValueMapBuilder.cpp:18-49): printer.model, printer.base_model,
  printer.<feature>, printer.tool_count, feeder.model and feeder.base_model (empty
  with no feeder), sheet.<feature>, sheet.type, tool.<feature>, and
  print.layer_height for tool_print and filament presets only.

Resolution (PresetCollectionEvaluator.cpp:186-420):
  * a node's own condition gates it, unless a child with its own condition
    inherits it;
  * `inherits` names top-level nodes, which are evaluated first, variants and all;
  * `unconditional_inherits` names any node and copies the values on the path to
    it, ignoring conditions;
  * then the node's id, name and values apply, so a child's name replaces a
    parent's, including a parent's '*quit*';
  * variants: first_match (the default) takes the first passing conditional
    variant, else the unconditional one; all_matches takes every passing one;
    with no variant taken, the node itself is the preset;
  * names merge with derive_name() (Domain/Preset/PresetTree.cpp:15-51), and a
    name starting with '*' is not public (PresetTree.cpp:64).
"""
import collections
import copy
import re


class UnknownVariable(Exception):
    pass


class DanglingId(Exception):
    pass


# --- conditions --------------------------------------------------------------

_TOKEN = re.compile(r'''
    \s*(?:
      (?P<num>\d+\.\d*|\.\d+|\d+)
    | "(?P<str>(?:[^"\\]|\\.)*)"
    | (?P<op>==|!=|<=|>=|=~|!~|&&|\|\||[()!<>+\-*/])
    | (?P<word>[A-Za-z_$][\w.$]*)
    )''', re.X)


def _tokens(text):
    pos, out = 0, []
    while pos < len(text):
        if text[pos:].strip() == '':
            break
        stripped = len(text[pos:]) - len(text[pos:].lstrip())
        if text[pos + stripped] == '/' and out and out[-1] in (('op', '=~'), ('op', '!~')):
            end = pos + stripped + 1
            while text[end] != '/' or text[end - 1] == '\\':
                end += 1
            out.append(('re', text[pos + stripped + 1:end].replace('\\/', '/')))
            pos = end + 1
            continue
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise SyntaxError('cannot read condition at %r' % text[pos:])
        pos = m.end()
        kind = m.lastgroup
        val = m.group(kind)
        if kind == 'word' and val in ('and', 'or', 'not'):
            out.append(('op', {'and': '&&', 'or': '||', 'not': '!'}[val]))
        elif kind == 'word' and val in ('true', 'false'):
            out.append(('lit', val == 'true'))
        elif kind == 'num':
            out.append(('lit', float(val)))
        elif kind == 'str':
            out.append(('lit', re.sub(r'\\(.)', r'\1', val)))
        else:
            out.append((kind, val))
    return out


class _Parser:
    LEVELS = [('||',), ('&&',), ('==', '!='), ('<', '>', '<=', '>=', '=~', '!~'),
              ('+', '-'), ('*', '/')]

    def __init__(self, text):
        self.toks = _tokens(text)
        self.i = 0
        self.tree = self.binary(0)
        if self.i != len(self.toks):
            raise SyntaxError('trailing input in condition %r' % text)

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def binary(self, level):
        if level == len(self.LEVELS):
            return self.unary()
        left = self.binary(level + 1)
        while self.peek()[0] == 'op' and self.peek()[1] in self.LEVELS[level]:
            op = self.take()[1]
            if op in ('=~', '!~'):
                kind, pattern = self.take()
                if kind != 're':
                    raise SyntaxError('expected a /regex/ after ' + op)
                left = ('match', op == '!~', left, re.compile(pattern))
            else:
                left = (op, left, self.binary(level + 1))
        return left

    def unary(self):
        kind, val = self.peek()
        if kind == 'op' and val in ('!', '-', '+'):
            self.take()
            return ('u' + val, self.unary())
        if kind == 'op' and val == '(':
            self.take()
            inner = self.binary(0)
            if self.take() != ('op', ')'):
                raise SyntaxError('missing )')
            return inner
        self.take()
        if kind == 'lit':
            return ('lit', val)
        if kind == 'word':
            if self.peek() == ('op', '('):
                raise SyntaxError('functions are not modelled: ' + val)
            return ('var', val)
        raise SyntaxError('unexpected %r' % (val,))


_CACHE = {}


def _eval(node, env):
    tag = node[0]
    if tag == 'lit':
        return node[1]
    if tag == 'var':
        if node[1] not in env:
            raise UnknownVariable(node[1])
        v = env[node[1]]
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
    if tag == 'u!':
        return not _eval(node[1], env)
    if tag == 'u-':
        return -_eval(node[1], env)
    if tag == 'u+':
        return _eval(node[1], env)
    if tag == 'match':
        _, negate, lhs, rx = node
        return (rx.fullmatch(str(_eval(lhs, env))) is not None) != negate
    if tag == '&&':
        return bool(_eval(node[1], env)) and bool(_eval(node[2], env))
    if tag == '||':
        return bool(_eval(node[1], env)) or bool(_eval(node[2], env))
    a, b = _eval(node[1], env), _eval(node[2], env)
    return {'==': lambda: a == b, '!=': lambda: a != b, '<': lambda: a < b,
            '>': lambda: a > b, '<=': lambda: a <= b, '>=': lambda: a >= b,
            '+': lambda: a + b, '-': lambda: a - b, '*': lambda: a * b,
            '/': lambda: a / b}[tag]()


def condition(text, envs):
    """True if the condition holds for any of envs (PrusaSlicer evaluates print
    and printer presets once per tool and ORs the results; tool_print and
    filament presets get a single env)."""
    tree = _CACHE.get(text)
    if tree is None:
        tree = _CACHE[text] = _Parser(str(text)).tree
    return any(bool(_eval(tree, env)) for env in envs)


# --- names -------------------------------------------------------------------

def derive_name(name, parent):
    if not name:
        return parent
    base, *tags = name.split('@')
    ptags = parent.split('@')[1:] if parent else []
    out = base
    for t in tags:
        if t not in ptags:
            out += '@' + t
    for t in ptags:
        out += '@' + t
    return out


def is_public(name):
    return bool(name) and not name.startswith('*')


# --- resolution --------------------------------------------------------------

# root: the id of the top-level document the preset came from
Preset = collections.namedtuple('Preset', 'name id values features path root')


class _Ctx:
    __slots__ = ('name', 'id', 'values', 'features', 'mode', 'path')

    def __init__(self, name='', id='', values=None, features=None, mode=None, path=()):
        self.name, self.id = name, id
        self.values = dict(values or {})
        self.features = dict(features or {})
        self.mode, self.path = mode, tuple(path)

    def clone(self):
        return _Ctx(self.name, self.id, self.values, self.features, self.mode, self.path)


class Collection:
    """All documents of one preset kind in one vendor (system and user alike)."""

    def __init__(self, documents):
        self.tops = list(documents)
        self.top_by_id = {}
        self.path_by_id = {}
        for doc in self.tops:
            self.top_by_id.setdefault(doc.get('id'), doc)
            self._index(doc, [])

    def _index(self, node, path):
        path = path + [node]
        if node.get('id') is not None:
            self.path_by_id.setdefault(str(node['id']), path)
        for v in node.get('variants') or []:
            self._index(v, path)

    def check_references(self):
        """Every id named by inherits / unconditional_inherits exists. alpha12 aborts
        on a dangling one, even in release builds (PresetCollectionEvaluator.cpp:507)."""
        missing = set()

        def walk(node):
            for inh in node.get('inherits') or []:
                if inh not in self.top_by_id:
                    missing.add(inh)
            for inh in node.get('unconditional_inherits') or []:
                if str(inh) not in self.path_by_id:
                    missing.add(inh)
            for v in node.get('variants') or []:
                walk(v)
        for doc in self.tops:
            walk(doc)
        if missing:
            raise DanglingId(', '.join(sorted(map(str, missing))))

    def _cond(self, node, envs):
        return 'condition' not in node or condition(node['condition'], envs)

    def _eval(self, node, parents, envs, skip_cond, downstream=True):
        if not skip_cond and not self._cond(node, envs):
            return []
        ret = [p.clone() for p in parents]
        for inh in node.get('inherits') or []:
            ret = self._eval(self.top_by_id[inh], ret, envs,
                             skip_cond or 'condition' in node)
            if not ret:
                return []
        unc_values, unc_features = {}, {}
        for inh in node.get('unconditional_inherits') or []:
            for n in self.path_by_id[str(inh)]:
                ctx = self._eval(n, ret, envs, True, downstream=False)[0]
                unc_values.update(ctx.values)
                unc_features.update(ctx.features)
        for ctx in ret:
            if node.get('id') is not None:
                ctx.id = derive_name(str(node['id']), ctx.id)
            if node.get('name') is not None:
                ctx.name = derive_name(str(node['name']), ctx.name)
            if node.get('match_mode') is not None:
                ctx.mode = node['match_mode']
            ctx.values.update(unc_values)
            ctx.values.update(node.get('values') or {})
            ctx.features.update(unc_features)
            ctx.features.update(node.get('features') or {})
            ctx.path = ctx.path + (node.get('id'),)
        if not downstream:
            return ret
        first_only = all((c.mode or 'first_match') == 'first_match' for c in ret)
        found = []
        for var in node.get('variants') or []:
            conditional = 'condition' in var
            if conditional and not condition(var['condition'], envs):
                continue
            sub = self._eval(var, [_Ctx(mode='first_match' if first_only else 'all_matches')],
                             envs, True)
            found.extend(sub)
            if conditional and first_only:
                break
        if not found:
            return ret
        product = []
        for ctx in ret:
            for var in found:
                c = ctx.clone()
                if var.id:
                    c.id = derive_name(var.id, c.id)
                if var.name:
                    c.name = derive_name(var.name, c.name)
                if var.mode:
                    c.mode = var.mode
                c.values.update(var.values)
                c.features.update(var.features)
                c.path = c.path + var.path
                product.append(c)
        return product

    def presets(self, envs, public_only=True):
        out = []
        for doc in self.tops:
            for ctx in self._eval(doc, [_Ctx(id='', path=())], envs, False):
                if not public_only or is_public(ctx.name):
                    out.append(Preset(ctx.name, ctx.id, ctx.values, ctx.features, ctx.path,
                                      doc.get('id')))
        return sorted(out, key=lambda p: (p.name, p.id))
