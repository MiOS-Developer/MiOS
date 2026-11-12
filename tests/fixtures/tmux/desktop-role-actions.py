#!/usr/bin/env python3
# AI-hint: Check real C95 desktop roles, ordinary floating applications and managed-agent guards on disposable servers.
# AI-related: tests/test-tmux-os.py; tools/native/mios-gen/src/tmux_desktop.rs; tools/native/mios-unit-gen/src/tmux_os.rs
"""Actual C95 private fixture: generated policy, four core roles, app actions.

No native user sessions, PAM, inputd, hardware, network, or credentials are used.
"""
from pathlib import Path
import argparse,hashlib,importlib.util,json,os,re,subprocess,time

def need(ok,message):
 if not ok:raise RuntimeError(message)
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--mode',choices=['baseline','candidate'],required=True);a=parser.parse_args();root=Path(a.source).resolve(strict=True)
 module_spec=importlib.util.spec_from_file_location('owned_tmux_test_harness',root/'tests/test-tmux-os.py');m=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(m)
 need(m.GEN.is_file()and digest(m.GEN)==os.environ['MIOS_TMUX_OS_GEN_SHA256'],'exact generator artifact required');need(m.DESKTOP_TMUX is not None and digest(m.DESKTOP_TMUX)==m.DESKTOP_TMUX_SHA256,'exact C95 artifact required')
 h=m.Harness('role-guard-'+a.mode,global_popups=True);checks=[]
 try:
  h.start(157,41);client=h.view(w=157,h=41);canvas=h.I('display-message','-p','-t','main','#{pane_id}');h.I('set-option','-w','-t',canvas,'@mios-desktop-enabled','1');origin='owned-private-desktop-role-fixture';h.I('set-option','-g','@mios-desktop-origin',origin)
  # These are the real layout-owned role options produced by the desktop
  # opener. Their commands are private idle fixtures, not live telemetry/agents.
  roles={'canvas':canvas}
  for name in['panel','chat','metrics']:
   roles[name]=h.I('split-window','-d','-t',canvas,'-P','-F','#{pane_id}','--','sleep','300')
  h.I('select-layout','-t',canvas,'tiled')
  for name,pane in roles.items():
   h.I('set-option','-p','-t',pane,'@mios-desktop-'+name,'1')
   if name!='canvas':h.I('set-option','-p','-t',pane,'@mios-desktop-'+('panel-origin'if name=='panel'else'role-origin'),origin)
   if name in['chat','metrics']:h.I('set-option','-p','-t',pane,'@mios-desktop-role',name)
  h.I('select-pane','-t',canvas);socket=h.I('display-message','-p','#{socket_path}')
  expected_line=next(line for line in(root/'usr/share/mios/tmux/mios-keys.tmux.conf').read_text().splitlines()if line.startswith('set -g @mios-managed-pane '));expected_expression=expected_line.split(' ',3)[3].strip("'")
  expression=h.I('show-options','-gqv','@mios-managed-pane');need(expression==expected_expression,'generated managed expression differs from canonical projection')
  need(all(h.I('display-message','-p','-t',pane,'#{E:@mios-managed-pane}')=='1'for pane in roles.values()),'all four generated core roles must be managed')
  def count():return int(h.I('display-message','-p','-t',canvas,'#{window_panes}'))
  def identity(pane):return h.I('display-message','-p','-t',pane,'#{pane_id}|#{pane_pid}|#{pane_tty}|#{session_id}|#{window_id}')
  def rect(pane):return h.I('display-message','-p','-t',pane,'#{pane_left}|#{pane_top}|#{pane_width}|#{pane_height}|#{pane_floating_flag}')
  def core_snapshot():return{name:{'identity':identity(pane),'rect':rect(pane)}for name,pane in roles.items()}
  initial=core_snapshot();need(count()==4,'exact four-role starting viewport required')
  def action(verb,pane):
   env=dict(h.env);env['PATH']=str(m.GEN.parent)+':'+env['PATH']
   return subprocess.run([str(m.GEN),'tmux-os','--root',str(root),'desktop',verb,'--socket',socket,'--client',client,'--pane',pane],env=env,stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=15)
  launched=action('new',canvas)
  if a.mode=='baseline':
   need(launched.returncode!=0 and'managed agents and the desktop canvas cannot be rearranged'in launched.stderr,'baseline must reproduce exact canonical managed-role refusal');need(count()==4 and core_snapshot()==initial,'baseline refusal changed private desktop')
   print(json.dumps({'ok':True,'mode':a.mode,'generator_sha256':digest(m.GEN),'generator_resolved_path':str(m.GEN.resolve()),'c95_sha256':digest(m.DESKTOP_TMUX),'four_core_roles':True,'generated_managed_expression_sha256':hashlib.sha256(expression.encode()).hexdigest(),'known_canonical_new_refusal_reproduced':True,'desktop_unchanged':True,'PAM_acceptance_claimed':False,'hardware_acceptance_claimed':False}));return
  need(launched.returncode==0,'candidate canonical new failed')
  def app_focus():
   pane=h.I('display-message','-p','-c',client,'#{client_pane_id}')
   return pane if re.fullmatch('%[0-9]+',pane)and pane not in roles.values()else None
  app=m.wait(app_focus);need(app is not None and count()==5,'one real canonical new app required');app_identity=identity(app);need(h.I('display-message','-p','-t',app,'#{pane_floating_flag}')=='1','canonical new must be a native float');need(core_snapshot()==initial,'new app changed protected desktop layout')
  centered=action('center',app);need(centered.returncode==0 and identity(app)==app_identity and core_snapshot()==initial and count()==5,'candidate center changed app identity or core layout');checks+=['canonical-new-from-canvas','center-ordinary-app-with-four-core-roles']
  for name,pane in roles.items():
   for verb in['close-confirm','center','snap-left']:
    before=core_snapshot();r=action(verb,pane);need(r.returncode!=0 and'desktop roles cannot be rearranged'in r.stderr and core_snapshot()==before and identity(app)==app_identity,'core role protection failed');checks.append(name+'-rejects-'+verb)
  # Native ordinary inventory may omit floats. A directly witnessed app target
  # is safe; an additional unknown float, even one advertising an agent role,
  # must make action refusal deterministic before any mutation.
  unknown=h.I('new-pane','-d','-t',canvas,'-P','-F','#{pane_id}','--','sleep','300');h.I('set-option','-p','-t',unknown,'@mios-workspace-worker','1');need(h.I('display-message','-p','-t',unknown,'#{pane_floating_flag}')=='1','native unknown float required')
  ordinary=h.I('list-panes','-a','-F','#{pane_id}').splitlines();need(unknown not in ordinary and app not in ordinary,'fixture must exercise actual omitted float inventory');unknown_identity=identity(unknown);before=core_snapshot();app_rect=rect(app)
  for verb,target in[('center',app),('new',canvas)]:
   r=action(verb,target);need(r.returncode!=0 and'unobserved panes'in r.stderr and count()==6 and core_snapshot()==before and rect(app)==app_rect and identity(unknown)==unknown_identity,'unknown native float bypassed window guard')
  checks.append('omitted-native-agent-float-fails-closed');h.I('kill-pane','-t',unknown);need(action('center',app).returncode==0 and identity(app)==app_identity,'known explicit float target not restored after unknown removed')
  # Drop only the owned app so the following tests isolate explicit real-agent
  # guards from omitted-float count failures. Forged desktop flags never win.
  h.I('kill-pane','-t',app);need(count()==4,'owned app cleanup failed');ordinary_app=h.I('split-window','-d','-t',canvas,'-P','-F','#{pane_id}','--','sleep','300');agent=h.I('split-window','-d','-t',canvas,'-P','-F','#{pane_id}','--','sleep','300')
  for flag in['@mios-workspace-head','@mios-mcp-owner','@mios-workspace-worker']:
   h.I('set-option','-p','-t',agent,flag,'1');h.I('set-option','-p','-t',agent,'@mios-desktop-canvas','1');before={pane:{'identity':identity(pane),'rect':rect(pane)}for pane in[ordinary_app,agent,*roles.values()]};n=count()
   for verb,target in[('new',canvas),('center',ordinary_app),('close-confirm',agent),('center',agent)]:
    r=action(verb,target);need(r.returncode!=0 and'managed'in r.stderr and count()==n and before=={pane:{'identity':identity(pane),'rect':rect(pane)}for pane in before},'real agent or forged role bypassed protection')
   checks.append(flag+'-wins-over-forged-desktop-role');h.I('set-option','-p','-u','-t',agent,flag);h.I('set-option','-p','-u','-t',agent,'@mios-desktop-canvas')
  # A pane-local manual managed expression is a negative control, never an
  # override of the generated global aggregate or a weakened positive fixture.
  h.I('set-option','-p','-t',agent,'@mios-managed-pane','1');h.I('set-option','-p','-t',agent,'@mios-desktop-panel','1');before={pane:{'identity':identity(pane),'rect':rect(pane)}for pane in[ordinary_app,agent,*roles.values()]};n=count();need(action('center',agent).returncode!=0 and count()==n and before=={pane:{'identity':identity(pane),'rect':rect(pane)}for pane in before},'manual managed marker lost to forged role or changed desktop');checks.append('manual-managed-marker-remains-protected')
  need(h.I('show-options','-gqv','@mios-managed-pane')==expression,'fixture changed generated global managed policy')
  print(json.dumps({'ok':True,'mode':a.mode,'generator_sha256':digest(m.GEN),'generator_resolved_path':str(m.GEN.resolve()),'c95_sha256':digest(m.DESKTOP_TMUX),'generated_managed_expression_sha256':hashlib.sha256(expression.encode()).hexdigest(),'four_core_roles':True,'checks':checks,'app_identity_preserved':True,'native_omitted_float_observed':True,'PAM_acceptance_claimed':False,'hardware_acceptance_claimed':False},sort_keys=True))
 finally:h.close()
if __name__=='__main__':main()
