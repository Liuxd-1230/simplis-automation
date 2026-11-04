import hashlib
from pathlib import Path
import tempfile
import tracemalloc
import unittest

from simplis_automation_v2.vectors import parse_show_file, summarize_show_file, build_vector_manifest
from simplis_automation_v2.watchdog import _ScriptErrorReader, _artifact_snapshot, _artifact_signature, recent_error_artifacts

FIXTURES = Path(__file__).parent/'fixtures/show'


class StreamingTests(unittest.TestCase):
    def test_streamed_real_and_complex_exports_match_full_reader(self):
        for name in ('rc_transient.txt', 'control_q.txt', 'feedback_ac.txt'):
            with self.subTest(name=name):
                source = FIXTURES/name
                full = parse_show_file(source)
                summary = summarize_show_file(source)
                self.assertEqual(set(full), {'path', 'x_name', 'y_name', 'x', 'y', 'sha256', 'created_at'})
                self.assertEqual(summary['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
                self.assertEqual(summary['samples'], len(full['x']))
                self.assertEqual((summary['x_min'], summary['x_max']), (min(full['x']), max(full['x'])))
                self.assertTrue(summary['finite'] and summary['monotonic'])
        q = parse_show_file(FIXTURES/'control_q.txt')
        self.assertTrue(any(a == b for a, b in zip(q['x'], q['x'][1:])))
        self.assertIsInstance(parse_show_file(FIXTURES/'feedback_ac.txt')['y'][0], dict)

    def test_nonfinite_and_reverse_time_at_end_are_still_checked(self):
        lines = (FIXTURES/'rc_transient.txt').read_text().splitlines()
        with tempfile.TemporaryDirectory() as name:
            source = Path(name)/'invalid.txt'
            source.write_text('\n'.join(lines+['nan nan'])+'\n')
            self.assertFalse(summarize_show_file(source)['finite'])
            source.write_text('\n'.join(lines+[lines[1]])+'\n')
            self.assertFalse(summarize_show_file(source)['monotonic'])

    def test_cached_and_streamed_manifests_have_same_records(self):
        source = (FIXTURES/'feedback_ac.txt').resolve()
        with tempfile.TemporaryDirectory() as name:
            args = dict(run_id='r', candidate_hash='c', source_hash='s', run_started_at=0,
                        exports=[dict(path=source, group='ac', analysis='ac', vector='FB')],
                        output_path=Path(name)/'manifest.json', trusted=True)
            streamed = build_vector_manifest(**args)
            for include_summary in (False, True):
                full = parse_show_file(source, include_summary=include_summary)
                cached = build_vector_manifest(**args, parsed_vectors={str(source): full})
                self.assertEqual(cached['vectors'], streamed['vectors'])
                self.assertEqual(cached['errors'], streamed['errors'])

    def test_summary_uses_less_memory_on_real_export(self):
        peaks = []
        for function in (parse_show_file, summarize_show_file):
            tracemalloc.start()
            result = function(FIXTURES/'rc_transient.txt')
            peaks.append(tracemalloc.get_traced_memory()[1])
            tracemalloc.stop()
            del result
        self.assertLess(peaks[1], peaks[0]/2)

    def test_log_reader_keeps_partial_errors_and_handles_truncation(self):
        content = (FIXTURES/'script_error.log').read_bytes()
        split = content.index(b'Error : Line')+8
        error = content.splitlines()[-1].decode('utf-8')
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)/'messages.log'
            reader = _ScriptErrorReader(path)
            self.assertEqual(reader.read(), [])
            path.write_bytes(content[:split])
            self.assertEqual(reader.read(), [])
            with path.open('ab') as stream:
                stream.write(content[split:])
            self.assertEqual(reader.read(), [error])
            self.assertEqual(reader.offset, len(content))
            self.assertEqual(reader.read(), [error])
            path.write_bytes(b'ordinary message\n')
            self.assertEqual(reader.read(), [])

    def test_shared_snapshot_gives_same_change_and_error_results(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root/'solver.err').write_text('error')
            (root/'raw.t2').write_text('raw')
            snapshot = _artifact_snapshot(root)
            self.assertEqual(_artifact_signature(root), _artifact_signature(root, snapshot=snapshot))
            self.assertEqual(recent_error_artifacts(root, started_at=0),
                             recent_error_artifacts(root, started_at=0, snapshot=snapshot))


if __name__ == '__main__':
    unittest.main()
