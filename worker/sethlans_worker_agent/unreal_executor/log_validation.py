# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal log rules, as data (Unreal foundation FR-UE-12, FR-UE-13, FR-UE-15;
v4.1: S1, S3; review Sec S3, S1).

``LOG_RULES`` is a tuple of ``LogRule``, versioned ``LOG_RULES_VERSION = 3``.
Lines carry a ``[timestamp][frame]`` prefix, so rules search anywhere in a
line. Severities: ``fatal`` (the runner terminates at once; the job fails
without retry), ``retry`` (FR-UE-10), ``warn`` and ``info``.
``cvar_priority_ignored`` is ``info`` and never escalates: the line means
**our** ``-DPCVars`` value held (Dom N-10).

**Linear matching (Sec S3).** A rule is ``first`` (a pattern without an
unbounded wildcard), optionally followed by ``then``, searched only in the
text after ``first``'s match: the spec's ``A.*B`` without backtracking.
``exclude`` rejects a line whose text after ``first`` contains it (the
``compute_framework_error`` cache-miss exclusion). ``literals`` is a cheap
prefilter. Each rule sees at most ``MAX_RULE_CHARS`` characters of a line,
and lines come from the bounded splitter (``log_tail``), so a hostile
megabyte line costs microseconds, not seconds.

``scan_log(path, max_bytes=...)`` scans the **whole** log to EOF after exit
confirmation (S3: ``Compilation cancelled.`` lines can follow
``RequestExitWithStatus(0, 0``). A log larger than ``max_bytes``
(``LOG_MAX_MB``) is not scanned: the result reports a ``log_flood`` hit, so
the phase fails as a flood. ``log_flood`` itself is a size rule enforced by
the runner and has no line pattern.
"""
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Pattern, Tuple

from sethlans_worker_agent.unreal_executor.log_tail import MB, iter_log_lines

LOG_RULES_VERSION = 3
SEVERITIES = ('fatal', 'retry', 'warn', 'info')
LOG_FLOOD = 'log_flood'
MAX_CAPTURES_PER_RULE = 256
MAX_RULE_CHARS = 4096


@dataclass(frozen=True)
class LogRule:
    name: str
    first: Optional[Pattern]
    severity: str
    capture: bool = False
    then: Optional[Pattern] = None
    exclude: str = ''
    literals: Tuple[str, ...] = ()

    @property
    def is_line_rule(self) -> bool:
        return self.first is not None

    def match(self, line: str) -> Optional[Tuple[str, ...]]:
        """The captured groups (possibly empty) when the rule matches, else None."""
        text = line[:MAX_RULE_CHARS]
        if self.literals and not any(literal in text for literal in self.literals):
            return None
        head = self.first.search(text)
        if head is None:
            return None
        rest = text[head.end():]
        if self.exclude and self.exclude in rest:
            return None
        if self.then is None:
            return tuple(group or '' for group in head.groups())
        tail = self.then.search(rest)
        if tail is None:
            return None
        return tuple(group or '' for group in head.groups() + tail.groups())


def _rule(name: str, first: Optional[str], severity: str, capture: bool = False, then: Optional[str] = None,
          exclude: str = '', literals: Tuple[str, ...] = ()) -> LogRule:
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}")
    return LogRule(name, re.compile(first) if first is not None else None, severity, capture,
                   re.compile(then) if then is not None else None, exclude, literals)


_COMPUTE = 'LogComputeFramework: '
LOG_RULES: Tuple[LogRule, ...] = (
    _rule('compilation_cancelled', re.escape(_COMPUTE), 'retry', then=r': Compilation cancelled\.',
          literals=('Compilation cancelled.',)),
    # v4.1 (S3): the benign cold-cache line "... from DDC failed. Shader needs recompile." is excluded.
    _rule('compute_framework_error', re.escape(_COMPUTE), 'warn', then=r'Error|failed',
          exclude='from DDC failed. Shader needs recompile.', literals=(_COMPUTE,)),
    _rule('memory_over_budget', r'REQUESTED MEMORY OVER BUDGET', 'warn'),
    _rule('gpu_device_lost', r'LogD3D12RHI|LogRHI|LogVulkanRHI', 'fatal',
          then=r'DXGI_ERROR_DEVICE_(?:REMOVED|HUNG|RESET)|GPU crash|Device (?:was )?removed|VK_ERROR_DEVICE_LOST',
          literals=('RHI',)),
    _rule('critical_error', r'=== Critical error: ===|Fatal error:', 'fatal'),
    # Sec S1, second layer behind the env scrub: UE appends UE-CmdLineArgs after -DPCVars.
    _rule('cmdline_env_injection', r'Inserting commandline from UE-CmdLineArgs', 'fatal'),
    _rule('python_error', r'LogPython: Error:', 'warn'),
    _rule('ensure_failed', r'Ensure condition failed', 'warn'),
    # v4.1 (S1): also the legacy line "Movie Pipeline completed. Duration: +00:00:04.643";
    # review D6: the "+d.hh:mm:ss" form of runs longer than a day.
    _rule('mrq_completed', r'(Movie Graph Render|Movie Pipeline) completed\. Duration: \+(?:(\d+)\.)?'
          r'(\d\d):(\d\d):(\d\d\.\d+)', 'info', capture=True, literals=(' completed. Duration: +',)),
    _rule('mrq_executor_finished', r'MoviePipelineLinearExecutorBase finished (\d+) jobs', 'info', capture=True),
    _rule('exit_status_zero', r'RequestExitWithStatus\(0, 0', 'info'),
    _rule('mrq_cvar_applied', r'LogMovieRenderPipeline: Applying CVar "([^"]{1,256})"', 'info', capture=True,
          literals=('Applying CVar',)),
    _rule('cvar_priority_ignored', r"Setting the console variable '([^']{1,256})' with '\w{1,64}' was ignored "
          r"as it is lower priority", 'info', capture=True, then=r"Value remains '([^']{0,256})'",
          literals=('was ignored as it is lower priority',)),
    _rule('dp_cvar_set', r'Setting CommandLine Device Profile CVar: \[\[([^:\]]{1,256}):', 'info', capture=True),
    _rule(LOG_FLOOD, None, 'fatal'),
)


def line_rules(rules: Iterable[LogRule] = LOG_RULES) -> Tuple[LogRule, ...]:
    return tuple(rule for rule in rules if rule.is_line_rule)


def fatal_rules(rules: Iterable[LogRule] = LOG_RULES) -> Tuple[LogRule, ...]:
    """The line rules the process runner enforces while UE runs."""
    return tuple(rule for rule in line_rules(rules) if rule.severity == 'fatal')


@dataclass
class LogScan:
    counts: Dict[str, int] = field(default_factory=dict)
    captures: Dict[str, List[Tuple[str, ...]]] = field(default_factory=dict)
    lines: int = 0
    oversized: bool = False

    def count(self, name: str) -> int:
        return self.counts.get(name, 0)

    def fatal_hits(self, rules: Iterable[LogRule] = LOG_RULES) -> List[str]:
        return [rule.name for rule in rules if rule.severity == 'fatal' and self.count(rule.name)]

    def mrq_duration_s(self) -> Optional[float]:
        """The last ``mrq_completed`` duration in seconds (FR-UE-15), or None."""
        found = self.captures.get('mrq_completed') or []
        if not found:
            return None
        _kind, days, hours, minutes, seconds = found[-1]
        return int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    def executor_jobs(self) -> Optional[int]:
        found = self.captures.get('mrq_executor_finished') or []
        return int(found[-1][0]) if found else None

    def captured_names(self, rule: str) -> List[str]:
        return [groups[0] for groups in self.captures.get(rule, []) if groups]


def scan_line(text: str, scan: LogScan, rules: Iterable[LogRule]) -> List[LogRule]:
    """Apply ``rules`` to one line; return the rules that matched."""
    matched = []
    scan.lines += 1
    for rule in rules:
        groups = rule.match(text)
        if groups is None:
            continue
        matched.append(rule)
        scan.counts[rule.name] = scan.counts.get(rule.name, 0) + 1
        if rule.capture:
            bucket = scan.captures.setdefault(rule.name, [])
            if len(bucket) < MAX_CAPTURES_PER_RULE and groups not in bucket:
                bucket.append(groups)
            elif groups in bucket:
                bucket.remove(groups)  # keep the latest occurrence last (durations, job counts)
                bucket.append(groups)
    return matched


def scan_lines(lines: Iterable[str], rules: Iterable[LogRule] = LOG_RULES) -> LogScan:
    active = line_rules(rules)
    scan = LogScan()
    for text in lines:
        scan_line(text.rstrip('\r\n'), scan, active)
    return scan


def scan_log(path, rules: Iterable[LogRule] = LOG_RULES, max_bytes: Optional[int] = None) -> LogScan:
    """Scan a whole log to EOF; a log above ``max_bytes`` is a ``log_flood`` hit, unscanned.

    ``max_bytes`` defaults to ``config_unreal.LOG_MAX_MB``. Raises ``OSError``.
    """
    if max_bytes is None:
        from sethlans_worker_agent import config_unreal
        max_bytes = config_unreal.LOG_MAX_MB * MB
    if os.stat(path).st_size > max_bytes:
        return LogScan(counts={LOG_FLOOD: 1}, oversized=True)
    return scan_lines(iter_log_lines(path), rules)


def render_time_seconds(scan: LogScan, render_wall_s: float) -> int:
    """FR-UE-15: the parsed MRQ duration (rounded) when present, else the render wall time."""
    duration = scan.mrq_duration_s()
    return int(round(duration if duration is not None else render_wall_s))
