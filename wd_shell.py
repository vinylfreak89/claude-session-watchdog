"""Read straight-line shell command lists without executing shell code.

Quoted words and here-documents are data. Literal assignments may resolve paths;
substitutions remain opaque. Unsupported control flow is unproven, never absence.
This is deliberately a bounded reader, not an interpreter for arbitrary shell.
"""
import re
from dataclasses import dataclass


class Unproven(ValueError):
    pass


@dataclass
class Word:
    value: str
    literal: str
    known: bool
    quoted: bool


def commands(source):
    """Return unconditional argv lists; raise Unproven for unsupported grammar.

    Newlines/semicolons, pipelines, redirections, literal assignments, cd, and quoted
    heredocs are supported. No environment, files or subprocesses are evaluated.
    """
    env = {}
    pos = 0
    words = []
    result = []
    pending = []
    redirect = None
    in_pipeline = False
    operators = ('<<-', '<<<', '<<', '>>', '&&', '||', ';;', '|&', '>&', '<&', ';', '&', '|', '<', '>', '(', ')')

    def substitution(start):
        # Consume an opaque $(...) or backtick expression, including embedded
        # newlines/operators. Never reinterpret its text as top-level commands.
        if source[start] == '`':
            i = start + 1
            while i < len(source):
                if source[i] == '\\': i += 2; continue
                if source[i] == '`': return i + 1
                i += 1
            raise Unproven('unterminated substitution')
        i, depth, quote = start + 2, 1, None
        while i < len(source):
            c = source[i]
            if c == '\\': i += 2; continue
            if quote:
                if c == quote: quote = None
            elif c in "\"'": quote = c
            elif c == '(': depth += 1
            elif c == ')':
                depth -= 1
                if not depth: return i + 1
            i += 1
        raise Unproven('unterminated substitution')

    def word(start):
        i, quote, quoted, known = start, None, False, True
        value, literal = [], []
        while i < len(source):
            c = source[i]
            if quote is None and (c.isspace() or c in ';&|<>()'): break
            if c in "\"'" and quote != "'":
                if quote == c: quote = None; quoted = True; i += 1; continue
                if quote is None: quote = c; quoted = True; i += 1; continue
            elif c == "'" and quote == "'":
                quote = None; quoted = True; i += 1; continue
            if c == '\\' and quote != "'":
                if i + 1 >= len(source): raise Unproven('unfinished escape')
                nxt = source[i + 1]
                if nxt == '\n': i += 2; continue
                if quote == '"' and nxt not in '$`"\\':
                    value.append(c); literal.append(c); i += 1; continue
                value.append(nxt); literal.append(nxt); quoted = True; i += 2; continue
            if quote != "'" and (source.startswith('$(', i) or c == '`'):
                end = substitution(i)
                value.append(source[i:end]); literal.append(source[i:end]); known = False; i = end; continue
            if quote != "'" and c == '$':
                m = re.match(r'\$(?:\{([A-Za-z_][A-Za-z_0-9]*)\}|([A-Za-z_][A-Za-z_0-9]*))', source[i:])
                if m:
                    raw = m.group(0); name = m.group(1) or m.group(2)
                    literal.append(raw)
                    if name in env and env[name] is not None:
                        resolved = env[name]
                        value.append(resolved)
                        if quote is None and (not resolved or re.search(r'[\s*?\[]', resolved)):
                            known = False
                    else: value.append(raw); known = False
                    i += len(raw); continue
                known = False
                if source.startswith('${', i):
                    raise Unproven('unsupported parameter expansion')
            if quote is None and c in '*?[{}~': known = False
            value.append(c); literal.append(c); i += 1
        if quote: raise Unproven('unterminated quote')
        if i == start: raise Unproven('unsupported token')
        return Word(''.join(value), ''.join(literal), known, quoted), i

    def finish(pipeline=False):
        nonlocal words, redirect
        if redirect is not None: raise Unproven('missing redirection target')
        if not words: return
        assignments = []
        while words and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', words[0].literal):
            assignment = words.pop(0)
            name, value = assignment.value.split('=', 1)
            assignments.append((name, value if assignment.known else None))
        if not words:
            if not pipeline: env.update(assignments)
            return
        if assignments:
            # Environment-prefix assignments have command-local expansion rules.
            raise Unproven('command-local environment assignment')
        first = words[0]
        if not first.known: raise Unproven('dynamic executable')
        if first.value in ('if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'until',
                           'do', 'done', 'case', 'esac', 'function', '{', '}', '!',
                           'eval', 'exec', 'source', '.', 'exit', 'return', 'set', 'trap',
                           'alias', 'unalias', 'export', 'readonly', 'local', 'declare', 'read', 'unset'):
            raise Unproven('control flow or shell state mutation')
        result.append(words)
        words = []

    while pos < len(source):
        c = source[pos]
        if c in ' \t\r': pos += 1; continue
        if source.startswith('\\\n', pos): pos += 2; continue
        if c == '#':
            end = source.find('\n', pos)
            pos = len(source) if end < 0 else end
            continue
        if c == '\n':
            if in_pipeline and not words: raise Unproven('unfinished pipeline')
            finish(in_pipeline); in_pipeline = False; pos += 1
            for delimiter, strip_tabs, quoted in pending:
                body = []
                while pos < len(source):
                    end = source.find('\n', pos)
                    end = len(source) if end < 0 else end
                    line = source[pos:end]
                    pos = min(end + 1, len(source))
                    if (line.lstrip('\t') if strip_tabs else line) == delimiter: break
                    body.append(line)
                else: raise Unproven('unterminated heredoc')
                if not quoted and any(x in '\n'.join(body) for x in ('$', '`')):
                    raise Unproven('expanding heredoc')
            pending = []
            continue
        op = next((op for op in operators if source.startswith(op, pos)), None)
        if op:
            pos += len(op)
            if op in ('|', '|&'):
                if not words: raise Unproven('missing pipeline command')
                finish(True); in_pipeline = True; continue
            if op in (';', '&'):
                if not words: raise Unproven('missing command before separator')
                finish(in_pipeline or op == '&'); in_pipeline = False; continue
            if op in ('&&', '||', ';;', '(', ')'):
                raise Unproven('conditional or compound scope')
            if redirect is not None: raise Unproven('stacked redirection')
            redirect = op
            continue
        # A file descriptor immediately adjoining a redirection is not argv.
        fd = re.match(r'\d+(?=[<>])', source[pos:])
        if fd and redirect is None:
            pos += len(fd.group()); continue
        token, pos = word(pos)
        if redirect:
            if redirect in ('<<', '<<-'):
                pending.append((token.literal, redirect == '<<-', token.quoted))
            redirect = None
        else:
            words.append(token)
    if in_pipeline and not words: raise Unproven('unfinished pipeline')
    finish(in_pipeline)
    if pending: raise Unproven('missing heredoc body')
    return result
