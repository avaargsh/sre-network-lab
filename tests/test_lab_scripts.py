"""Offline contract tests for SRE lab scripts using fake commands; not Kubernetes E2E."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE_KUBECTL = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
state_path=pathlib.Path(os.environ['MOCK_STATE'])
state=json.loads(state_path.read_text())
a=sys.argv[1:]
with open(os.environ['MOCK_LOG'],'a') as f:f.write(' '.join(a)+'\n')
if a[:2]==['cluster-info'] or a[:1]==['cluster-info']:
 print('mock cluster');sys.exit(0)
if len(a)>=2 and a[:2]==['-n','sre-lab-service']: a=a[2:]
if a[:2]==['get','svc'] and 'jsonpath=' in ' '.join(a):
 if 'clusterIP' in ' '.join(a):print('10.96.0.11',end='')
 else:print(state['port'],end='')
 sys.exit(0)
if a[:2]==['get','pod']:
 if 'app=lab-probe' in a: print('probe-123',end='')
 elif 'app=lab-echo' in a: print('10.42.0.10',end='')
 sys.exit(0)
if a[:1]==['exec']:
 url=a[-1]
 ok='10.42.0.10:5678' in url or (state['port']=='http' and ':80/' in url)
 if ok:print('sre-network-lab-ok');sys.exit(0)
 print('curl: connection refused',file=sys.stderr);sys.exit(int(os.environ.get('MOCK_SERVICE_FAILURE_RC','7')))
if a[:2]==['patch','svc']:
 val=json.loads(a[-1].removeprefix('-p='))[0]['value'];state['port']=str(val)
 state_path.write_text(json.dumps(state));print('patched');sys.exit(0)
if a[:2]==['version','-o']:print(json.dumps({'serverVersion':{'gitVersion':'v1.34.0'}}));sys.exit(0)
if a[:3]==['get','nodes','-o'] and a[-1]=='json':print(json.dumps({'items':[{'metadata':{'name':'node1'},'status':{'nodeInfo':{'kernelVersion':'6.1','kubeletVersion':'v1.34'}}}]}));sys.exit(0)
if a[:2]==['get','nodes'] and '-o' in a:print('node1 Ready');sys.exit(0)
if a[:1]==['get']:print('mock resource');sys.exit(0)
if a[:1] in (['apply'],['rollout'],['delete']):print('ok');sys.exit(0)
if a[:1]==['logs']:print('log');sys.exit(0)
print('unsupported mock: '+str(a),file=sys.stderr);sys.exit(44)
'''
FAKE_IP = r'''#!/usr/bin/env python3
import json,os,pathlib,sys
p=pathlib.Path(os.environ['MOCK_NETNS'])
state=json.loads(p.read_text())
a=sys.argv[1:]
if a[:2]==['netns','list']:
 print('\n'.join(state));sys.exit(0)
if a[:2]==['netns','add']:
 if a[2] in state:sys.exit(1)
 state.append(a[2]);p.write_text(json.dumps(state));sys.exit(0)
if a[:2]==['netns','del']:
 state.remove(a[2]);p.write_text(json.dumps(state));sys.exit(0)
print('mock ip');sys.exit(0)
'''

class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.d = Path(self.t.name)
        self.bin = self.d/'bin'; self.bin.mkdir()
        for name, source in [('kubectl',FAKE_KUBECTL),('ip',FAKE_IP)]:
            x=self.bin/name; x.write_text(source); x.chmod(0o755)
        ping=self.bin/'ping'; ping.write_text('#!/bin/sh\nexit 0\n'); ping.chmod(0o755)
        self.state=self.d/'state.json'; self.state.write_text('{"port":"http"}')
        self.nsstate=self.d/'namespaces.json';self.nsstate.write_text('[]')
        self.log=self.d/'calls.txt';self.log.touch()
        self.env=os.environ.copy();self.env.update({'PATH':f'{self.bin}:'+os.environ['PATH'], 'MOCK_STATE':str(self.state), 'MOCK_NETNS':str(self.nsstate),'MOCK_LOG':str(self.log),'LAB_ATTEMPTS':'2','LAB_RETRY_DELAY':'0','SRE_LAB_ALLOW_MUTATIONS':'1'})
    def script(self, name, *args, ok=True, **env_extra):
        env=self.env|env_extra
        p=subprocess.run(['bash',str(ROOT/'scripts'/name),*args],cwd=self.d,env=env,capture_output=True,text=True,timeout=45)
        if ok:self.assertEqual(p.returncode,0,p.stdout+'\n'+p.stderr)
        else:self.assertNotEqual(p.returncode,0,p.stdout+'\n'+p.stderr)
        return p

    def test_netns_up_verify_down_and_refuse_existing(self):
        self.script('lab-netns.sh','up')
        self.assertEqual(set(json.loads(self.nsstate.read_text())),{'sre-netlab-a','sre-netlab-b'})
        self.script('lab-netns.sh','up',ok=False)
        self.script('lab-netns.sh','verify')
        self.script('lab-netns.sh','down')
        self.assertEqual(json.loads(self.nsstate.read_text()),[])

    def test_mutations_require_explicit_ack(self):
        self.script('lab-service.sh','setup',ok=False,SRE_LAB_ALLOW_MUTATIONS='0')
        self.assertEqual(self.log.read_text(),'')
        self.script('lab-service.sh','cleanup',ok=False,SRE_LAB_ALLOW_MUTATIONS='0')
        self.assertEqual(self.log.read_text(),'')

    def test_service_full_transition(self):
        for act in ['setup','baseline','inject','fault','restore','verify','cleanup']:
            self.script('lab-service.sh',act)
        self.assertEqual(json.loads(self.state.read_text())['port'],'http')
        self.script('verify-recovery.sh','service')
        self.assertNotIn('delete ns',self.log.read_text())

    def test_service_guards_against_wrong_state(self):
        self.script('lab-service.sh','fault',ok=False)
        self.script('lab-service.sh','restore',ok=False)
        self.script('lab-service.sh','inject')
        self.script('lab-service.sh','inject',ok=False)
        self.script('lab-service.sh','baseline',ok=False)
        self.script('verify-recovery.sh','dns',ok=False)

    def test_service_fault_rejects_dns_errors_as_wrong_port(self):
        self.script('lab-service.sh','inject')
        self.script('lab-service.sh','fault',ok=False,MOCK_SERVICE_FAILURE_RC='6')
        self.script('lab-service.sh','fault')

    def test_evidence_id_and_stable_location(self):
        p=self.script('collect-evidence.sh','service','baseline',RUN_ID='test-01',EVIDENCE_DIR=str(self.d/'evidence'))
        self.assertIn('RUN_ID: test-01',p.stdout)
        e=self.d/'evidence'/'test-01'
        self.assertTrue((e/'environment.json').exists())
        self.assertTrue((e/'baseline'/'command-status.tsv').exists())
        self.state.write_text('{"port":"5679"}')
        self.script('collect-evidence.sh','service','fault',RUN_ID='test-01',EVIDENCE_DIR=str(self.d/'evidence'))
        self.assertTrue((e/'fault'/'services.out').exists())
        self.assertIn('PASS cluster-ip: expected failure', (e/'fault'/'lab-probe.out').read_text())
        self.assertTrue((e/'fault'/'SHA256SUMS').exists())
        self.script('collect-evidence.sh','service','recovery',ok=False,EVIDENCE_DIR=str(self.d/'evidence'))
        self.script('collect-evidence.sh','service','baseline',ok=False,RUN_ID='../escaped',EVIDENCE_DIR=str(self.d/'evidence'))

if __name__=='__main__':unittest.main()
