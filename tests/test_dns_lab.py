"""Offline DNS lab contract tests. Mocked kubectl; NOT a real Kind or CNI test."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE_KUBECTL = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
p=pathlib.Path(os.environ['DNS_MOCK_STATE'])
s=json.loads(p.read_text())
a=sys.argv[1:]
with open(os.environ['DNS_MOCK_LOG'],'a') as f:f.write(' '.join(a)+'\n')
if a[:2]==['-n','sre-lab-dns']:a=a[2:]
def out(t):print(t,end='');sys.exit(0)
if a[:1]==['cluster-info']:out('cluster ok')
if a[:2]==['get','deployment'] and 'jsonpath=' in ' '.join(a):out(s['policy'])
if a[:2]==['get','pods'] and 'app=lab-dns-probe' in a and 'json' in a:
 new={'metadata':{'name':'dns-client-new'},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}}
 pods=[new]
 if os.environ.get('DNS_MOCK_OLD_POD')=='1':
  old={'metadata':{'name':'dns-client-old','deletionTimestamp':'2026-11-21T03:00:00Z'},'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}}
  pods=[old,new]
 out(json.dumps({'items':pods}))
if a[:2]==['get','pod']:
 if 'app=lab-dns-echo' in a:out('10.41.0.12')
if a[:2]==['get','svc'] and 'jsonpath=' in ' '.join(a):out('10.96.0.35')
if a[:2]==['patch','deployment']:
 payload=json.loads(next(x[3:] for x in a if x.startswith('-p=')))
 s['policy']=next(x['value'] for x in payload if x.get('path')=='/spec/template/spec/dnsPolicy')
 p.write_text(json.dumps(s))
 out('patched')
if a[:1]==['exec']:
 call=a[a.index('--')+1:] if '--' in a else []
 if 'nslookup' in call:
  if s['policy']=='None':print('connection refused',file=sys.stderr);sys.exit(1)
  out('Name: lab-dns-echo.sre-lab-dns.svc.cluster.local')
 if 'curl' in call:
  url=call[-1]
  if os.environ.get('DNS_MOCK_VIP_DOWN')=='1' and '10.96.0.35' in url:
   print('network down',file=sys.stderr);sys.exit(7)
  if 'lab-dns-echo.sre-lab-dns.svc.cluster.local' in url and s['policy']=='None':
   print('could not resolve host',file=sys.stderr)
   sys.exit(int(os.environ.get('DNS_MOCK_CURL_FAULT_RC','6')))
  out('sre-network-lab-dns-ok')
 print('unsupported exec',file=sys.stderr);sys.exit(44)
if a[:2]==['version','-o']:out(json.dumps({'serverVersion':{'gitVersion':'v1.34.0'}}))
if a[:3]==['get','nodes','-o'] and a[-1]=='json':out(json.dumps({'items':[{'metadata':{'name':'n1'},'status':{'nodeInfo':{'kernelVersion':'6.1','kubeletVersion':'v1.34'}}}]}))
if a[:1] in (['get'],['apply'],['rollout'],['delete'],['logs']):out('mock resource')
print('unsupported mock '+repr(a),file=sys.stderr);sys.exit(44)
'''

class DnsLabTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d=Path(tmp.name)
        fake=self.d/'bin';fake.mkdir()
        mock=fake/'kubectl';mock.write_text(FAKE_KUBECTL);mock.chmod(0o755)
        self.state=self.d/'state.json';self.state.write_text('{"policy":"ClusterFirst"}')
        self.log=self.d/'commands.log';self.log.touch()
        self.env=os.environ.copy()
        self.env.update({
            'PATH':str(fake)+os.pathsep+os.environ['PATH'],
            'DNS_MOCK_STATE':str(self.state), 'DNS_MOCK_LOG':str(self.log),
            'LAB_ATTEMPTS':'2','LAB_RETRY_DELAY':'0',
            'SRE_LAB_ALLOW_MUTATIONS':'1',
            'EVIDENCE_DIR':str(self.d/'evidence'),
            'RUN_ID':'dns-test-01',
        })
    def run_script(self, script, *args, ok=True, overrides=None):
        env=self.env.copy()
        if overrides:env.update(overrides)
        p=subprocess.run(['bash',str(ROOT/'scripts'/script),*args],cwd=self.d,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=45)
        if ok:
            self.assertEqual(p.returncode,0,p.stdout+'\n'+p.stderr)
        else:
            self.assertNotEqual(p.returncode,0,p.stdout+'\n'+p.stderr)
        return p

    def test_full_dns_cycle(self):
        for action in ['setup','baseline','inject','fault','restore','verify','cleanup']:
            self.run_script('lab-dns.sh',action)
        self.assertEqual(json.loads(self.state.read_text())['policy'],'ClusterFirst')
        self.assertNotIn('delete ns',self.log.read_text())

    def test_mutations_fail_closed_and_wrong_state_rejected(self):
        self.run_script('lab-dns.sh','setup',ok=False,overrides={'SRE_LAB_ALLOW_MUTATIONS':'0'})
        self.assertEqual(self.log.read_text(),'')
        self.run_script('lab-dns.sh','fault',ok=False)
        self.run_script('lab-dns.sh','restore',ok=False)
        self.run_script('lab-dns.sh','inject')
        self.run_script('lab-dns.sh','inject',ok=False)
        self.run_script('lab-dns.sh','baseline',ok=False)

    def test_rollout_ignores_terminating_pod(self):
        self.run_script('lab-dns.sh','inject')
        self.run_script('lab-dns.sh','fault',overrides={'DNS_MOCK_OLD_POD':'1'})
        self.assertIn('exec dns-client-new --', self.log.read_text())
        self.assertNotIn('exec dns-client-old --', self.log.read_text())

    def test_dns_failure_not_confused_with_other_network_errors(self):
        self.run_script('lab-dns.sh','inject')
        self.run_script('lab-dns.sh','fault',ok=False,overrides={'DNS_MOCK_CURL_FAULT_RC':'7'})
        self.run_script('lab-dns.sh','fault',ok=False,overrides={'DNS_MOCK_VIP_DOWN':'1'})
        self.run_script('lab-dns.sh','fault')

    def test_evidence_pipeline_and_recovery(self):
        self.run_script('collect-evidence.sh','dns','fault',ok=False)
        self.run_script('collect-evidence.sh','dns','baseline')
        self.run_script('lab-dns.sh','inject')
        self.run_script('collect-evidence.sh','dns','fault')
        self.run_script('lab-dns.sh','restore')
        self.run_script('verify-recovery.sh','dns')
        self.run_script('collect-evidence.sh','dns','recovery')
        root=self.d/'evidence'/'dns-test-01'
        for phase in ['baseline','fault','recovery']:
            p=root/phase
            self.assertTrue((p/'lab-probe.out').exists())
            self.assertTrue((p/'SHA256SUMS').exists())
            self.assertEqual(json.loads((p/'phase-status.json').read_text())['probe_status'],'PASS')
        self.assertIn('RC=6', (root/'fault'/'lab-probe.out').read_text())

    def test_evidence_failure_is_not_success(self):
        self.run_script('collect-evidence.sh','dns','baseline')
        self.run_script('lab-dns.sh','inject')
        self.run_script('collect-evidence.sh','dns','fault',ok=False,overrides={'DNS_MOCK_CURL_FAULT_RC':'7'})
        status=self.d/'evidence'/'dns-test-01'/'fault'/'phase-status.json'
        self.assertEqual(json.loads(status.read_text())['probe_status'],'FAILED')
        self.assertEqual(json.loads(status.read_text())['probe_exit_code'],1)

if __name__=='__main__':unittest.main()
