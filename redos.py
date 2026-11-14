"""ReDoS guards for user-supplied regex patterns in dataset contracts.

Three layers (see SECURITY-GATE-REEL20-REPORT-2026-11-14.md, finding F1):

1. Creation-time rejection -- validate_pattern() statically refuses
   patterns with known-catastrophic structure (nested quantifiers,
   ambiguous alternation under a quantifier, backreferences) before they
   are stored.
2. Runtime wall-clock guard -- safe_fullmatch() runs each per-cell match
   with the third-party `regex` module's in-thread `timeout=` (new
   dependency, pinned in requirements.txt). A thread-based guard was
   tried first and MEASURED to fail: a worker thread spinning in
   CPython's `re` holds the GIL, so the main thread could not even raise
   the timeout -- a `(a+)+$` test ran 57 s before the match finished on
   its own and the timeout never fired. The `regex` engine also handles
   most nested quantifiers natively (measured: `(a+)+$`, `([a-z]+)*$`,
   `(x+x+)+y` all return in <1 ms); the timeout covers the residue --
   measured: `(a|aa)+$` on 30 a's + "b" trips a 0.5 s timeout promptly,
   in-thread. engine.validate() also re-runs the static check once per
   column, so a legacy catastrophic pattern is refused without ever
   executing it.
3. Scheduler quarantine -- a dataset whose pattern trips the guard is
   flagged pattern_blocked (store.py) and the scheduler never re-runs it
   (monitor.py); a successful manual run clears the flag.

Stdlib only, except for the `regex` module used for the runtime guard
(no new dependencies otherwise). The static checker is a small
hand-rolled parser -- re's own parser (sre_parse) is private and was
removed in 3.13, so it cannot be relied on.

Limits of the design, stated plainly: the static checker is a
conservative approximation -- it rejects a few shapes that are only
quadratic, not exponential (e.g. "(a+)?"), because contract patterns are
simple field validators and never need them. The `regex` engine's
timeout is checked inside its own matching loop, so it fires promptly
in-thread; the residual risk is a pattern the static checker allows
AND the `regex` engine is slow on AND that survives the 2 s budget --
no such pattern is known, and the scheduler quarantine (layer 3) bounds
any repeat exposure to one run.
"""

import re

import regex

MAX_PATTERN_LENGTH = 500
PATTERN_TIMEOUT_SECONDS = 2.0

_DIGITS = frozenset("0123456789")
_WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SPACES = frozenset(" \t\n\r\f\v")


class PatternUnsafeError(Exception):
    """A pattern is statically unsafe (or unparseable) for contract use."""

    def __init__(self, message, column=None, pattern=None):
        super().__init__(message)
        self.column = column
        self.pattern = pattern


class PatternTimeoutError(PatternUnsafeError):
    """A pattern match exceeded its wall-clock budget."""


# --- Static checker -------------------------------------------------------

# First-sets use None to mean ANY (".", negated class, \D, ...). Character
# classes are expanded to frozensets; \w / \d / \s use their ASCII sets --
# Unicode word/space characters beyond ASCII are a known gap, covered by
# the runtime guard instead of the static check.


class _Parser:
    """Minimal regex parser: just enough structure to detect nested
    quantifiers and ambiguous alternation under a quantifier. Callers run
    re.compile() first, so the input is always a valid pattern."""

    def __init__(self, pattern):
        self.s = pattern
        self.i = 0
        self.n = len(pattern)

    def parse(self):
        branches, _ = self._parse_alt(top=True)
        if self.i != self.n:
            raise PatternUnsafeError("could not parse pattern")
        return branches

    def _parse_alt(self, top=False):
        branches = [[]]
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == "|":
                self.i += 1
                branches.append([])
                continue
            if ch == ")":
                if top:
                    # Unbalanced ")" -- re.compile would have rejected it.
                    branches[-1].append((("char", ch), None))
                    self.i += 1
                    continue
                return branches, ")"
            if ch == "(":
                atom = self._parse_group()
            elif ch == "[":
                atom = self._parse_class()
            elif ch == "\\":
                atom = self._parse_escape()
            elif ch == ".":
                self.i += 1
                atom = ("dot",)
            elif ch in "^$":
                self.i += 1
                atom = ("anchor",)
            else:
                self.i += 1
                atom = ("char", ch)
            quant = self._parse_quantifier()
            branches[-1].append((atom, quant))
        return branches, None

    def _parse_group(self):
        # self.s[self.i] == "("
        self.i += 1
        lookaround = False
        if self.s[self.i : self.i + 1] == "?":
            nxt = self.s[self.i + 1 : self.i + 2]
            if nxt == ":":
                self.i += 2
            elif nxt == "P":
                if self.s[self.i + 2 : self.i + 3] == "<":
                    end = self.s.find(">", self.i + 3)
                    if end == -1:
                        raise PatternUnsafeError("unterminated (?P<name>")
                    self.i = end + 1
                elif self.s[self.i + 2 : self.i + 3] == "=":
                    # (?P=name) -- a backreference.
                    self.i += 3
                    while self.i < self.n and self.s[self.i] != ")":
                        self.i += 1
                    self.i += 1  # consume ")"
                    return ("backref", 0)
                else:
                    raise PatternUnsafeError("unsupported group syntax")
            elif nxt in ("=", "!"):
                lookaround = True
                self.i += 2
            elif nxt == "<":
                if self.s[self.i + 2 : self.i + 3] in ("=", "!"):
                    lookaround = True
                    self.i += 3
                else:
                    raise PatternUnsafeError("unsupported group syntax")
            elif nxt == "#":
                end = self.s.find(")", self.i + 2)
                if end == -1:
                    raise PatternUnsafeError("unterminated comment")
                self.i = end + 1
                return ("empty",)
            elif nxt == ">":
                self.i += 2  # atomic group (?>...)
            else:
                # Inline flags: (?i) or (?i:...)
                j = self.i + 1
                while j < self.n and self.s[j] in "aiLmsux":
                    j += 1
                if j < self.n and self.s[j] == ")":
                    self.i = j + 1
                    return ("empty",)
                if j < self.n and self.s[j] == ":":
                    self.i = j + 1
                else:
                    raise PatternUnsafeError("unsupported group syntax")
        branches, term = self._parse_alt()
        if term != ")":
            raise PatternUnsafeError("unbalanced group")
        self.i += 1  # consume ")"
        return ("group", branches, lookaround)

    def _parse_class(self):
        # self.s[self.i] == "["
        self.i += 1
        negated = False
        if self.s[self.i : self.i + 1] == "^":
            negated = True
            self.i += 1
        chars = set()
        first = True
        while self.i < self.n:
            ch = self.s[self.i]
            if ch == "]" and not first:
                self.i += 1
                break
            first = False
            if ch == "\\":
                got = self._parse_class_escape()
                if got is None:
                    return ("class", None)
                chars |= got
                continue
            if (
                self.i + 2 < self.n
                and self.s[self.i + 1] == "-"
                and self.s[self.i + 2] != "]"
                and self.s[self.i + 2] != "\\"
            ):
                lo, hi = ch, self.s[self.i + 2]
                if ord(hi) - ord(lo) > 255:
                    return ("class", None)
                if ord(lo) <= ord(hi):
                    chars.update(chr(o) for o in range(ord(lo), ord(hi) + 1))
                else:
                    chars.add(ch)
                    chars.add("-")
                    chars.add(hi)
                self.i += 3
                continue
            chars.add(ch)
            self.i += 1
        else:
            raise PatternUnsafeError("unterminated character class")
        if negated or len(chars) > 256:
            return ("class", None)
        return ("class", frozenset(chars))

    def _parse_class_escape(self):
        # Inside [...]; returns a set of chars, or None for ANY.
        self.i += 1  # consume backslash
        if self.i >= self.n:
            raise PatternUnsafeError("trailing backslash")
        c = self.s[self.i]
        self.i += 1
        if c == "d":
            return set(_DIGITS)
        if c == "D":
            return None
        if c == "w":
            return set(_WORD)
        if c == "W":
            return None
        if c == "s":
            return set(_SPACES)
        if c == "S":
            return None
        if c in "bB":
            # Inside a class, \b is the backspace character.
            return {"\b"} if c == "b" else set()
        return {self._escape_char(c)}

    def _escape_char(self, c):
        # Resolve a single escaped character to its literal value.
        simple = {
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "f": "\f",
            "v": "\v",
            "a": "\a",
        }
        if c in simple:
            return simple[c]
        if c == "x":
            return chr(self._take_hex(2, "x"))
        if c == "u":
            return chr(self._take_hex(4, "u"))
        if c == "U":
            return chr(self._take_hex(8, "U"))
        if c == "0":
            # \0 plus up to two octal digits.
            digits = ""
            while len(digits) < 2 and self.i < self.n and self.s[self.i] in "01234567":
                digits += self.s[self.i]
                self.i += 1
            return chr(int("0" + digits, 8)) if digits else "\0"
        return c

    def _take_hex(self, count, what):
        text = self.s[self.i : self.i + count]
        if len(text) != count:
            raise PatternUnsafeError(f"bad \\{what} escape")
        try:
            value = int(text, 16)
        except ValueError:
            raise PatternUnsafeError(f"bad \\{what} escape") from None
        self.i += count
        return value

    def _parse_escape(self):
        # Outside [...]; returns an atom.
        self.i += 1  # consume backslash
        if self.i >= self.n:
            raise PatternUnsafeError("trailing backslash")
        c = self.s[self.i]
        self.i += 1
        if c == "d":
            return ("class", _DIGITS)
        if c == "D":
            return ("class", None)
        if c == "w":
            return ("class", _WORD)
        if c == "W":
            return ("class", None)
        if c == "s":
            return ("class", _SPACES)
        if c == "S":
            return ("class", None)
        if c in "bB":
            return ("anchor",)
        if c in "AZz":
            return ("anchor",)
        if c in "123456789":
            return ("backref", int(c))
        if c == "N":
            if self.s[self.i : self.i + 1] == "{":
                end = self.s.find("}", self.i)
                if end == -1:
                    raise PatternUnsafeError("bad \\N escape")
                self.i = end + 1
                return ("class", None)
            return ("char", "N")
        return ("char", self._escape_char(c))

    def _parse_quantifier(self):
        if self.i >= self.n:
            return None
        ch = self.s[self.i]
        if ch == "*":
            quant = (0, None)
            self.i += 1
        elif ch == "+":
            quant = (1, None)
            self.i += 1
        elif ch == "?":
            quant = (0, 1)
            self.i += 1
        elif ch == "{":
            match = re.match(r"\{(\d+)(,(\d*))?\}", self.s[self.i :])
            if not match:
                return None  # "{" is a literal (re.compile agrees)
            low = int(match.group(1))
            if match.group(2) is None:
                high = low
            elif match.group(3):
                high = int(match.group(3))
            else:
                high = None
            quant = (low, high)
            self.i += len(match.group(0))
        else:
            return None
        # Lazy "?" or possessive "+" suffix.
        if self.i < self.n and self.s[self.i] in "?+":
            self.i += 1
        return quant


def _atom_first(atom):
    """First-set of one atom: (frozenset-or-None-for-ANY, nullable)."""
    kind = atom[0]
    if kind == "char":
        return frozenset((atom[1],)), False
    if kind == "class":
        return atom[1], False
    if kind == "dot":
        return None, False
    if kind in ("anchor", "empty"):
        return frozenset(), True
    if kind == "backref":
        return None, False
    if kind == "group":
        _, branches, lookaround = atom
        if lookaround:
            return frozenset(), True
        first = set()
        any_first = False
        nullable = False
        for branch in branches:
            branch_first, branch_nullable = _branch_first(branch)
            if branch_nullable:
                nullable = True
            if branch_first is None:
                any_first = True
            else:
                first |= branch_first
        return (None if any_first else frozenset(first), nullable)
    raise PatternUnsafeError("internal: unknown atom")


def _branch_first(branch):
    """First-set of one alternation branch: (set-or-None, nullable)."""
    first = set()
    any_first = False
    nullable = True
    for atom, quant in branch:
        atom_first, atom_nullable = _atom_first(atom)
        if atom_first is None:
            any_first = True
        else:
            first |= atom_first
        if quant is not None and quant[0] == 0:
            atom_nullable = True
        if not atom_nullable:
            nullable = False
            break
    return (None if any_first else frozenset(first), nullable)


def _group_has_quantifier(branches):
    for branch in branches:
        for atom, quant in branch:
            if quant is not None:
                return True
            if atom[0] == "group" and _group_has_quantifier(atom[1]):
                return True
    return False


def _sets_overlap(a, b):
    # None means ANY: overlaps everything.
    if a is None or b is None:
        return True
    return bool(a & b)


def _ambiguous_alternation(branches):
    if len(branches) < 2:
        return False
    firsts = []
    for branch in branches:
        first_set, nullable = _branch_first(branch)
        if nullable:
            # An empty-matching branch overlaps every other branch.
            return True
        firsts.append(first_set)
    for i in range(len(firsts)):
        for j in range(i + 1, len(firsts)):
            if _sets_overlap(firsts[i], firsts[j]):
                return True
    return False


def _check_branches(branches):
    for branch in branches:
        for atom, quant in branch:
            if atom[0] == "backref":
                raise PatternUnsafeError(
                    "backreferences (\\1, \\2, ...) are not allowed in "
                    "contract patterns: they can make matching exponential"
                )
            if atom[0] == "group":
                _check_branches(atom[1])
                if quant is not None:
                    if _group_has_quantifier(atom[1]):
                        raise PatternUnsafeError(
                            "nested quantifier: a repeated group must not "
                            "contain another repetition -- it can make "
                            "matching exponential"
                        )
                    if _ambiguous_alternation(atom[1]):
                        raise PatternUnsafeError(
                            "ambiguous alternation under a quantifier: two "
                            "branches can match the same text -- it can "
                            "make matching exponential"
                        )


def compile_pattern(pattern):
    """Compile a user pattern for execution; returns a regex-compiled
    pattern. Raises re.error for invalid syntax (callers treat that as
    "skip this pattern", as before) and PatternUnsafeError for statically
    unsafe patterns (callers quarantine the dataset). The pattern must
    compile under both `re` (the validity check) and `regex` (the engine
    that will run it)."""
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise PatternUnsafeError(
            f"pattern is {len(pattern)} characters (max {MAX_PATTERN_LENGTH})"
        )
    re.compile(pattern)
    try:
        compiled = regex.compile(pattern)
    except regex.error as exc:
        raise re.error(str(exc)) from None
    _check_branches(_Parser(pattern).parse())
    return compiled


def validate_pattern(pattern):
    """Raise PatternUnsafeError if the pattern must not be stored.

    Rejects: over-long patterns, invalid regexes, nested quantifiers,
    ambiguous alternation under a quantifier, and backreferences.
    """
    try:
        compile_pattern(pattern)
    except re.error as exc:
        raise PatternUnsafeError(f"not a valid regex: {exc}") from None


# --- Runtime wall-clock guard ------------------------------------------------


def safe_fullmatch(pattern, value, timeout=PATTERN_TIMEOUT_SECONDS):
    """regex fullmatch under a wall-clock guard.

    `pattern` is a regex-compiled pattern (from compile_pattern) or a
    pattern string. Returns True/False. Raises PatternTimeoutError if
    the match exceeds `timeout` seconds -- the `regex` engine checks the
    deadline inside its own matching loop, so the timeout fires promptly
    in-thread (a thread-based guard was measured to fail: CPython's `re`
    holds the GIL while matching, so the timeout could never fire).
    """
    compiled = pattern if hasattr(pattern, "fullmatch") else regex.compile(pattern)
    try:
        return compiled.fullmatch(value, timeout=timeout) is not None
    except TimeoutError:
        raise PatternTimeoutError(
            f"pattern match exceeded {timeout:g}s wall-clock budget"
        ) from None
