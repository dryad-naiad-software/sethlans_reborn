# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The warm-up script's visit list (``ue_scripts/sethlans_warm_up.py``; Unreal foundation FR-UE-9, sequence rendering
FR-SW-6). A by-hand run found a 3-frame piece's warm-up stepping the whole sequence (101 visits, 11 minutes): the
stride and every camera-cut start were always visited. A piece (``only_frames``) now visits its own frames only.
The function is taken from the script's source: the script runs ``main()`` (an editor session) when loaded.
"""
import ast

from sethlans_worker_agent.unreal_executor import job_dir

SCRIPT = job_dir.SCRIPTS_SOURCE / job_dir.WARMUP_SCRIPT


def _visit_list(params):
    tree = ast.parse(SCRIPT.read_text(encoding='utf-8'))
    func, = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'visit_list']
    namespace = {'P': params}
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(SCRIPT), 'exec'), namespace)  # noqa: S102 - our file
    return namespace['visit_list']


CUTS = [0, 99, 202, 309, 965, 1810]


def test_a_piece_visits_only_its_own_frames():
    visit = _visit_list({'only_frames': True, 'frames': [150, 99, 150, 5000], 'camera_cut_starts': CUTS,
                         'step_frames': 24})
    assert visit(0, 1928) == [99, 150]


def test_a_piece_visits_at_most_64_frames():
    visit = _visit_list({'only_frames': True, 'frames': list(range(100)), 'step_frames': 24})
    assert visit(0, 1928) == list(range(64))


def test_other_jobs_keep_the_stride_and_every_cut():
    visit = _visit_list({'frames': [1000], 'camera_cut_starts': CUTS, 'step_frames': 24})
    visits = visit(0, 1928)
    assert len(visits) > 80 and {1000, 965, 1810} <= set(visits)
    assert _visit_list({'only_frames': False, 'frames': [1000], 'step_frames': 24})(0, 1928) == \
        _visit_list({'frames': [1000], 'step_frames': 24})(0, 1928)
