import argparse
import contextlib
import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import react_lab as lab
import compare_state_feedback as comparison


class ComparisonTests(unittest.TestCase):
    def test_frozen_pairs_no_answer_leakage_resume_and_change_guard(self):
        class Client:
            model='offline-test'
            base_url='http://localhost'
            temperature=0
            thinking='disabled'
            token_field='max_tokens'
            requests=[]
            def __init__(self,args):pass
            def complete(self,messages):
                self.requests.append(str(messages))
                return 'Thought: Finish.\nAction: Finish[guess]',{}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data.json';out=root/'out'
            lab.write_json(data,[{'_id':str(i),'question':'Question '+str(i),'answer':'GOLD_SENTINEL'} for i in range(2)])
            args=argparse.Namespace(data=str(data),out=str(out),phase='development',exclude=[],seed=233,
                                    max_steps=7,max_tokens=1024,obs_chars=4000,timeout=5,retries=0,
                                    resume=False,verbose=False)
            with patch.object(lab,'load_env'),patch.object(lab,'ChatClient',Client), \
                 patch.object(lab,'WikiStore',return_value=lab.LocalStore({})),contextlib.redirect_stdout(io.StringIO()):
                comparison.run(args)
                self.assertEqual(len(Client.requests),4)
                self.assertNotIn('GOLD_SENTINEL',''.join(Client.requests))
                manifest=lab.read_json(out/'manifest.json')
                self.assertNotIn('GOLD_SENTINEL',str(manifest))
                self.assertEqual([p['arms'][0] for p in manifest['pair_order']],list(comparison.ARMS))
                self.assertTrue(lab.read_json(out/'comparison.json')['complete'])
                args.resume=True
                comparison.run(args)
                self.assertEqual(len(Client.requests),4)
                args.max_steps=8
                with self.assertRaises(ValueError):comparison.run(args)

    def test_evaluation_rejects_id_and_text_overlap_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data.json';excluded=root/'excluded.json'
            lab.write_json(data,[{'_id':'new','question':'Same question','answer':'new gold'}])
            lab.write_json(excluded,[{'_id':'old','question':'Same question','answer':'old gold'}])
            args=argparse.Namespace(data=str(data),exclude=[str(excluded)],phase='evaluation')
            with patch.object(lab,'load_env'),patch.object(lab,'ChatClient') as client:
                with self.assertRaisesRegex(ValueError,'question text'):comparison.run(args)
                client.assert_not_called()


if __name__=='__main__':unittest.main()
