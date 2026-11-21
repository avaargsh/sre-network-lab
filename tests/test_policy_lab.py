"""Offline LAB-04 policy lifecycle tests. Fake kubectl != real Calico enforcement."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
p=pathlib.Path(os.environ['POLICY_MOCK_STATE'])
s=json.loads(p.read_text())
argv=sys.argv[1:]
with open(os.environ['POLICY_MOCK_LOG'],'a') as f:f.write(' '.join(argv)+'\n')
a=argv
if a[:2]==['-n','sre-lab-policy'] or a[:2]==['-n','calico-system']:
 a=a[2:]
def emit(msg):
 print(msg,end='');sys.exit(0)
def save():
 p.write_text(json.dumps(s))
if a[:2]==['config','current-context']:emit(os.environ.get('POLICY_MOCK_CONTEXT','kind-sre-policy-lab'))
if a[:1]==['cluster-info']:emit('mock cluster')
if a[:2]==['get','installation.operator.tigera.io']:
 if os.environ.get('POLICY_MOCK_NO_CALICO')=='1':sys.exit(1)
 emit('installation.operator.tigera.io/default')
if a[:2]==['get','daemonset'] and 'calico-node' in a:
 if os.environ.get('POLICY_MOCK_NO_CALICO')=='1':sys.exit(1)
 emit('daemonset.apps/calico-node')
if a[:2]==['get','namespace']:
 if not s['setup']:sys.exit(1)
 if 'jsonpath=' in ' '.join(a):emit('policy-lab')
 emit('namespace/sre-lab-policy')
if a[:2]==['get','networkpolicies'] and 'json' in a:
 emit(json.dumps({'items':[{'metadata':{'name':n}} for n in sorted(s['policies'])]}))
if a[:2]==['get','pods'] and 'json' in a:
 label=next((x.split('=',1)[1] for x in a if x.startswith('app=')),'missing')
 pod={'metadata':{'name':label+'-pod'},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}}
 emit(json.dumps({'items':[pod]}))
if a[:2]==['get','pod'] and 'jsonpath=' in ' '.join(a):emit('192.168.10.11')
if a[:2]==['get','svc'] and 'jsonpath=' in ' '.join(a):emit('10.96.0.10')
if a[:1]==['exec']:
 client=a[1]
 is_blocked='lab-policy-blocked' in client
 stage=set(s['policies'])
 blocked=(stage=={'lab-allow-allowed','lab-deny-ingress'} or stage=={'lab-deny-egress-blocked'})
 if is_blocked and blocked:
  print('curl: timed out',file=sys.stderr)
  sys.exit(int(os.environ.get('POLICY_MOCK_DENY_RC','28')))
 emit('sre-network-lab-policy-ok')
if a[:1]==['apply']:
 path=a[-1]
 if path.endswith('/manifests.yaml'):s['setup']=True
 if path.endswith('/ingress.yaml'):s['policies']=sorted(set(s['policies'])|{'lab-deny-ingress','lab-allow-allowed'})
 if path.endswith('/egress.yaml'):s['policies']=sorted(set(s['policies'])|{'lab-deny-egress-blocked'})
 if path.endswith('/allow-blocked.yaml'):s['policies']=sorted(set(s['policies'])|{'lab-allow-blocked'})
 save();emit('applied')
if a[:2]==['delete','networkpolicy']:
 s['policies']=sorted(set(s['policies'])-set(x for x in a[2:] if not x.startswith('-')))
 save();emit('deleted')
if a[:1]==['delete']:emit('deleted')
if a[:1]==['rollout']:emit('rollout ready')
if a[:2]==['version','-o']:emit(json.dumps({'serverVersion':{'gitVersion':'v1.37.0'}}))
if a[:3]==['get','nodes','-o'] and a[-1]=='json':
 emit(json.dumps({'items':[{'metadata':{'name':'worker'},'status':{'nodeInfo':{'kernelVersion':'6.1','kubeletVersion':'v1.37'}}}]}))
if a[:1]==['get']:emit('mock resource')
print('Unsupported mock '+repr(a),file=sys.stderr)
sys.exit(40)
'''

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.d = Path(self.temp.name)
        bindir = self.d/'bin'; bindir.mkdir()
        binary=bindir/'kubectl'; binary.write_text(FAKE); binary.chmod(0o755)
        self.state=self.d/'state.json'
        self.state.write_text(json.dumps({'setup':False,'policies':[]}))
        self.log=self.d/'commands.txt'; self.log.touch()
        self.env=os.environ.copy()
        self.env.update({
            'PATH':str(bindir)+os.pathsep+os.environ['PATH'],
            'POLICY_MOCK_STATE':str(self.state),
            'POLICY_MOCK_LOG':str(self.log),
            'LAB_ATTEMPTS':'3','LAB_RETRY_DELAY':'0',
            'SRE_LAB_ALLOW_MUTATIONS':'1',
            'SRE_LAB_EXPECT_CONTEXT':'kind-sre-policy-lab',
            'RUN_ID':'policy-lab-test-01',
            'EVIDENCE_DIR':str(self.d/'evidence'),
        })
    def call(self, name, *args, success=True, overrides=None):
        env=self.env.copy()
        if overrides:env.update(overrides)
        cmd=['bash',str(ROOT/'scripts'/name),*args]
        proc=subprocess.run(cmd,cwd=self.d,env=env,capture_output=True,text=True,timeout=90)
        if success:self.assertEqual(proc.returncode,0,proc.stdout+'\n'+proc.stderr)
        else:self.assertNotEqual(proc.returncode,0,proc.stdout+'\n'+proc.stderr)
        return proc
    def stage(self):
        return self.state_json()['policies']
    def state_json(self):
        return json.loads(self.state.read_text())

    def test_policy_full_matrix(self):
        for command in ['setup','baseline','inject-ingress','ingress','inject-egress','egress',
                        'inject-additive','additive','restore','verify','cleanup']:
            self.call('lab-policy.sh',command)
        self.assertEqual(self.stage(),[])
        self.assertNotIn('delete namespace',self.log.read_text())
        self.call('verify-recovery.sh','network-policy')

    def test_context_and_ack_are_fail_closed(self):
        self.call('lab-policy.sh','setup',success=False,overrides={'SRE_LAB_ALLOW_MUTATIONS':'0'})
        self.assertEqual(self.log.read_text(),'')
        self.call('lab-policy.sh','setup',success=False,overrides={'POLICY_MOCK_CONTEXT':'kind-production'})
        self.assertNotIn('apply',self.log.read_text())
        self.call('lab-policy.sh','setup',success=False,overrides={'POLICY_MOCK_NO_CALICO':'1'})
        self.assertNotIn('apply',self.log.read_text())

    def test_wrong_policy_stage_refuses_to_change(self):
        self.call('lab-policy.sh','setup')
        self.call('lab-policy.sh','ingress',success=False)
        self.call('lab-policy.sh','inject-egress',success=False)
        self.call('lab-policy.sh','inject-ingress')
        self.call('lab-policy.sh','inject-ingress',success=False)
        self.call('lab-policy.sh','baseline',success=False)

    def test_dns_failure_is_not_misidentified_as_networkpolicy_deny(self):
        self.call('lab-policy.sh','setup')
        self.call('lab-policy.sh','inject-ingress')
        self.call('lab-policy.sh','ingress',success=False,overrides={'POLICY_MOCK_DENY_RC':'6'})
        self.call('lab-policy.sh','ingress')

    def test_foreign_policy_blocks_restore(self):
        self.call('lab-policy.sh','setup')
        self.state.write_text(json.dumps({'setup':True,'policies':['foreign-policy']}))
        self.call('lab-policy.sh','restore',success=False)
        self.assertEqual(self.stage(),['foreign-policy'])

    def test_multiphase_evidence(self):
        self.call('lab-policy.sh','setup')
        self.call('collect-evidence.sh','network-policy','baseline')
        self.call('lab-policy.sh','inject-ingress')
        self.call('collect-evidence.sh','network-policy','ingress')
        self.call('lab-policy.sh','inject-egress')
        self.call('collect-evidence.sh','network-policy','egress')
        self.call('lab-policy.sh','inject-additive')
        self.call('collect-evidence.sh','network-policy','additive')
        self.call('lab-policy.sh','restore')
        self.call('collect-evidence.sh','network-policy','recovery')
        self.call('verify-recovery.sh','network-policy')
        root=self.d/'evidence'/'policy-lab-test-01'
        for phase in ['baseline','ingress','egress','additive','recovery']:
            d=root/phase
            self.assertEqual(json.loads((d/'phase-status.json').read_text())['probe_status'],'PASS')
            self.assertTrue((d/'SHA256SUMS').exists())
            self.assertTrue((d/'lab-probe.out').exists())

if __name__=='__main__':unittest.main()
