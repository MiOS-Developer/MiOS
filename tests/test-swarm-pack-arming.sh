#!/usr/bin/env bash
# AI-hint: Offline two-sided tests of dedicated GPU worker planning, per-instance environment, readiness, and real Quadlet Service environment propagation; no GPU or model inference.
# AI-related: usr/libexec/mios/mios-swarm-pack-firstboot, usr/share/mios/mios.toml [nodes], [containers."mios-llm-worker@"]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
fail(){ echo "[swarm-pack] FAIL: $*" >&2; exit 1; }
. "$ROOT/usr/libexec/mios/mios-swarm-pack-firstboot"
systemctl(){
  echo "$*" >> "$TMP/systemctl.log"
  [[ "$1" != is-active ]] || return "${ACTIVE_RESULT:-1}"
  [[ "$1" != start ]] || return "${START_RESULT:-0}"
}
logger(){ :; }
worker_ready(){ printf '%s\n' "$*" >> "$TMP/ready.log"; return "${READY_RESULT:-0}"; }
RUNDIR="$TMP/run"; mkdir -p "$RUNDIR"
SLOTS="$TMP/slots"
MIOS_SERVICES_LLAMACPP_UID="$(id -u)"; MIOS_SERVICES_LLAMACPP_GID="$(id -g)"
export MIOS_SERVICES_LLAMACPP_UID MIOS_SERVICES_LLAMACPP_GID
arm_worker w1 nested/m.gguf 8601 all 32768 mios-heavy 2 >/dev/null || fail "w1 did not arm"
[ -d "$SLOTS/w1/cache" ] || fail "private cache absent"
[ "$(stat -c %a "$SLOTS/w1/cache")" = 700 ] || fail "cache is not private"
grep -qx 'WORKER_MODEL=nested/m.gguf' "$RUNDIR/w1.env" || fail "wrong weight"
grep -qx 'WORKER_ALIAS=mios-heavy' "$RUNDIR/w1.env" || fail "wrong served alias"
grep -qx 'WORKER_NGL=all' "$RUNDIR/w1.env" || fail "GPU layers lost"
grep -qx 'WORKER_CTX=32768' "$RUNDIR/w1.env" || fail "context lost"
grep -qx '8601 mios-heavy 2' "$TMP/ready.log" || fail "OpenAI identity was not checked"
arm_worker w2 other.gguf 8602 12 4096 mios-second 2 >/dev/null || fail "w2 did not arm"
grep -qx 'WORKER_PORT=8602' "$RUNDIR/w2.env" || fail "second port lost"
grep -qx 'WORKER_NGL=12' "$RUNDIR/w2.env" || fail "second GPU layers lost"
grep -qx 'WORKER_CTX=4096' "$RUNDIR/w2.env" || fail "second context lost"
START_RESULT=1
if arm_worker fail-start m.gguf 8603 all 8192 mios-failed 1 > "$TMP/start-failure.log" 2>&1; then fail "bad start claimed armed"; fi
! grep -q 'Armed worker' "$TMP/start-failure.log" || fail "bad start logged armed"
START_RESULT=0; READY_RESULT=1
if arm_worker fail-ready m.gguf 8604 all 8192 mios-wrong 1 > "$TMP/ready-failure.log" 2>&1; then fail "bad identity claimed armed"; fi
! grep -q 'Armed worker' "$TMP/ready-failure.log" || fail "bad identity logged armed"
READY_RESULT=0; ACTIVE_RESULT=0
before="$(sha256sum "$RUNDIR/w1.env")"
if arm_worker w1 changed.gguf 8601 all 8192 changed 1 > "$TMP/active-failure.log" 2>&1; then fail "active configuration overwritten"; fi
[ "$before" = "$(sha256sum "$RUNDIR/w1.env")" ] || fail "active env changed"
ACTIVE_RESULT=1
mkdir "$TMP/outside"
ln -s "$TMP/outside" "$SLOTS/fail-symlink"
if arm_worker fail-symlink m.gguf 8605 all 8192 mios-slots 1 >/dev/null 2>&1; then fail "symlink slots armed"; fi
[ ! -e "$RUNDIR/fail-symlink.env" ] || fail "symlink slot got env"
[ ! -e "$TMP/outside/cache" ] || fail "symlink target changed"
: > "$TMP/notadir"; SLOTS="$TMP/notadir"
if arm_worker fail-slots m.gguf 8605 all 8192 mios-slots 1 >/dev/null 2>&1; then fail "bad slots armed"; fi
[ ! -e "$RUNDIR/fail-slots.env" ] || fail "bad slot worker got env"
! grep -q 'mios-llm-worker@fail-slots' "$TMP/systemctl.log" || fail "bad slots started"
echo '[swarm-pack] PASS: per-node environment and cache, independent instances, failed start/readiness/active-change/slots'
python3 - "$ROOT" <<'PY'
import copy, http.server, json, os, pathlib, subprocess, sys, tempfile, threading
root = pathlib.Path(sys.argv[1])
script = root/'usr/libexec/mios/mios-swarm-pack-firstboot'
env = {k:v for k,v in os.environ.items() if not k.startswith(('MIOS_', 'PYTHON', 'CONTAINER_'))}
env.update(MIOS_VENDOR_TOML=str(root/'usr/share/mios/mios.toml'),
           MIOS_VENDOR_TOML_D=str(root/'usr/lib/mios/mios.d'),
           MIOS_USER_TOML='/dev/null', MIOS_USER_TOML_D='/nonexistent/user.d',
           MIOS_HOST_TOML_D='/nonexistent/host.d', PYTHONDONTWRITEBYTECODE='1')
with tempfile.TemporaryDirectory() as directory:
    tmp = pathlib.Path(directory)
    models = tmp/'models'; (models/'nested').mkdir(parents=True)
    (models/'nested/brain.gguf').write_bytes(b'GGUFfixture')
    (models/'second.gguf').write_bytes(b'GGUFsecond')
    config = tmp/'host.toml'
    env['MIOS_HOST_TOML'] = str(config)
    node = dict(serve=True, gguf='nested/brain.gguf', endpoint='http://localhost:${MIOS_PORTS_LLM_HEAVY}/v1',
                model='mios-heavy', lane='gpu', ngl='all', ctx=32768, vram_mb=8320, blade='local')
    fixture = {'dispatch':dict(gpu_profile='swarm', vram_budget_mb=11000),
               'llamacpp':dict(models_dir=str(models), slot_dir=str(tmp/'slots')),
               'gpu.cdi.nvidia':dict(device='nvidia.com/gpu=GPU-12345678-1234-1234-1234-123456789abc'),
               'nodes.local-heavy':node}
    def render(tables):
        lines=[]
        for table, values in tables.items():
            lines.append('[nodes.'+json.dumps(table[6:])+']' if table.startswith('nodes.') else '['+table+']')
            for key, val in values.items():
                lines.append(key+' = '+json.dumps(val))
        config.write_text('\n'.join(lines)+'\n')
    def plan(tables):
        render(tables)
        return subprocess.run(['bash','-c','source "$1"; plan_workers','test',str(script)],
                              env=env, text=True, capture_output=True, timeout=10)
    result = plan(fixture)
    assert result.returncode == 0, result.stderr
    assert 'WORKER\tlocal-heavy\tnested/brain.gguf\t8520\tall\t32768\tmios-heavy\t180' in result.stdout, result.stdout
    two = copy.deepcopy(fixture)
    two['nodes.second'] = dict(node, gguf='second.gguf', endpoint='http://127.0.0.1:8530/v1',
                               model='mios-second', ctx=4096, ngl=12, vram_mb=2048)
    result = plan(two)
    assert result.returncode == 0 and 'OK\t10368\t11000\t2' in result.stdout, result.stderr
    assert 'second\tsecond.gguf\t8530\t12\t4096\tmios-second' in result.stdout
    bad_cases = [
        ('unsafe-node', 'name', '../../escape'),
        ('traversal', 'gguf', '../escape.gguf'),
        ('absolute', 'gguf', str(models/'second.gguf')),
        ('missing', 'gguf', 'missing.gguf'),
        ('alias-injection', 'model', 'mios-heavy\nOTHER=bad'),
        ('remote', 'endpoint', 'http://remote.invalid:8520/v1'),
        ('cpu-port', 'endpoint', 'http://localhost:8500/v1'),
        ('port-mismatch', 'port', 8530),
        ('zero-vram', 'vram_mb', 0),
        ('over-budget', 'vram_mb', 11001),
        ('cpu-lane', 'lane', 'cpu'),
        ('zero-offload', 'ngl', 0),
        ('invalid-context', 'ctx', -1),
    ]
    for label, key, val in bad_cases:
        bad=copy.deepcopy(fixture)
        if key == 'name': bad['nodes.'+val] = bad.pop('nodes.local-heavy')
        else: bad['nodes.local-heavy'][key] = val
        result=plan(bad)
        assert result.returncode != 0 and 'WORKER\t' not in result.stdout, (label,result.stdout,result.stderr)
    for device in ('nvidia.com/gpu=all','nvidia.com/gpu=0',''):
        bad=copy.deepcopy(fixture); bad['gpu.cdi.nvidia']['device']=device
        result=plan(bad)
        assert result.returncode != 0 and 'WORKER\t' not in result.stdout, result.stdout
    bad=copy.deepcopy(two); bad['nodes.second']['endpoint']='http://localhost:8520/v1'
    assert plan(bad).returncode != 0
    outside=tmp/'outside.gguf'; outside.write_bytes(b'GGUFoutside')
    (models/'escape.gguf').symlink_to(outside)
    bad=copy.deepcopy(fixture);bad['nodes.local-heavy']['gguf']='escape.gguf'
    assert plan(bad).returncode != 0
    print('[swarm-pack] PASS: resolved local-heavy port, two nodes per Blade, 18 invalid plan controls')

    render(fixture)
    quadlets=tmp/'quadlets'; env['MIOS_POD_OUT']=str(quadlets)
    result=subprocess.run(['/usr/bin/mios-gen','pod-quadlets','--root',str(root)],env=env,text=True,capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr
    worker=(quadlets/'mios-llm-worker@.container').read_text()
    assert 'AddDevice=nvidia.com/gpu=GPU-12345678-1234-1234-1234-123456789abc' in worker
    assert 'EnvironmentFile=-' not in worker
    assert worker.count('EnvironmentFile=/run/mios/swarm/%i.env')==2,worker
    assert '--ctx-size ${WORKER_CTX}' in worker and '--n-gpu-layers ${WORKER_NGL}' in worker
    assert '--alias ${WORKER_ALIAS}' in worker and '/%i/cache' in worker
    assert 'Volume='+str(models)+':/models:ro' in worker, worker
    assert 'ConditionPathExists='+str(models)+'/.ready' in worker, worker
    assert str(tmp/'slots')+'/%i/cache' in worker, worker
    # Feed only the worker and its real pod to the actual installed Quadlet generator.
    for entry in quadlets.iterdir():
        if entry.is_file() and entry.name not in ('mios-llm-worker@.container','mios-ai.pod'): entry.unlink()
    genenv=dict(env, QUADLET_UNIT_DIRS=str(quadlets))
    result=subprocess.run(['/usr/libexec/podman/quadlet','-dryrun'],env=genenv,text=True,capture_output=True,timeout=20)
    generated=result.stdout+result.stderr
    assert result.returncode==0,generated
    assert 'EnvironmentFile=/run/mios/swarm/%i.env' in generated,generated
    assert '--model /models/${WORKER_MODEL}' in generated,generated
    assert '--alias ${WORKER_ALIAS}' in generated and '--ctx-size ${WORKER_CTX}' in generated,generated
    assert '/usr/share/containers/systemd/-/run/' not in generated,generated
    print('[swarm-pack] PASS: native projection and actual Quadlet generated Service environment/ExecStart')

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        assert self.path=='/v1/models'
        body=json.dumps({'data':[{'id':'mios-heavy'}]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
    def ready(alias):
        return subprocess.run(['bash','-c','source "$1"; worker_ready "$2" "$3" .3','test',str(script),str(server.server_port),alias],env=env,timeout=3)
    assert ready('mios-heavy').returncode==0
    assert ready('wrong-alias').returncode!=0
finally:
    server.shutdown();server.server_close();thread.join()
print('[swarm-pack] PASS: actual OpenAI model-identity readiness accepts alias and rejects wrong alias')
PY
