#!/usr/bin/env python3
# AI-hint: Opt-in offscreen test of the actual shared Quickshell launcher, using recorded subprocesses for success, duplicate clicks, failure, timeout and terminal entries; launches no real applications and no private bus.
# AI-related: usr/share/mios/quickshell/AppLaunch.qml, tools/native/mios-gen/src/app_preflight.rs
# AI-functions: SharedLauncher
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]

@unittest.skipUnless(os.environ.get('MIOS_APP_LAUNCH_LIVE')=='1' and os.getuid()!=0,'opt in as the desktop operator')
class SharedLauncher(unittest.TestCase):
    def test_shared_launcher_has_bounded_success_and_failure_paths(self):
        with tempfile.TemporaryDirectory(prefix='mios-launch-qml-',dir='/var/tmp') as temp:
            base=Path(temp);runtime=base/'runtime';runtime.mkdir(mode=0o700);record=base/'events.jsonl'
            shutil.copyfile(ROOT/'usr/share/mios/quickshell/AppLaunch.qml',base/'AppLaunch.qml')
            (base/'DockLamp.qml').write_text('pragma Singleton\nimport Quickshell\nSingleton { function recordLaunch(entry, output, rect) {} }\n')
            def program(name,body):
                path=base/name;path.write_text('#!/usr/bin/python3\n'+body);path.chmod(0o755);return str(path)
            recorder=program('record',f"import json,os,sys\nwith open({str(record)!r},'a') as f:f.write(json.dumps({{'program':sys.argv[0].split('/')[-1],'argv':sys.argv[1:],'mios_fake_present':'MIOS_FAKE' in os.environ}})+'\\n')\n")
            for name in ('notify','gio','terminal'):shutil.copyfile(recorder,base/name);(base/name).chmod(0o755)
            with (base/'gio').open('a') as out:out.write("\nif any('dispatch-failed' in a for a in sys.argv):sys.exit(9)\n")
            policy={'overall_timeout_ms':1000,'notification_program':str(base/'notify'),'desktop_launcher_program':str(base/'gio'),'environment_program':'env','terminal_command':[str(base/'terminal'),'--title','{name}','-e']}
            reader=program('policy',f'import json\nprint(json.dumps({policy!r}))\n')
            check=program('preflight',"import json,sys,time\nname=sys.argv[-1]\nif name=='timeout':time.sleep(30)\nif name=='failed':print(json.dumps({'ok':False,'error':'Synthetic sandbox failure'}));sys.exit(1)\nprint(json.dumps({'ok':True,'desktop_file':'/synthetic/'+name+'.desktop','unset_environment':['MIOS_FAKE']}))\n")
            (base/'shell.qml').write_text('''import QtQuick
import Quickshell
ShellRoot {
    property bool begun: false
    Timer { interval: 50; repeat: true; running: true
        onTriggered: {
            if (parent.begun || !AppLaunch.cfg.desktop_launcher_program) return
            parent.begun = true
            const app = {id:"success",name:"Success",runInTerminal:false,command:["true"],workingDirectory:""}
            AppLaunch.launch(app); AppLaunch.launch(app)
            AppLaunch.launch({id:"failed",name:"Failure",runInTerminal:false})
            AppLaunch.launch({id:"dispatch-failed",name:"Dispatch failure",runInTerminal:false})
            AppLaunch.launch({id:"timeout",name:"Timeout",runInTerminal:false})
            AppLaunch.launch({id:"terminal",name:"Terminal test",runInTerminal:true,command:["synthetic-tui","--flag"],workingDirectory:""})
        }
    }
    Timer { interval: 3500; running: true
        onTriggered: { console.warn("MIOS_LAUNCH_PENDING",Object.keys(AppLaunch.jobs).length); Qt.quit() }
    }
}
''')
            env=dict(os.environ,XDG_RUNTIME_DIR=str(runtime),QT_QPA_PLATFORM='offscreen',QT_QUICK_BACKEND='software',MIOS_TOML_GET=reader,MIOS_APP_PREFLIGHT=check,MIOS_FAKE='not-for-apps')
            result=subprocess.run(['quickshell','--no-color','--path',str(base/'shell.qml')],env=env,capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertIn('MIOS_LAUNCH_PENDING 0',result.stdout+result.stderr)
            self.assertTrue(record.exists(),result.stdout+result.stderr)
            events=[json.loads(line) for line in record.read_text().splitlines()]
            normal=[e for e in events if e['program']=='gio'];term=[e for e in events if e['program']=='terminal'];errors=[e for e in events if e['program']=='notify']
            self.assertEqual(len(normal),2,events);self.assertFalse(normal[0]['mios_fake_present'])
            self.assertEqual(len(term),1,events);self.assertEqual(term[0]['argv'],['--title','Terminal test','-e','synthetic-tui','--flag'])
            self.assertEqual(len(errors),3,events)
            self.assertTrue(any('timed out' in ' '.join(e['argv']) for e in errors))

if __name__=='__main__':unittest.main()
