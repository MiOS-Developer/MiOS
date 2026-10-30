#!/usr/bin/env python3
# AI-hint: Operator opt-in synthetic RDPECAM DVC wire tests through the compiled Rust test binary and actual native PipeWire consumers. Verify raw, H264, MJPEG, consent denial, pause/resume and cleanup without recording a physical camera.
# AI-related: src/mios-rdp-server/crates/mios-rdp-server/src/remote_camera.rs, usr/share/mios/mios.toml
# AI-functions: main

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

def main():
    parser=argparse.ArgumentParser(description='Synthetic RDPECAM protocol/native video integration; no physical camera capture')
    parser.add_argument('--test-binary',required=True,help='compiled Rust unit-test binary from the isolated builder')
    parser.add_argument('--report',required=True)
    args=parser.parse_args()
    assert os.getuid()!=0,'run as the desktop operator'
    assert os.environ.get('MIOS_RDP_CAMERA_LIVE')=='1','explicit opt-in is required'
    binary=Path(args.test_binary).resolve()
    assert binary.is_file() and os.access(binary,os.X_OK)
    assert binary!=Path('/usr/libexec/mios/rdp-native/mios-rdp-server').resolve()
    test='remote_camera::tests::camera_drdynvc_to_native_video_consumer'
    listing=subprocess.check_output([str(binary),'--list'],text=True,timeout=10)
    assert test+': test' in listing,'the selected binary does not contain the native camera fixture'
    report=Path(args.report);assert not report.exists(),'preserve an existing report'
    work=Path(tempfile.mkdtemp(prefix='mios-camera-native-',dir='/var/tmp'))
    results=[]
    for case_name,format_name,cleanup,portrait,transposed in (('i420','i420','0','0','0'),('h264','h264','0','0','0'),('mjpg','mjpg','1','0','0'),('enumeration-close','i420','enum','0','0'),('h264-portrait','h264','0','1','0'),('h264-transposed','h264','0','1','1'),('h264-scaled','h264','0','1','0')):
        env=dict(os.environ,MIOS_RDP_CAMERA_TEST_FORMAT=format_name,MIOS_RDP_CAMERA_TEST_REMOVE=cleanup,MIOS_RDP_CAMERA_TEST_PORTRAIT=portrait,MIOS_RDP_CAMERA_TEST_TRANSPOSED=transposed,MIOS_RDP_CAMERA_TEST_LARGE_CODED='1' if case_name=='h264-scaled' else '0')
        run=subprocess.run([str(binary),'--ignored','--exact',test,'--nocapture','--test-threads=1'],env=env,capture_output=True,text=True,timeout=45)
        (work/(case_name+'.log')).write_text(run.stdout+run.stderr)
        assert run.returncode==0,run.stdout+run.stderr
        assert '1 passed; 0 failed' in run.stdout,'a zero-test invocation is not verification'
        proof=[line.split('MIOS_CAMERA_NATIVE_PROOF ',1)[1] for line in run.stdout.splitlines() if 'MIOS_CAMERA_NATIVE_PROOF ' in line]
        assert len(proof)==1
        entry=json.loads(proof[0]);entry.update(format=format_name,case=case_name)
        assert entry['frames']==12 and entry['bytes']==1382400
        assert entry['permission_controls'] and entry['consumer_idle_deactivates'] and entry['consumer_reopen_resumes'] and entry['disconnect_cleanup']
        results.append(entry)
        print(json.dumps({'case':case_name,'format':format_name,'passed':True,'native_frames':entry['frames']}),flush=True)
    result={'passed':True,'protocol':'MS-RDPECAM over actual encoded DRDYNVC messages','transport':'in-process protocol loop, not TCP/TLS or a physical mobile client','physical_camera_recorded':False,'workdir':str(work),'cases':results}
    with report.open('x') as handle:json.dump(result,handle,indent=2);handle.write('\n')
    print(json.dumps(result),flush=True)

if __name__=='__main__':
    main()
