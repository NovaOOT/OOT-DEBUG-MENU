"""CPU detail policy and rebuild-option regressions, without game data.
Run: python tests/performance_options_check.py --cc <clang.exe>
"""
import argparse
import ast
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PerformanceOptions(unittest.TestCase):
    def test_shadow_policy(self):
        source = r'''
#include <assert.h>
#include "foliage_detail.h"
int main(void) {
    for (int d=0; d<3; ++d) for (int s=0; s<3; ++s)
    for (int task=0; task<2; ++task) for (int depth=0; depth<2; ++depth) {
        /* Non-foliage geometry is never removed, even by the lowest detail. */
        assert(!foliage_detail_skip(0,task,depth,d,s));
        /* Shadow detail cannot alter the colour pass or resurrect omitted plants. */
        if (!depth) assert(foliage_detail_skip(1,task,0,d,s)==foliage_detail_skip(1,task,0,d,0));
        if (foliage_detail_skip(1,task,depth,d,0)) assert(foliage_detail_skip(1,task,depth,d,s));
    }
    assert(!foliage_detail_skip(1,1,1,0,0));
    assert(foliage_detail_skip(1,1,1,0,1));
    assert(!foliage_detail_skip(1,0,1,0,1));
    assert(foliage_detail_skip(1,0,1,0,2));
    assert(!foliage_detail_skip(1,1,0,0,2));
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as t:
            p=Path(t); (p/'policy.c').write_text(source)
            subprocess.run([CC,'-std=c11','-Wall','-Werror','-I',str(ROOT/'runtime'),str(p/'policy.c'),'-o',str(p/'policy.exe')],check=True)
            subprocess.run([str(p/'policy.exe')],check=True)

    def test_rebuild_without_old_gpu_mode(self):
        module=ast.parse((ROOT/'tools/make_game.py').read_text(encoding='utf-8'))
        function=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='guest_opt_ini')
        scope={'os':os,'Path':Path}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'guest_opt_ini','exec'),scope)
        old=os.environ.pop('OOTPC_GUEST_OPT',None)
        try:
            with tempfile.TemporaryDirectory() as t:
                p=Path(t)
                for text in ('[performance]\nguest_opt = 2\n','low_end_gpu=0\nguest_opt=2 # faster code\n','OOTPC_GUEST_OPT=2\n'):
                    os.environ.pop('OOTPC_GUEST_OPT',None)
                    (p/'OOT-PC.ini').write_text(text)
                    scope['guest_opt_ini'](p)
                    self.assertEqual(os.environ.get('OOTPC_GUEST_OPT'),'2')
                os.environ['OOTPC_GUEST_OPT']='1'
                scope['guest_opt_ini'](p)
                self.assertEqual(os.environ['OOTPC_GUEST_OPT'],'1')
                os.environ.pop('OOTPC_GUEST_OPT')
                (p/'OOT-PC.ini').write_text('guest_opt=invalid\n')
                scope['guest_opt_ini'](p)
                self.assertNotIn('OOTPC_GUEST_OPT',os.environ)
        finally:
            os.environ.pop('OOTPC_GUEST_OPT',None)
            if old is not None: os.environ['OOTPC_GUEST_OPT']=old

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--cc',required=True)
    args,rest=parser.parse_known_args(); CC=args.cc
    unittest.main(argv=[__file__,*rest])
