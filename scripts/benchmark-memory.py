"""Reproducible synthetic QCE JSON parse memory comparison; no account data."""
import gc
import json
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hub.streaming_json import iter_export_messages


def measure(action):
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    result = action()
    elapsed = time.perf_counter() - started
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {'rows': result, 'seconds': round(elapsed, 3), 'peak_bytes': peak}


def main():
    with tempfile.TemporaryDirectory(prefix='lmh-memory-') as directory:
        path = Path(directory) / 'qce.json'
        rows = 50000
        with path.open('w', encoding='utf-8') as source:
            source.write('{"messages":[')
            for index in range(rows):
                if index:
                    source.write(',')
                json.dump({'id': str(index), 'content': {'text': 'x' * 1024,
                           'resources': [{'filename': 'synthetic.png'}]}}, source)
            source.write(']}')
        baseline = measure(lambda: len(json.loads(path.read_text(encoding='utf-8'))['messages']))
        optimized = measure(lambda: sum(1 for _ in iter_export_messages(path)))
        print(json.dumps({'qce_rows': rows, 'file_bytes': path.stat().st_size,
                          'baseline': baseline, 'optimized': optimized}, indent=2))
        if optimized['rows'] != rows or optimized['peak_bytes'] >= baseline['peak_bytes'] / 4:
            raise SystemExit('Streaming memory regression')


if __name__ == '__main__':
    main()
