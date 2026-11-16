# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A failing settings script stops the render (#240).

Blender ignores a failing ``--python`` script unless ``--python-exit-code``
is given, and then renders with whatever settings were in place. Both
command builders now pass ``--python-exit-code 86`` immediately before
``--python``; exit 86 becomes a job error naming the failing line and the
exception. The GPU device check (exit 87 and its marker) keeps its own
message. The scripts are written as UTF-8 and their values are ASCII.
"""
import pytest

from sethlans_worker_agent import blender_executor, blender_failure, blender_thumbnail
from sethlans_worker_agent import capacity as capacity_module
from sethlans_worker_agent.blender_failure import (
    SETTINGS_SCRIPT_EXIT_CODE, describe_blender_failure, python_script_args,
)
from sethlans_worker_agent.gpu_device_script import GPU_CHECK_EXIT_CODE, GPU_CHECK_MARKER
from tests.unit.worker import test_blender_executor_gpu_check as _gpu_check

# Shared with the #238 executor tests: the same patched executor environment.
executor_env = _gpu_check.executor_env
_fake_process, _gpu_job = _gpu_check._fake_process, _gpu_check._gpu_job

BLENDER_TRACEBACK = [
    "Read blend: scene.blend\n",
    "Traceback (most recent call last):\n",
    '  File "C:\\temp\\tmpab12.py", line 27, in <module>\n',
    "    scene.cycles.no_such_setting = 1\n",
    "    ^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n",
    "AttributeError: 'CyclesRenderSettings' object has no attribute 'no_such_setting'\n",
    "\n",
    "Error: script failed, file: 'C:\\temp\\tmpab12.py', exiting.\n",
]
PREFIX = 'The render settings script failed, so the render was stopped before it started. '
NON_ASCII = 'Note \u00ae \u663e\u5361'


def _cpu_job(settings=None):
    return dict(_gpu_job(), render_device='CPU', render_settings=settings or {})


def _command(popen):
    return list(popen.call_args.args[0])


def _assert_flag_before_python(command):
    i = command.index('--python')
    assert command[i - 2:i] == ['--python-exit-code', str(SETTINGS_SCRIPT_EXIT_CODE)]
    assert command.count('--python') == 1


class TestMessages:

    def test_constants_and_args(self):
        assert SETTINGS_SCRIPT_EXIT_CODE == 86 != GPU_CHECK_EXIT_CODE
        assert python_script_args('s.py') == ['--python-exit-code', '86', '--python', 's.py']

    def test_exit_86_names_the_failing_line_and_exception(self):
        message = describe_blender_failure(86, ''.join(BLENDER_TRACEBACK))
        assert message == PREFIX + (
            "Failing line: scene.cycles.no_such_setting = 1; "
            "AttributeError: 'CyclesRenderSettings' object has no attribute 'no_such_setting'"
        )

    def test_script_that_cannot_compile(self):
        stderr = ('  File "C:\\temp\\tmp.py", line 2\n    x = "\ufffd"\n           ^\n'
                  "SyntaxError: (unicode error) 'utf-8' codec can't decode byte 0xae\n")
        assert describe_blender_failure(86, stderr) == PREFIX + (
            'Failing line: x = "\ufffd"; '
            "SyntaxError: (unicode error) 'utf-8' codec can't decode byte 0xae")

    @pytest.mark.parametrize('stderr, detail', [('', 'No STDERR.'), ('one\ntwo\n', 'one; two')])
    def test_exit_86_without_traceback(self, stderr, detail):
        assert describe_blender_failure(86, stderr) == PREFIX + detail

    def test_detail_is_bounded(self):
        stderr = 'Traceback (most recent call last):\nValueError: ' + 'x' * 5000
        assert len(describe_blender_failure(86, stderr)) == len(PREFIX) + 1000

    @pytest.mark.parametrize('code', [GPU_CHECK_EXIT_CODE, 1, 120])
    def test_gpu_marker_at_line_start_wins_on_other_failing_codes(self, code):
        stderr = ''.join(BLENDER_TRACEBACK) + f"{GPU_CHECK_MARKER} 0 Blender device entries match\n"
        assert describe_blender_failure(code, stderr).startswith('GPU device check failed:')

    def test_other_codes_stay_generic(self):
        assert describe_blender_failure(1, 'boom') == 'Blender exited with code 1. Details: boom'


class TestExecutor:

    def test_flag_immediately_before_python(self, mocker, executor_env):
        executor_env['popen'].return_value = _fake_process(mocker, 0, [])

        assert blender_executor.execute_blender_job(_cpu_job())[0] is True

        _assert_flag_before_python(_command(executor_env['popen']))

    def test_exit_86_fails_the_job_with_the_settings_message(self, mocker, executor_env):
        executor_env['popen'].return_value = _fake_process(mocker, 86, BLENDER_TRACEBACK)

        result = blender_executor.execute_blender_job(_cpu_job({'cycles.no_such_setting': 1}))

        assert result[0] is False and result[6] is None
        assert result[5].startswith(PREFIX)
        assert 'Failing line: scene.cycles.no_such_setting = 1' in result[5]
        assert capacity_module.gpu_list_recheck_pending() is False  # not a GPU check failure

    def test_non_ascii_value_gives_ascii_script_written_as_utf8(self, mocker, executor_env):
        written = {}

        def popen(command, **_kwargs):
            with open(command[command.index('--python') + 1], 'rb') as f:
                written['bytes'] = f.read()
            return _fake_process(mocker, 0, [])

        executor_env['popen'].side_effect = popen
        temp_spy = mocker.spy(blender_executor.tempfile, 'NamedTemporaryFile')

        blender_executor.execute_blender_job(_cpu_job({'render.stamp_note_text': NON_ASCII}))

        assert temp_spy.call_args.kwargs['encoding'] == 'utf-8'
        text = written['bytes'].decode('ascii')  # raises if any byte is not ASCII
        assert f"scene.render.stamp_note_text = {ascii(NON_ASCII)}" in text


class TestThumbnail:

    def _run(self, mocker, tmp_path, return_code, stderr_lines):
        mocker.patch('sethlans_worker_agent.config.WORKER_TEMP_DIR', str(tmp_path / 'temp'))
        mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])
        popen = mocker.patch('subprocess.Popen', return_value=_fake_process(mocker, return_code, stderr_lines))
        temp_spy = mocker.spy(blender_thumbnail.tempfile, 'NamedTemporaryFile')
        job = dict(_cpu_job({'render.image_settings.file_format': 'OPEN_EXR'}))
        result = blender_thumbnail.render_exr_hdr_thumbnail(
            job, str(tmp_path / 'blender'), str(tmp_path / 'scene.blend'), 1,
            str(tmp_path / 'out' / 'render_####.exr'), None,
        )
        return result, _command(popen), temp_spy

    def test_flag_before_python_and_utf8(self, mocker, tmp_path):
        _result, command, temp_spy = self._run(mocker, tmp_path, 0, [])

        _assert_flag_before_python(command)
        assert temp_spy.call_args.kwargs['encoding'] == 'utf-8'

    def test_exit_86_is_a_failed_thumbnail_not_a_failed_job(self, mocker, tmp_path, caplog):
        with caplog.at_level('WARNING', logger=blender_thumbnail.__name__):
            result, _command_line, _spy = self._run(mocker, tmp_path, 86, BLENDER_TRACEBACK)

        assert result is None
        assert f'Thumbnail render failed: {PREFIX}' in caplog.text
        assert capacity_module.gpu_list_recheck_pending() is False

    def test_settings_failure_does_not_request_a_gpu_recheck(self):
        assert blender_failure.request_recheck_if_check_failed(86, ''.join(BLENDER_TRACEBACK)) is False


# A user's setting value echoed in Blender's traceback (security review of #240).
FORGED_IN_SOURCE = (
    "Traceback (most recent call last):\n"
    '  File "C:\\temp\\tmpab12.py", line 27, in <module>\n'
    f"    scene.render.resolution_x = '{GPU_CHECK_MARKER} card fried, replace GPU 0'\n"
    "    ^^^^^^^^^^^^^^^^^^^^^^^^^\n"
    "TypeError: bpy_struct: item.attr = val: expected an int type, not str\n"
)
FORGED_AT_LINE_START = (
    "Traceback (most recent call last):\n"
    '  File "C:\\temp\\tmpab12.py", line 27, in <module>\n'
    r"    scene.render.image_settings.file_format = 'x\nSETHLANS'" "\n"
    'TypeError: bpy_struct: item.attr = val: enum "x\n'
    f'{GPU_CHECK_MARKER} card fried" not found in (\'PNG\',)\n'
)
CONTROL = 'a\x1b[31mb\x07c\u202ed\udcae'
CONTROL_ESCAPED = r'a\x1b[31mb\x07c\u202ed\udcae'


class TestForgedMarker:

    @pytest.mark.parametrize('stderr', [FORGED_IN_SOURCE, FORGED_AT_LINE_START], ids=['in-source', 'line-start'])
    def test_exit_86_is_always_the_settings_failure(self, stderr):
        message = describe_blender_failure(86, stderr)

        assert message.startswith(PREFIX)
        assert 'GPU device check failed' not in message
        assert blender_failure.gpu_check_failure_message(86, stderr) is None
        assert blender_failure.request_recheck_if_check_failed(86, stderr) is False
        assert capacity_module.gpu_list_recheck_pending() is False

    def test_marker_mid_line_on_exit_1_is_not_a_gpu_failure(self):
        stderr = f"Error: {GPU_CHECK_MARKER} card fried\n"

        assert describe_blender_failure(1, stderr).startswith('Blender exited with code 1.')
        assert blender_failure.request_recheck_if_check_failed(1, stderr) is False

    @pytest.mark.parametrize('code', [1, GPU_CHECK_EXIT_CODE, 120])
    def test_marker_at_line_start_on_other_codes_is_a_gpu_failure(self, code):
        stderr = f"noise\n{GPU_CHECK_MARKER} 0 Blender device entries match\n"

        assert describe_blender_failure(code, stderr).startswith('GPU device check failed:')
        assert blender_failure.request_recheck_if_check_failed(code, stderr) is True


class TestDetailText:

    @pytest.mark.parametrize('code, stderr', [
        (86, f'Traceback (most recent call last):\nValueError: {CONTROL}\n'),
        (GPU_CHECK_EXIT_CODE, f'{GPU_CHECK_MARKER} {CONTROL}\n'),
        (1, f'{CONTROL}\n'),
    ], ids=['settings', 'gpu-check', 'generic'])
    def test_control_characters_are_escaped(self, code, stderr):
        message = describe_blender_failure(code, stderr)

        assert message.isprintable()
        assert message.endswith(CONTROL_ESCAPED)

    def test_line_breaks_and_tabs_in_generic_details(self):
        assert describe_blender_failure(1, 'one\ttab\r\ntwo\n') == 'Blender exited with code 1. Details: one tab; two'

    def test_long_source_line_keeps_the_exception(self):
        stderr = ('Traceback (most recent call last):\n  File "s.py", line 3, in <module>\n'
                  f"    scene.render.stamp_note_text = '{'n' * 3000}'\n"
                  "AttributeError: 'RenderSettings' object has no attribute 'stamp_note_text'\n")

        message = describe_blender_failure(86, stderr)

        assert message.endswith("n...; AttributeError: 'RenderSettings' object has no attribute 'stamp_note_text'")
        assert len(message) < len(PREFIX) + 300

    def test_file_line_without_source_names_the_exception_once(self):
        assert describe_blender_failure(86, '  File "s.py", line 2\nSyntaxError: x\n') == PREFIX + 'SyntaxError: x'

    @pytest.mark.parametrize('stderr, detail', [
        ('Traceback (most recent call last):\n'
         '  File "C:\\temp\\tmpab12.py", line 27, in <module>\n'
         '    _apply(scene)\n'
         '  File "<string>", line 3, in _apply\n'
         '  File "<frozen importlib._bootstrap>", line 1, in _load\n'
         'ValueError: boom\n',
         'Failing line: _apply(scene); ValueError: boom'),
        ('Traceback (most recent call last):\n'
         '  File "<string>", line 3, in _apply\n'
         '  File "<frozen importlib._bootstrap>", line 1, in _load\n'
         'ValueError: boom\n',
         'ValueError: boom'),
        ('Traceback (most recent call last):\n'
         '  File "C:\\temp\\tmpab12.py", line 27, in <module>\n'
         '    _apply(scene)\n'
         '  File "<string>", line 3, in _apply\n'
         '  [Previous line repeated 996 more times]\n'
         'RecursionError: maximum recursion depth exceeded\n',
         'Failing line: _apply(scene); RecursionError: maximum recursion depth exceeded'),
    ], ids=['last-real-source-line', 'no-source-line-at-all', 'repeated-frame-note'])
    def test_frame_without_source_line_is_not_the_failing_line(self, stderr, detail):
        assert describe_blender_failure(SETTINGS_SCRIPT_EXIT_CODE, stderr) == PREFIX + detail
        assert blender_failure.settings_script_failure_message(SETTINGS_SCRIPT_EXIT_CODE, stderr) == PREFIX + detail

    def test_traceback_header_only_falls_back_to_the_last_lines(self):
        stderr = 'Read blend: scene.blend\nTraceback (most recent call last):\n'
        assert describe_blender_failure(86, stderr) == (
            PREFIX + 'Read blend: scene.blend; Traceback (most recent call last):')
