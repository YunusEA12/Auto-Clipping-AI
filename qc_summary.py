"""Read-only technical QC statistics for the newest rendered clips."""
import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(root: Path, limit: int = 20) -> dict:
    if limit < 1:
        raise ValueError('limit must be positive')
    clips = sorted(root.rglob('*.mp4'), key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)[:limit]
    passed = blocked = missing = invalid = 0
    reasons = Counter()
    for video in clips:
        try:
            metadata = json.loads(video.with_suffix('.json').read_text(encoding='utf-8'))
            if not isinstance(metadata, dict):
                raise ValueError('invalid metadata')
            if 'auto_qc' not in metadata:
                missing += 1
                continue
            qc = metadata['auto_qc']
            if not isinstance(qc, dict) or type(qc.get('passed')) is not bool:
                raise ValueError('invalid QC')
            if qc['passed']:
                passed += 1
            else:
                blocked += 1
                values = qc.get('reasons')
                if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                    values = []
                reasons.update(set(values or ['unspecified']))
        except FileNotFoundError:
            missing += 1
        except (OSError, ValueError):
            invalid += 1
    evaluated = passed + blocked
    return {'total': len(clips), 'evaluated': evaluated, 'passed': passed, 'blocked': blocked,
            'blocked_fraction': blocked / evaluated if evaluated else None,
            'missing_qc': missing, 'invalid_reports': invalid,
            'reasons': dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0])))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('output'))
    parser.add_argument('-n', '--limit', type=int, default=20)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit must be positive')
    print(json.dumps(summarize(args.root, args.limit), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
