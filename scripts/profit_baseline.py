"""V1.4 read-only simulator earnings baseline; no action or currency conversion."""
import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path


def summarize(rows):
    valid = [r for r in rows if isinstance(r['duration'], (int, float))
             and math.isfinite(r['duration']) and r['duration'] > 0]
    duration = sum(r['duration'] for r in valid)
    return {
        'settlements': len(rows),
        'successes': sum(r['success'] == 1 for r in rows),
        'success_rate': sum(r['success'] == 1 for r in rows) / len(rows) if rows else None,
        'valid_duration_samples': len(valid),
        'duration_seconds': duration if valid else None,
        'simulated_gold': sum(r['simulated_gold'] for r in rows) if rows else None,
        'gold_per_minute': sum(r['simulated_gold'] for r in valid) * 60 / duration if duration else None,
        'simulated_fragments': sum(r['simulated_fragments'] for r in rows) if rows else None,
        'simulated_materials': sum(r['simulated_materials'] for r in rows) if rows else None,
        'consumable_cost': sum(r['consumable_cost'] for r in rows) if rows else None,
    }


def main():
    parser = argparse.ArgumentParser(description='只读模拟收益基线')
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    path = args.database.resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT * FROM research_settlement ORDER BY run_id,stage_id')]
        assert all(r['source'] == 'SIMULATOR' and r['cost_unit'] == 'SIMULATED_SUPPLY_UNIT' for r in rows)
        result = {'source': 'SIMULATOR', 'database': str(path), 'database_sha256': digest,
                  'cost_unit': 'SIMULATED_SUPPLY_UNIT', 'currency_conversion': None,
                  'overall': summarize([r for r in rows if r['stage_id'] == 0]),
                  'stages': {str(s): summarize([r for r in rows if r['stage_id'] == s]) for s in range(1, 5)},
                  'run_ids': sorted({r['run_id'] for r in rows}),
                  'limitations': ['八轮短时样本，不能代表长期效率', '没有优化前后对照，不能宣称收益提高',
                                  '消耗与金币单位不同，不相减', '仅自建模拟器，不换算人民币']}
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result['overall'], ensure_ascii=False))


if __name__ == '__main__':
    main()
